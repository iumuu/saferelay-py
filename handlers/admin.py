"""管理员命令处理 — /menu, /ban, /unban, /trust, /untrust 等。"""

from typing import Any, List, Optional

from core.bot import ParseMode, Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove, filters
from core.database import Database
from core.logger import get_logger
from services.forward import ForwardService
from services.menu import build_main_menu
from services.security import SecurityService
from services.stats import StatsService

logger = get_logger("handlers.admin")

CONFIG_WELCOME_MSG = "welcome_msg"
CONFIG_AUTO_REPLY_MSG = "auto_reply_msg"


def register(
    bot: Bot,
    db: Database,
    forward_svc: ForwardService,
    security_svc: SecurityService,
    stats_svc: StatsService,
    admin_ids: List[int],
) -> None:
    """注册管理员命令处理器。"""

    ### ---- /menu ---- ###

    @bot.on_message(filters.command("menu") & filters.user(admin_ids))
    async def on_menu(client: Any, message: Message) -> None:
        """显示管理面板。"""
        logger.info("admin_menu", {"admin_id": message.from_user.id if message.from_user else 0})
        # 先移除私聊中可能残留的人机验证 ReplyKeyboard。
        if message.chat and str(getattr(message.chat, "type", "")).lower().endswith("private"):
            cleanup = await message.reply_text("正在打开管理面板…", reply_markup=ReplyKeyboardRemove())
            try:
                await cleanup.delete()
            except Exception:
                pass
        text, keyboard = await build_main_menu(db, forward_svc, security_svc)
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    ### ---- /help (admin) ---- ###

    @bot.on_message(filters.command("help") & filters.user(admin_ids))
    async def on_admin_help(client: Any, message: Message) -> None:
        """显示管理员帮助。"""
        await message.reply_text(
            "🤖 <b>SafeRelay 管理指令</b>\n\n"
            "<b>常用指令：</b>\n"
            "/menu - 打开图形菜单\n"
            "/help - 显示帮助\n"
            "/broadcast - 广播消息\n\n"
            "<b>用户管理（回复消息或指定ID）：</b>\n"
            "/ban - 封禁用户\n"
            "/unban - 解封用户\n"
            "/reset - 重置验证\n"
            "/trust - 信任用户(白名单)\n"
            "/untrust - 取消信任\n\n"
            "<b>消息设置：</b>\n"
            "/del /delete - 撤回你发给用户的消息（在话题里回复自己发出的那条消息后发送）\n\n"
            "<b>系统：</b>\n"
            "/cleanup - 清理失效话题\n"
            "/cachestats - 查看缓存统计\n"
            "/clearcache - 清空缓存\n\n"
            "<b>快捷操作：</b> 回复用户消息即可转发",
            parse_mode=ParseMode.HTML,
        )

    ### ---- /ban ---- ###

    @bot.on_message(filters.command("ban") & filters.user(admin_ids))
    async def on_ban(client: Any, message: Message) -> None:
        """封禁用户。"""
        logger.info("admin_ban", {"admin_id": message.from_user.id if message.from_user else 0})
        target_id = await _get_target_id(message, db, forward_svc)
        if not target_id:
            await message.reply_text("⚠️ 格式错误。请回复用户消息发送 /ban，或发送 /ban 123456")
            return
        await security_svc.ban_user(target_id)
        await db.remove_verified(target_id)
        await message.reply_text(f"🚫 用户 <code>{target_id}</code> 已被封禁。", parse_mode=ParseMode.HTML)

    ### ---- /unban ---- ###

    @bot.on_message(filters.command("unban") & filters.user(admin_ids))
    async def on_unban(client: Any, message: Message) -> None:
        """解封用户。"""
        logger.info("admin_unban", {"admin_id": message.from_user.id if message.from_user else 0})
        target_id = await _get_target_id(message, db, forward_svc)
        if not target_id:
            await message.reply_text("⚠️ 格式错误。请回复用户消息发送 /unban，或发送 /unban 123456")
            return
        await security_svc.unban_user(target_id)
        await message.reply_text(f"✅ 用户 <code>{target_id}</code> 已解封。", parse_mode=ParseMode.HTML)

    ### ---- /trust ---- ###

    @bot.on_message(filters.command("trust") & filters.user(admin_ids))
    async def on_trust(client: Any, message: Message) -> None:
        """信任用户（白名单）。"""
        logger.info("admin_trust", {"admin_id": message.from_user.id if message.from_user else 0})
        target_id = await _get_target_id(message, db, forward_svc)
        if not target_id:
            await message.reply_text("📋 请回复用户消息或发送 /trust 123456 来信任用户")
            return
        await security_svc.add_whitelist(target_id)
        await message.reply_text(f"✅ 已信任用户 <code>{target_id}</code>", parse_mode=ParseMode.HTML)

    ### ---- /untrust ---- ###

    @bot.on_message(filters.command("untrust") & filters.user(admin_ids))
    async def on_untrust(client: Any, message: Message) -> None:
        """取消信任。"""
        logger.info("admin_untrust", {"admin_id": message.from_user.id if message.from_user else 0})
        target_id = await _get_target_id(message, db, forward_svc)
        if not target_id:
            await message.reply_text("📋 请回复用户消息或发送 /untrust 123456 来取消信任")
            return
        await security_svc.remove_whitelist(target_id)
        await message.reply_text(f"✅ 已取消信任用户 <code>{target_id}</code>", parse_mode=ParseMode.HTML)

    ### ---- /reset ---- ###

    @bot.on_message(filters.command("reset") & filters.user(admin_ids))
    async def on_reset(client: Any, message: Message) -> None:
        """重置用户验证状态。"""
        logger.info("admin_reset", {"admin_id": message.from_user.id if message.from_user else 0})
        target_id = await _get_target_id(message, db, forward_svc)
        if not target_id:
            await message.reply_text("⚠️ 格式错误。请回复用户消息发送 /reset，或发送 /reset 123456")
            return

        # 检查白名单
        if await security_svc.is_whitelisted(target_id):
            await message.reply_text(
                f"⚠️ 用户 <code>{target_id}</code> 在白名单中，无需验证即可发送消息。",
                parse_mode=ParseMode.HTML,
            )
            return

        await db.remove_verified(target_id)
        await message.reply_text(f"🔄 用户 <code>{target_id}</code> 验证状态已取消。", parse_mode=ParseMode.HTML)

    ### ---- /welcome ---- ###

    @bot.on_message(filters.command("welcome") & filters.user(admin_ids))
    async def on_welcome(client: Any, message: Message) -> None:
        """设置欢迎消息。"""
        logger.info("admin_welcome", {"admin_id": message.from_user.id if message.from_user else 0})
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip() == "delete":
            await db.delete_config(CONFIG_WELCOME_MSG)
            await message.reply_text("✅ 欢迎消息已删除（恢复默认）。")
            return
        await db.set_config(CONFIG_WELCOME_MSG, parts[1].strip())
        await message.reply_text("✅ 欢迎消息已设置。")

    ### ---- /autoreply ---- ###

    @bot.on_message(filters.command("autoreply") & filters.user(admin_ids))
    async def on_autoreply(client: Any, message: Message) -> None:
        """设置自动回复。"""
        logger.info("admin_autoreply", {"admin_id": message.from_user.id if message.from_user else 0})
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip() == "off":
            await db.delete_config(CONFIG_AUTO_REPLY_MSG)
            await message.reply_text("✅ 自动回复已关闭。")
            return
        await db.set_config(CONFIG_AUTO_REPLY_MSG, parts[1].strip())
        await message.reply_text("✅ 自动回复已设置。")

    ### ---- /broadcast ---- ###

    @bot.on_message(filters.command("broadcast") & filters.user(admin_ids))
    async def on_broadcast(client: Any, message: Message) -> None:
        """广播消息。"""
        logger.info("admin_broadcast", {"admin_id": message.from_user.id if message.from_user else 0})
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text("⚠️ 用法：/broadcast 消息内容\n\n支持 HTML 格式", parse_mode=ParseMode.HTML)
            return

        msg_text = parts[1].strip()
        users = await db.get_verified_users()
        total = len(users)
        sent = 0
        failed = 0

        status = await message.reply_text(f"📤 开始广播，共 {total} 位用户...")

        for user in users:
            try:
                await bot.send_message(user["user_id"], msg_text, parse_mode=ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1

            if (sent + failed) % 10 == 0:
                await status.edit_text(f"📤 广播中... {sent + failed}/{total}")

        await status.edit_text(
            f"✅ 广播完成\n\n已发送：{sent}/{total}\n失败：{failed}",
            parse_mode=ParseMode.HTML,
        )

    ### ---- /cleanup ---- ###

    @bot.on_message(filters.command("cleanup") & filters.user(admin_ids))
    async def on_cleanup(client: Any, message: Message) -> None:
        """清理失效话题。"""
        logger.info("admin_cleanup", {"admin_id": message.from_user.id if message.from_user else 0})
        if not forward_svc.group_id:
            await message.reply_text("⚠️ 未配置 GROUP_ID，话题清理功能不可用。")
            return

        await message.reply_text("🧹 开始清理失效话题...（此功能需要论坛群组权限）")

    ### ---- /cachestats ---- ###

    @bot.on_message(filters.command("cachestats") & filters.user(admin_ids))
    async def on_cachestats(client: Any, message: Message) -> None:
        """查看缓存统计。"""
        logger.info("admin_cachestats", {"admin_id": message.from_user.id if message.from_user else 0})
        await message.reply_text("📊 缓存统计信息\n\n<i>当前版本使用 SQLite，缓存由数据库管理。</i>", parse_mode=ParseMode.HTML)

    ### ---- /clearcache ---- ###

    @bot.on_message(filters.command("clearcache") & filters.user(admin_ids))
    async def on_clearcache(client: Any, message: Message) -> None:
        """清空缓存。"""
        logger.info("admin_clearcache", {"admin_id": message.from_user.id if message.from_user else 0})
        await message.reply_text("✅ 缓存已清空。")

    ### ---- 普通回复（管理员回复用户消息） ---- ###

    @bot.on_message(filters.user(admin_ids) & filters.chat(forward_svc.group_id))
    async def on_admin_message(client: Any, message: Message) -> None:
        """处理管理员在群组话题中的回复消息。"""
        logger.info("admin_message", {
            "chat_id": message.chat.id if message.chat else None,
            "thread_id": message.message_thread_id,
            "has_reply": message.reply_to_message is not None,
        })
        text = (message.text or "").strip()
        command = text.split()[0].split("@", 1)[0].lower() if text.startswith("/") else ""

        # 管理员撤回自己发给用户的消息：回复自己之前发出的那条消息发送 /del 或 /delete
        if command in ("/del", "/delete"):
            ok = await forward_svc.delete_by_admin_reply(message)
            await message.reply_text("✅ 已删除用户侧对应消息" if ok else "⚠️ 未找到对应消息映射：请回复你自己之前发给用户的那条消息。")
            return

        # 避免未知 /命令 被当作管理员回复转发给用户
        if command:
            await message.reply_text("⚠️ 未识别的管理命令，请检查 /help 或 /menu。")
            return

        await forward_svc.handle_admin_reply(message)

    @bot.on_edited_message(filters.user(admin_ids))
    async def on_admin_edit(client: Any, message: Message) -> None:
        """同步管理员编辑消息到用户。"""
        logger.info("admin_edit", {
            "chat_id": message.chat.id if message.chat else None,
            "thread_id": message.message_thread_id,
        })
        await forward_svc.sync_admin_edit(message)

    logger.info("admin_handlers_registered")


async def _get_target_id(
    message: Message, db: Database, forward_svc: ForwardService
) -> Optional[int]:
    """辅助函数：从回复或参数中获取目标用户 ID。"""
    text = message.text or ""
    parts = text.split()
    reply = message.reply_to_message

    # 话题模式：从话题 ID 获取用户
    if forward_svc.group_id and message.chat and str(message.chat.id) == str(forward_svc.group_id) and message.message_thread_id:
        uid = await db.get_user_by_thread(message.message_thread_id)
        if uid:
            return uid

    # 从参数提取
    if len(parts) > 1:
        try:
            return int(parts[1])
        except ValueError:
            pass

    return None
