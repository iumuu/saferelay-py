"""Bot 模块 — 封装 Pyrogram/Kurigram Client。

对外暴露 Bot 类以及 Message、CallbackQuery、filters 等类型，
防止 handlers/services 直接 import pyrogram。
"""

from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar, Union

# 从 pyrogram 重导出常用类型
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Chat,
    User,
    ForumTopic,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    ForceReply,
    WebAppInfo,
)
from pyrogram.enums import ChatAction, ParseMode as _ParseMode
ParseMode = _ParseMode
from pyrogram import filters as _pyro_filters
from pyrogram import Client as _PyroClient
from pyrogram.handlers import MessageHandler, CallbackQueryHandler, EditedMessageHandler

from core.logger import get_logger

logger = get_logger("core.bot")


class Bot:
    """Bot 封装，包装 Pyrogram/Kurigram Client。"""

    def __init__(self, bot_token: str, api_id: int = 0, api_hash: str = "",
                 proxy: Optional[Dict[str, Any]] = None,
                 protect_user_content: bool = False,
                 protect_user_content_getter: Optional[Callable[[], Awaitable[bool]]] = None):
        """初始化 Bot。

        Args:
            bot_token: Telegram Bot Token
            api_id: 可选，MTProto API ID（未提供时 Pyrogram 使用默认值）
            api_hash: 可选，MTProto API Hash
            proxy: 可选 SOCKS5 代理，如 {"scheme": "socks5", "hostname": "...", "port": 7890}
        """
        self._token = bot_token
        kwargs: Dict[str, Any] = dict(
            name="saferelay",
            bot_token=bot_token,
            api_id=api_id or 6,
            api_hash=api_hash or "eb06d4abfb49dc3eeb1aeb98ae0f581e",
            in_memory=True,
        )
        if proxy:
            kwargs["proxy"] = proxy
        self._client = _PyroClient(**kwargs)
        self._started = False
        self._protect_user_content = protect_user_content
        self._protect_user_content_getter = protect_user_content_getter

    # ---- 工具方法 ----

    @staticmethod
    def _normalize_parse_mode(parse_mode: Any) -> Any:
        """将字符串 parse_mode 转为 ParseMode 枚举（Kurigram 要求枚举值）。"""
        if isinstance(parse_mode, str):
            mode_map = {
                "html": _ParseMode.HTML,
                "markdown": _ParseMode.MARKDOWN,
                "default": _ParseMode.DEFAULT,
                "disabled": _ParseMode.DISABLED,
            }
            return mode_map.get(parse_mode.lower(), _ParseMode.DEFAULT)
        return parse_mode

    async def _should_protect_user_content(self) -> bool:
        if self._protect_user_content_getter is not None:
            try:
                return bool(await self._protect_user_content_getter())
            except Exception:
                pass
        return self._protect_user_content

    async def _apply_user_content_protection(self, chat_id: Union[int, str], kwargs: Dict[str, Any]) -> None:
        """Apply Telegram copy/forward protection to direct user chats."""
        if "protect_content" in kwargs:
            return
        try:
            is_user_chat = int(chat_id) > 0
        except (TypeError, ValueError):
            is_user_chat = False
        if is_user_chat and await self._should_protect_user_content():
            kwargs["protect_content"] = True

    # ---- 事件注册 ----

    def on_message(self, filter=None, group: int = 0) -> Callable:
        """装饰器：注册消息处理器。"""
        def decorator(func: Callable) -> Callable:
            self._client.add_handler(
                MessageHandler(func, filter),
                group,
            )
            return func
        return decorator

    def on_callback_query(self, filter=None, group: int = 0) -> Callable:
        """装饰器：注册回调查询处理器。"""
        def decorator(func: Callable) -> Callable:
            self._client.add_handler(
                CallbackQueryHandler(func, filter),
                group,
            )
            return func
        return decorator

    def on_edited_message(self, filter=None, group: int = 0) -> Callable:
        """装饰器：注册编辑消息处理器。"""
        def decorator(func: Callable) -> Callable:
            self._client.add_handler(
                EditedMessageHandler(func, filter),
                group,
            )
            return func
        return decorator

    # ---- 消息方法 ----

    async def send_message(
        self, chat_id: Union[int, str], text: str, **kwargs
    ) -> Optional[Message]:
        """发送消息。"""
        if "parse_mode" in kwargs:
            kwargs["parse_mode"] = self._normalize_parse_mode(kwargs["parse_mode"])
        await self._apply_user_content_protection(chat_id, kwargs)
        return await self._client.send_message(chat_id, text, **kwargs)

    async def forward_messages(
        self, chat_id: Union[int, str], from_chat_id: Union[int, str],
        message_ids: Union[int, List[int]], **kwargs
    ) -> Optional[List[Message]]:
        """转发消息。"""
        if isinstance(message_ids, int):
            message_ids = [message_ids]
        await self._apply_user_content_protection(chat_id, kwargs)
        result = await self._client.forward_messages(
            chat_id, from_chat_id, message_ids, **kwargs
        )
        if isinstance(result, list):
            return result
        return [result]

    async def copy_message(
        self, chat_id: Union[int, str], from_chat_id: Union[int, str],
        message_id: int, **kwargs
    ) -> Optional[Message]:
        """复制消息。"""
        await self._apply_user_content_protection(chat_id, kwargs)
        return await self._client.copy_message(chat_id, from_chat_id, message_id, **kwargs)

    async def edit_message_text(
        self, chat_id: Union[int, str], message_id: int, text: str, **kwargs
    ) -> Optional[Message]:
        """编辑消息文本。"""
        if "parse_mode" in kwargs:
            kwargs["parse_mode"] = self._normalize_parse_mode(kwargs["parse_mode"])
        return await self._client.edit_message_text(chat_id, message_id, text, **kwargs)

    async def delete_messages(
        self, chat_id: Union[int, str], message_ids: Union[int, List[int]]
    ) -> bool:
        """删除消息。"""
        if isinstance(message_ids, int):
            message_ids = [message_ids]
        return await self._client.delete_messages(chat_id, message_ids)

    async def answer_callback_query(
        self, callback_query_id: str, text: str = None, **kwargs
    ) -> bool:
        """回答回调查询。"""
        return await self._client.answer_callback_query(
            callback_query_id, text=text, **kwargs
        )

    # ---- 信息查询 ----

    async def get_chat(self, chat_id: Union[int, str]) -> Optional[Chat]:
        """获取聊天信息。"""
        return await self._client.get_chat(chat_id)

    async def get_users(self, user_ids: Union[int, str, List[Union[int, str]]]) -> Union[User, List[User]]:
        """获取用户信息。"""
        return await self._client.get_users(user_ids)

    async def get_me(self) -> Optional[User]:
        """获取机器人自身信息。"""
        return await self._client.get_me()

    # ---- 话题管理 ----

    async def create_forum_topic(
        self, chat_id: Union[int, str], title: str
    ) -> Optional[ForumTopic]:
        """创建论坛话题。"""
        return await self._client.create_forum_topic(chat_id, title)

    async def close_forum_topic(
        self, chat_id: Union[int, str], topic_id: int
    ) -> bool:
        """关闭论坛话题。"""
        return await self._client.close_forum_topic(chat_id, topic_id)

    async def reopen_forum_topic(
        self, chat_id: Union[int, str], topic_id: int
    ) -> bool:
        """重新打开论坛话题。"""
        return await self._client.reopen_forum_topic(chat_id, topic_id)

    async def edit_forum_topic(
        self, chat_id: Union[int, str], topic_id: int, title: str
    ) -> bool:
        """编辑论坛话题标题。"""
        return await self._client.edit_forum_topic(chat_id, topic_id, title)

    async def get_forum_topic(
        self, chat_id: Union[int, str], topic_id: int
    ) -> Optional[ForumTopic]:
        """获取论坛话题信息。"""
        return await self._client.get_forum_topic(chat_id, topic_id)

    async def set_commands(self) -> None:
        """设置 Bot 命令列表。"""
        from pyrogram.types import BotCommand
        commands = [
            BotCommand("menu", "打开管理面板"),
            BotCommand("help", "显示帮助"),
        ]
        await self._client.set_bot_commands(commands)

    async def send_chat_action(
        self, chat_id: Union[int, str], action: str
    ) -> bool:
        """发送聊天动作。"""
        return await self._client.send_chat_action(chat_id, action)

    async def get_messages(
        self, chat_id: Union[int, str], message_ids: Union[int, List[int]]
    ) -> Optional[Union[Message, List[Message]]]:
        """获取消息。"""
        return await self._client.get_messages(chat_id, message_ids)

    # ---- 启动 ----

    async def start(self) -> None:
        """启动 bot（异步，用于嵌入已有事件循环）。"""
        logger.info("bot_starting", {"token_preview": self._token[:8] + "..."})
        await self._client.start()
        self._started = True

    async def stop(self) -> None:
        """停止 bot。"""
        if not self._started:
            logger.info("bot_stop_skipped", {"reason": "not_started"})
            return
        logger.info("bot_stopping")
        self._started = False
        await self._client.stop()

    async def restart(self) -> None:
        """重启 bot（启动新进程后立即退出当前进程）。

        注意：此方法通常在 callback handler 中调用，
        不能 await client.stop()，否则 Pyrogram dispatcher 死锁。
        """
        import subprocess
        import sys
        import os

        logger.info("bot_restarting_via_subprocess")

        cmd = [sys.executable] + sys.argv
        logger.info("bot_restart_cmd", {"cmd": " ".join(cmd)})

        # 启动新进程后立即退出当前进程
        subprocess.Popen(cmd)
        os._exit(0)

    def run(self) -> None:
        """启动 bot（阻塞，独立运行入口）。"""
        logger.info("bot_starting", {"token_preview": self._token[:8] + "..."})
        self._client.run()

    @property
    def client(self) -> _PyroClient:
        """获取原始 Pyrogram Client（仅 core 内部使用）。"""
        return self._client


# 导出 filters 供 handlers/services 使用
filters = _pyro_filters
