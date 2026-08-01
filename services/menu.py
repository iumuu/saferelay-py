"""管理菜单构建 — 提供管理面板各子菜单的文本和键盘构建函数。

仅供 handlers/callback.py 使用，将菜单构建逻辑从 handler 中剥离。
"""
import html
from typing import Any, Dict, List, Optional, Tuple

from core.bot import InlineKeyboardButton, InlineKeyboardMarkup
from core.database import Database
from core.logger import get_logger

logger = get_logger("services.menu")

CONFIG_WELCOME_MSG = "welcome_msg"
CONFIG_PROTECT_USER_CONTENT = "protect_user_content"
CONFIG_VERIFY_PROVIDER = "verify_provider"


async def build_main_menu(
    db: Database, forward_svc: Any, security_svc: Any,
) -> Tuple[str, InlineKeyboardMarkup]:
    """构建管理面板主菜单。"""
    welcome_msg = await db.get_config(CONFIG_WELCOME_MSG, "")
    auto_reply = await db.get_config("auto_reply_msg", "")
    spam_enabled = await security_svc.is_spam_enabled()
    protect_user_content = await db.get_config(CONFIG_PROTECT_USER_CONTENT, "1")
    protect_enabled = protect_user_content not in ("0", "false")
    verify_provider = await db.get_config(CONFIG_VERIFY_PROVIDER, "quiz")
    verify_label = "hCaptcha" if verify_provider == "hcaptcha" else "本地题库"

    text = (
        f"🛠 <b>SafeRelay 管理面板</b>\n\n"
        f"📊 <b>当前配置:</b>\n"
        f"🔸 📝 验证模式：{verify_label}\n"
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
            [InlineKeyboardButton("📝 验证模式", callback_data="submenu_verify"),
             InlineKeyboardButton("🔒 普通复制/转发", callback_data="submenu_protect")],
            [InlineKeyboardButton("📊 统计信息", callback_data="submenu_stats")],
            [InlineKeyboardButton("🔄 重启 Bot", callback_data="restart_bot")],
        ]
    )
    return text, keyboard


async def build_verify_menu(db: Database) -> Tuple[str, InlineKeyboardMarkup]:
    """构建验证模式切换菜单。"""
    provider = await db.get_config(CONFIG_VERIFY_PROVIDER, "quiz")
    is_hcaptcha = provider == "hcaptcha"
    text = (
        f"📝 <b>验证模式设置</b>\n\n"
        f"当前模式: <b>{'🟢 hCaptcha' if is_hcaptcha else '🟢 本地题库 Quiz'}</b>\n\n"
        f"• 本地题库：用户回答简单问题\n"
        f"• hCaptcha：用户通过 Telegram Web App 完成人机验证\n\n"
        f"切换后对新触发验证的用户立即生效。"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ 本地题库 Quiz" if not is_hcaptcha else "切换到本地题库 Quiz", callback_data="set_verify_quiz")],
        [InlineKeyboardButton("✅ hCaptcha" if is_hcaptcha else "切换到 hCaptcha", callback_data="set_verify_hcaptcha")],
        [InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")],
    ])
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
    db: Database, page: int = 0,
) -> Tuple[str, InlineKeyboardMarkup]:
    """构建可分页的用户管理菜单。"""
    users = await db.get_managed_users()
    page_size = 8
    total = len(users)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    page_users = users[page * page_size:(page + 1) * page_size]

    text = (
        f"👥 <b>用户管理</b>\n\n"
        f"已知用户：<b>{total}</b>　页码：<b>{page + 1}/{pages}</b>\n"
        f"点击用户查看资料，并可直接管理黑名单和白名单。"
    )
    rows = []
    for item in page_users:
        name = item.get("display_name") or "访客"
        if len(name) > 16:
            name = name[:15] + "…"
        flags = ""
        if item.get("banned"):
            flags += " 🚫"
        if item.get("whitelisted"):
            flags += " ⭐"
        rows.append([InlineKeyboardButton(
            f"{name} · {item['user_id']}{flags}",
            callback_data=f"user_view:{item['user_id']}:{page}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"users_page:{page - 1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"users_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔄 刷新", callback_data=f"users_page:{page}")])
    rows.append([InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def build_user_detail(
    db: Database, bot: Any, user_id: int, page: int = 0,
) -> Tuple[str, InlineKeyboardMarkup]:
    """构建用户资料和操作按钮。"""
    banned = await db.is_banned(user_id)
    whitelisted = await db.is_whitelisted(user_id)
    verified = await db.is_verified(user_id)
    name = "未知"
    try:
        user = await bot.get_users(user_id)
        if user:
            name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "未知"
    except Exception:
        pass

    text = (
        f"👤 <b>用户资料</b>\n\n"
        f"UID：<code>{user_id}</code>\n"
        f"昵称：<a href=\"tg://user?id={user_id}\">{html.escape(name)}</a>\n\n"
        f"验证状态：{'✅ 已验证' if verified else '⚪️ 未验证'}\n"
        f"黑名单：{'🚫 已拉黑' if banned else '✅ 正常'}\n"
        f"白名单：{'⭐ 已加入' if whitelisted else '⚪️ 未加入'}"
    )
    rows = [
        [InlineKeyboardButton(
            "✅ 解除拉黑" if banned else "🚫 拉入黑名单",
            callback_data=f"user_unban:{user_id}:{page}" if banned else f"user_ban:{user_id}:{page}",
        )],
        [InlineKeyboardButton(
            "移出白名单" if whitelisted else "⭐ 加入白名单",
            callback_data=f"user_untrust:{user_id}:{page}" if whitelisted else f"user_trust:{user_id}:{page}",
        )],
    ]
    if verified:
        rows.append([InlineKeyboardButton(
            "🔄 取消验证",
            callback_data=f"user_unverify:{user_id}:{page}",
        )])
    rows.append([InlineKeyboardButton("◀️ 返回用户列表", callback_data=f"users_page:{page}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
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
