"""用户消息处理 — 访客私聊消息、/start、验证触发。"""

from typing import Any

from core.bot import ParseMode, Bot, Message, ReplyKeyboardRemove, filters
from core.database import Database
from core.logger import get_logger
from services.forward import ForwardService
from services.security import SecurityService
from services.stats import StatsService
from services.verify import VerifyService

logger = get_logger("handlers.user")

# 配置键
CONFIG_WELCOME_MSG = "welcome_msg"
CONFIG_AUTO_REPLY_MSG = "auto_reply_msg"


def register(
    bot: Bot,
    db: Database,
    forward_svc: ForwardService,
    verify_svc: VerifyService,
    security_svc: SecurityService,
    stats_svc: StatsService,
) -> None:
    """注册用户消息处理器。"""

    async def _notify_verify_success(user_id: int, display_name: str, message: Message) -> None:
        pending_result = await forward_svc.process_pending(user_id)
        success_text = "✅ 验证成功！您现在可以发送消息给管理员了。"
        if pending_result["forwarded"] > 0:
            success_text = f"✅ 验证成功！\n\n📨 刚才的 {pending_result['forwarded']} 条消息已送达管理员。"
        await bot.send_message(user_id, success_text, reply_markup=ReplyKeyboardRemove())
        await stats_svc.record_active_user(user_id)

        if forward_svc.admin_uid and message.from_user:
            username = message.from_user.username
            name = (
                f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}"
            ).strip()
            username_line = f"\n📎 @{username}" if username else ""
            await bot.send_message(
                forward_svc.admin_uid,
                f"✅ <b>新用户验证通过</b>\n\n🆔 <code>{user_id}</code> ({name or display_name}){username_line}",
                parse_mode=ParseMode.HTML,
            )

    async def _send_verification_prompt(user_id: int, welcome: str = "") -> None:
        if verify_svc.is_hcaptcha_enabled():
            text = (
                f"{welcome}\n\n🛡 请完成人机验证以继续对话。"
                if welcome
                else "🛡 为了防止垃圾消息，请先完成人机验证。"
            )
            await bot.send_message(
                user_id,
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=verify_svc.generate_hcaptcha_keyboard(user_id),
            )
            return

        challenge_id, question = verify_svc.create_challenge(user_id)
        text = (
            f"{welcome}\n\n🛡 请回答以下问题以继续对话：\n\n<b>{question['q']}</b>"
            if welcome
            else f"🛡 为了防止垃圾消息，请回答以下问题：\n\n<b>{question['q']}</b>"
        )
        await bot.send_message(
            user_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=verify_svc.generate_keyboard(question),
        )

    @bot.on_message(filters.private & ~filters.command(["start", "help", "menu"]))
    async def on_guest_message(client: Any, message: Message) -> None:
        """处理用户私聊消息（非命令）。"""
        user_id = message.from_user.id if message.from_user else message.chat.id
        text_preview = (message.text or message.caption or "")[:80]
        logger.info("guest_message", {"user_id": user_id, "text_preview": text_preview})

        # ⛔ 管理员消息跳过（管理员走 admin handler）
        if user_id in forward_svc.admin_ids:
            return

        # 用户删除转发消息：回复自己已发送的消息发送 /del
        if (message.text or "").strip().split()[0:1] == ["/del"]:
            reply = message.reply_to_message
            if not reply:
                await message.reply_text("请回复要撤回的那条消息发送 /del")
                return
            ok = await forward_svc.delete_by_guest_message(user_id, reply.id)
            await message.reply_text("✅ 已删除管理员侧对应消息" if ok else "⚠️ 未找到对应消息，可能尚未转发或映射已清理。")
            return

        # 白名单用户直接转发
        if await security_svc.is_whitelisted(user_id):
            logger.info("whitelist_hit", {"user_id": user_id})
            await forward_svc.forward_guest_message(message)
            return

        # 检查封禁
        if await security_svc.is_banned(user_id):
            logger.warn("banned_blocked", {"user_id": user_id})
            await message.reply_text("🚫 您已被管理员拉黑，无法发送消息。")
            return

        # 检查欺诈
        is_fraud = await security_svc.check_fraud(user_id)
        if is_fraud:
            logger.warn("fraud_detected", {"user_id": user_id})
            if forward_svc.admin_uid:
                await bot.send_message(
                    forward_svc.admin_uid,
                    f"🚨 <b>检测到欺诈用户</b>\n\nUID: <code>{user_id}</code>\n该用户出现在欺诈数据库中，已自动拦截。",
                    parse_mode=ParseMode.HTML,
                )
            await message.reply_text(
                "🚫 <b>服务不可用</b>\n\n您的账号存在异常，无法使用本服务。",
                parse_mode=ParseMode.HTML,
            )
            return

        web_app_data = getattr(message, "web_app_data", None)
        if web_app_data and verify_svc.is_hcaptcha_enabled():
            logger.info("hcaptcha_webapp_data_received", {"user_id": user_id})
            token = verify_svc.extract_hcaptcha_token(getattr(web_app_data, "data", ""))
            result = await verify_svc.verify_hcaptcha_token(token)
            if result["success"]:
                display_name = message.from_user.first_name or message.from_user.username or "Unknown"
                await db.mark_verified(user_id, display_name)
                await _notify_verify_success(user_id, display_name, message)
            else:
                await bot.send_message(user_id, result["message"])
            return

        # 已验证用户
        if await db.is_verified(user_id):
            # 垃圾过滤
            spam_check = await security_svc.check_spam(message)
            if spam_check["is_spam"]:
                if forward_svc.admin_uid:
                    await bot.send_message(
                        forward_svc.admin_uid,
                        f"🗑 <b>垃圾消息拦截</b>\n\nUID: <code>{user_id}</code>\n原因: {spam_check['reason']}\n\n<i>消息已拦截，未转发给管理员</i>",
                        parse_mode=ParseMode.HTML,
                    )
                await message.reply_text("🚫 您的消息因违反规则被拦截。如有疑问请联系管理员。")
                return

            # 速率限制
            allowed = await db.check_rate_limit(f"msg:{user_id}", 5000, 5)
            if not allowed:
                await message.reply_text("⚠️ 发送过于频繁，请稍后再试。")
                return

            # 自动回复（每小时一次）
            auto_reply = await db.get_config(CONFIG_AUTO_REPLY_MSG, "")
            if auto_reply:
                autoreply_key = f"autoreply_sent:{user_id}"
                autoreply_sent = await db.get_config(autoreply_key, "")
                if not autoreply_sent:
                    await message.reply_text(auto_reply)
                    await db.set_config(autoreply_key, "1")

            logger.info("verified_forward", {"user_id": user_id})
            await forward_svc.forward_guest_message(message)
            return

        # 未验证：暂存消息并提示验证
        queue_len = await forward_svc.append_pending(user_id, message.id)
        if queue_len >= 10:
            await message.reply_text("📝 消息已暂存，完成验证后会自动发送（最多暂存10条）")

        # 触发验证
        limit_ok = await verify_svc.check_trigger_limit(user_id)
        if limit_ok:
            welcome = await db.get_config(CONFIG_WELCOME_MSG, "")
            await _send_verification_prompt(user_id, welcome)
        else:
            await message.reply_text("⏳ 验证尝试过于频繁，请5分钟后再试。")

    @bot.on_message(filters.private & filters.command("start"))
    async def on_start(client: Any, message: Message) -> None:
        """处理 /start 命令。"""
        user_id = message.from_user.id if message.from_user else message.chat.id
        logger.info("start_command", {"user_id": user_id})

        # 白名单用户
        if await security_svc.is_whitelisted(user_id):
            await message.reply_text("👋 欢迎使用 SafeRelay！\n\n您已在白名单中，可以直接发送消息给管理员。")
            return

        # 已验证用户
        if await db.is_verified(user_id):
            auto_reply = await db.get_config(CONFIG_AUTO_REPLY_MSG, "")
            text = auto_reply or "👋 欢迎使用 SafeRelay！\n\n您已通过验证，可以直接发送消息给管理员。"
            await message.reply_text(text)
            return

        # 未验证：发送验证
        limit_ok = await verify_svc.check_trigger_limit(user_id)
        if limit_ok:
            welcome = await db.get_config(CONFIG_WELCOME_MSG, "")
            await _send_verification_prompt(user_id, welcome)
        else:
            await message.reply_text("⏳ 验证尝试过于频繁，请5分钟后再试。")

    @bot.on_message(filters.private & filters.command("help"))
    async def on_help(client: Any, message: Message) -> None:
        """处理 /help 命令。"""
        await message.reply_text(
            "🤖 <b>SafeRelay 使用说明</b>\n\n"
            "• 发送消息给机器人，消息将转发给管理员\n"
            "• 如未验证，会先进行简单问答验证\n"
            "• 管理员回复的消息会转发给您\n\n"
            "<i>如有问题请联系管理员。</i>",
            parse_mode=ParseMode.HTML,
        )

    @bot.on_edited_message(filters.private)
    async def on_guest_edit(client: Any, message: Message) -> None:
        """同步用户编辑消息到管理员。"""
        user_id = message.from_user.id if message.from_user else message.chat.id
        logger.info("guest_edit", {"user_id": user_id})
        if user_id in forward_svc.admin_ids:
            return
        await forward_svc.sync_guest_edit(message)

    logger.info("user_handlers_registered")
