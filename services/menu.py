"""管理菜单构建 — 提供管理面板各子菜单的文本和键盘构建函数。

仅供 handlers/callback.py 使用，将菜单构建逻辑从 handler 中剥离。
"""
from typing import Any, Dict, List, Optional, Tuple

from core.bot import InlineKeyboardButton, InlineKeyboardMarkup
from core.database import Database
from core.logger import get_logger

logger = get_logger("services.menu")

CONFIG_WELCOME_MSG = "welcome_msg"
CONFIG_PROTECT_USER_CONTENT = "protect_user_content"


async def build_main_menu(
    db: Database, forward_svc: Any, security_svc: Any,
) -> Tuple[str, InlineKeyboardMarkup]:
    """构建管理面板主菜单。"""
    welcome_msg = await db.get_config(CONFIG_WELCOME_MSG, "")
    auto_reply = await db.get_config("auto_reply_msg", "")
    spam_enabled = await security_svc.is_spam_enabled()
    protect_user_content = await db.get_config(CONFIG_PROTECT_USER_CONTENT, "1")
    protect_enabled = protect_user_content not in ("0", "false")

    text = (
        f"🛠 <b>SafeRelay 管理面板</b>\n\n"
        f"📊 <b>当前配置:</b>\n"
        f"🔸 📝 验证模式：本地题库\n"
        f"🔸 {'🟢' if spam_enabled else '🔴'} 垃圾过滤：{'已开启' if spam_enabled else '已关闭'}\n"
        f"🔸 {'🟢' if protect_enabled else '🔴'} 普通复制/转发保护：{'已开启' if protect_enabled else '已关闭'}\n"
        f"🔸 {'🟢' if welcome_msg else '⚪️'} 欢迎消息：{'已设置' if welcome_msg else '未设置'}\n"
        f"🔸 {'🟢' if auto_reply else '⚪️'} 自动回复：{'已设置' if auto_reply else '未设置'}\n"
        f"🔸 💬 转发模式：群聊话题\n\n"
        f"👇 点击下方按钮进入设置"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton("🗑 垃圾过滤", callback_data="submenu_spam"),
             InlineKeyboardButton("👥 用户管理", callback_data="submenu_users")],
            [InlineKeyboardButton("👋 欢迎消息", callback_data="submenu_welcome"),
             InlineKeyboardButton("🤖 自动回复", callback_data="submenu_autoreply")],
            [InlineKeyboardButton("🔒 普通复制/转发", callback_data="submenu_protect"),
             InlineKeyboardButton("📊 统计信息", callback_data="submenu_stats")],
            [InlineKeyboardButton("🔄 重启 Bot", callback_data="restart_bot")],
        ]
    )
    return text, keyboard


async def build_spam_menu(
    security_svc: Any,
) -> Tuple[str, InlineKeyboardMarkup]:
    """构建垃圾过滤设置菜单。"""
    enabled = await security_svc.is_spam_enabled()
    text = (
        f"🗑 <b>垃圾消息过滤设置</b>\n\n"
        f"当前状态: <b>{'🟢 已开启' if enabled else '🔴 已关闭'}</b>\n\n"
        f"💡 直接发送关键词即可添加拦截规则\n"
        f"发送 <code>del:关键词</code> 删除拦截词"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            f"{'🔴 关闭过滤' if enabled else '🟢 开启过滤'}",
            callback_data="toggle_spam_filter",
        )],
        [InlineKeyboardButton("🔄 重置为默认规则", callback_data="reset_spam_rules")],
        [InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")],
    ])
    return text, keyboard


async def build_protect_menu(db: Database) -> Tuple[str, InlineKeyboardMarkup]:
    """构建普通复制/转发保护设置菜单。"""
    raw = await db.get_config(CONFIG_PROTECT_USER_CONTENT, "1")
    enabled = raw not in ("0", "false")
    text = (
        f"🔒 <b>普通复制/转发保护</b>\n\n"
        f"当前状态: <b>{'🟢 已开启' if enabled else '🔴 已关闭'}</b>\n\n"
        f"开启后，发给普通用户的消息会启用 Telegram protect_content。"
        f" 用户无法直接复制或转发这些消息。"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            f"{'🔴 关闭保护' if enabled else '🟢 开启保护'}",
            callback_data="toggle_protect_user_content",
        )],
        [InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")],
    ])
    return text, keyboard


async def build_welcome_menu(
    db: Database,
) -> Tuple[str, InlineKeyboardMarkup]:
    """构建欢迎消息设置菜单。"""
    current = await db.get_config(CONFIG_WELCOME_MSG, "(未设置)")
    text = f"👋 <b>欢迎消息设置</b>\n\n📄 <b>当前内容:</b>\n<pre>{current}</pre>\n\n💡 使用 /welcome 消息内容 设置新消息"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")],
    ])
    return text, keyboard


async def build_autoreply_menu(
    db: Database,
) -> Tuple[str, InlineKeyboardMarkup]:
    """构建自动回复设置菜单。"""
    current = await db.get_config("auto_reply_msg", "(已关闭)")
    text = f"🤖 <b>自动回复设置</b>\n\n📄 <b>当前内容:</b>\n<pre>{current}</pre>\n\n💡 使用 /autoreply 消息内容 设置自动回复"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")],
    ])
    return text, keyboard


async def build_users_menu(
    db: Database,
) -> Tuple[str, InlineKeyboardMarkup]:
    """构建用户管理菜单。"""
    total = await db.get_verified_count()
    text = f"👥 <b>用户管理</b>\n\n📊 已验证用户: {total}\n\n💡 使用 /ban、/trust 等命令管理用户"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🔄 刷新", callback_data="refresh_users")],
        [InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")],
    ])
    return text, keyboard


async def build_stats_menu(
    stats_svc: Any, db: Database,
) -> Tuple[str, InlineKeyboardMarkup]:
    """构建统计信息菜单。"""
    stats = await stats_svc.get_stats()
    text = (
        f"📊 <b>统计信息</b>\n\n"
        f"📅 <b>今日数据</b>\n"
        f"• 消息数: {stats['today_messages']}\n"
        f"• 活跃用户: {stats['today_active_users']}\n\n"
        f"📈 <b>累计数据</b>\n"
        f"• 总消息数: {stats['total_messages']}\n"
        f"• 已验证用户: {await stats_svc.get_verified_count()}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🔄 刷新", callback_data="refresh_stats")],
        [InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")],
    ])
    return text, keyboard
