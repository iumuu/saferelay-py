"""回调查询处理 — 验证答案、管理菜单导航。"""

import asyncio
from typing import Any

from core.bot import Bot, ParseMode, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, filters
from core.database import Database
from core.logger import get_logger
from services.forward import ForwardService
from services.menu import (
    build_main_menu, build_spam_menu,
    build_welcome_menu, build_autoreply_menu,
    build_users_menu, build_stats_menu,
    build_protect_menu,
)
from services.security import SecurityService
from services.stats import StatsService
from services.verify import VerifyService

logger = get_logger("handlers.callback")

CONFIG_WELCOME_MSG = "welcome_msg"


def register(
    bot: Bot,
    db: Database,
    forward_svc: ForwardService,
    verify_svc: VerifyService,
    security_svc: SecurityService,
    stats_svc: StatsService,
) -> None:
    """注册回调查询处理器。"""

    ### ---- 验证答案回调 ---- ###

    @bot.on_callback_query(filters.regex(r"^quiz_answer:"))
    async def on_quiz_answer(client: Any, callback: CallbackQuery) -> None:
        """处理验证答案提交。"""
        user_id = callback.from_user.id
        data = callback.data

        try:
            answer_index = int(data.split(":")[1])
        except (IndexError, ValueError):
            await callback.answer("❌ 无效的选项", show_alert=True)
            return

        logger.info("quiz_answer", {"user_id": user_id, "answer_index": answer_index})

        locked = await db.acquire_verify_lock(user_id, 60)
        if not locked:
            await callback.answer("⏳ 验证进行中，请稍后再试", show_alert=True)
            return

        try:
            result = verify_svc.verify_answer(user_id, answer_index)

            if result["success"]:
                logger.info("verify_success", {"user_id": user_id})
                display_name = callback.from_user.first_name or callback.from_user.username or "Unknown"
                await db.mark_verified(user_id, display_name)
                pending_result = await forward_svc.process_pending(user_id)

                success_text = "✅ 验证成功！您现在可以发送消息给管理员了。"
                if pending_result["forwarded"] > 0:
                    success_text = f"✅ 验证成功！\n\n📩 刚才的 {pending_result['forwarded']} 条消息已送达管理员。"

                try:
                    await bot.edit_message_text(
                        callback.message.chat.id,
                        callback.message.id,
                        success_text,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                    )
                except Exception as e:
                    logger.error("edit_verify_message_failed", {"error": str(e)})

                if pending_result["forwarded"] == 0:
                    welcome = await db.get_config(CONFIG_WELCOME_MSG, "")
                    if welcome:
                        await bot.send_message(user_id, welcome)

                await stats_svc.record_active_user(user_id)
                await callback.answer("✅ 验证成功！")

                if forward_svc.admin_uid:
                    username = callback.from_user.username
                    name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
                    username_line = f"\n📎 @{username}" if username else ""
                    await bot.send_message(
                        forward_svc.admin_uid,
                        f"✅ <b>新用户验证通过</b>\n\n🆔 <code>{user_id}</code> ({name}){username_line}",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[[
                                InlineKeyboardButton("👤 打开用户资料", url=f"tg://user?id={user_id}")
                            ]]
                        ),
                    )
            else:
                logger.info("verify_failed", {"user_id": user_id, "reason": result["reason"]})
                if result["reason"] in ("expired", "max_attempts"):
                    try:
                        await bot.edit_message_text(
                            callback.message.chat.id,
                            callback.message.id,
                            result["message"],
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                        )
                    except Exception:
                        pass
                    await callback.answer(result["message"], show_alert=True)
                else:
                    await callback.answer(result["message"], show_alert=True)
        finally:
            await db.release_verify_lock(user_id)

    ### ---- 管理菜单回调 ---- ###

    async def _show_submenu(data: str, callback: CallbackQuery) -> None:
        """构建并显示子菜单。"""
        chat_id = callback.message.chat.id
        msg_id = callback.message.id

        builders = {
            "submenu_spam": lambda: build_spam_menu(security_svc),
            "submenu_welcome": lambda: build_welcome_menu(db),
            "submenu_autoreply": lambda: build_autoreply_menu(db),
            "submenu_users": lambda: build_users_menu(db),
            "submenu_protect": lambda: build_protect_menu(db),
            "submenu_stats": lambda: build_stats_menu(stats_svc, db),
            "back_to_main": lambda: build_main_menu(db, forward_svc, security_svc),
        }

        builder = builders.get(data)
        if not builder:
            await callback.answer()
            return

        text, keyboard = await builder()
        await bot.edit_message_text(chat_id, msg_id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await callback.answer()

    @bot.on_callback_query(filters.regex(r"^(submenu_|toggle_|back_to_|refresh_|users_|user_|reset_spam|restart_bot)"))
    async def on_admin_callback(client: Any, callback: CallbackQuery) -> None:
        """处理管理菜单导航。"""
        user_id = callback.from_user.id
        logger.info("admin_callback", {"user_id": user_id, "data": callback.data})
        if not security_svc.is_admin(user_id):
            await callback.answer("无权限", show_alert=True)
            return

        data = callback.data
        chat_id = callback.message.chat.id
        msg_id = callback.message.id

        # 动作处理
        if data == "toggle_spam_filter":
            enabled = await security_svc.is_spam_enabled()
            await security_svc.set_spam_enabled(not enabled)
            await callback.answer(f"{'已开启' if not enabled else '已关闭'}垃圾过滤")
            return await _show_submenu("submenu_spam", callback)

        if data == "reset_spam_rules":
            await security_svc.reset_spam_rules()
            await callback.answer("已重置为默认规则")
            return await _show_submenu("submenu_spam", callback)

        if data == "toggle_protect_user_content":
            current = await db.get_config("protect_user_content", "1")
            enabled = current not in ("0", "false")
            await db.set_config("protect_user_content", "0" if enabled else "1")
            await callback.answer(f"{'已关闭' if enabled else '已开启'}普通复制/转发保护")
            return await _show_submenu("submenu_protect", callback)

        if data == "refresh_stats":
            await callback.answer("已刷新")
            return await _show_submenu("submenu_stats", callback)

        if data == "refresh_users":
            await callback.answer("已刷新")
            return await _show_submenu("submenu_users", callback)

        if data == "restart_bot":
            logger.info("restart_bot_requested", {"admin_id": user_id})
            await callback.answer("🔄 正在重启 Bot...", show_alert=True)
            try:
                await bot.edit_message_text(
                    chat_id, msg_id,
                    "🔄 <b>Bot 正在重启...</b>\n\n<i>重启完成后将自动通知</i>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            # 等待 Telegram API 调用完成后再重启
            await asyncio.sleep(0.5)
            await bot.restart()
            return

        # 子菜单显示
        await _show_submenu(data, callback)

    logger.info("callback_handlers_registered")
