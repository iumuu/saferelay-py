"""话题转发逻辑 — 话题创建、管理、收集媒体组消息。

topic 模块仅被 services/forward.py 中的 ForwardService 导入使用。
"""
import asyncio
import html
import time
from typing import Any, Dict, Optional

from core.bot import ParseMode, Bot
from core.database import Database
from core.logger import get_logger

logger = get_logger("services.forward_topic")

# 媒体组缓冲配置
MEDIA_GROUP_WAIT_MS = 300
MEDIA_GROUP_MAX_WAIT_MS = 3000


class MediaGroupCollector:
    """媒体组消息收集器。"""

    def __init__(self):
        self._buffers: Dict[str, Dict[str, Any]] = {}

    async def collect(self, msg: Any, handler) -> Any:
        """收集媒体组消息，收集完毕后执行 handler。

        Args:
            msg: 当前消息
            handler: 接收消息列表的异步函数

        Returns:
            handler 的返回值或 None（还在收集中）
        """
        group_id = msg.media_group_id
        if not group_id:
            return await handler([msg])

        buf = self._buffers.get(group_id)
        is_first = buf is None

        if is_first:
            buf = {
                "messages": [],
                "handler": handler,
                "last_update": 0,
                "event": asyncio.Event(),
                "max_deadline": time.time() + MEDIA_GROUP_MAX_WAIT_MS / 1000,
            }
            self._buffers[group_id] = buf

        buf["messages"].append(msg)
        buf["last_update"] = time.time()

        if not is_first:
            buf["event"].set()
            buf["event"].clear()
            return None

        # 首条消息：等待收集
        while True:
            remaining = buf["max_deadline"] - time.time()
            if remaining <= 0:
                break
            wait = min(MEDIA_GROUP_WAIT_MS / 1000, remaining)
            await asyncio.sleep(wait)
            since_last = time.time() - buf["last_update"]
            if since_last >= MEDIA_GROUP_WAIT_MS / 1000:
                break

        self._buffers.pop(group_id, None)
        buf["messages"].sort(key=lambda m: m.id)
        return await handler(buf["messages"])


class TopicManager:
    """话题管理器 — 创建、管理用户话题。"""

    def __init__(self, db: Database, bot: Bot, group_id: Optional[int] = None):
        self.db = db
        self.bot = bot
        self.group_id = group_id
        # 线程创建 In-Flight 保护
        self._topic_create_in_flight: Dict[str, asyncio.Future] = {}

    async def ensure_user_topic(
        self, user_id: int, profile: Dict[str, Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """确保用户有话题，如果没有则创建。"""
        logger.info("ensure_user_topic", {"user_id": user_id, "has_profile": profile is not None})
        if not self.group_id:
            return None

        # 已有映射就直接复用。Kurigram 的 get_forum_topic 在部分版本中
        # 会对正常话题返回空或抛异常，主动校验会错误清理映射并重复建话题。
        # 失效映射仅在实际发送失败时由 ForwardService 清理和重建。
        thread_id = await self.db.get_user_topic(user_id)
        if thread_id:
            return {"thread_id": thread_id, "newly_created": False}

        # In-Flight 去重
        key = str(user_id)
        if key in self._topic_create_in_flight:
            try:
                return await self._topic_create_in_flight[key]
            except Exception:
                pass

        future = asyncio.get_event_loop().create_future()
        self._topic_create_in_flight[key] = future

        try:
            result = await self._ensure_user_topic_internal(user_id, profile)
            future.set_result(result)
            return result
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            self._topic_create_in_flight.pop(key, None)

    async def _ensure_user_topic_internal(
        self, user_id: int, profile: Dict[str, Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """内部：创建用户话题。"""
        thread_id = await self.db.get_user_topic(user_id)
        if thread_id:
            return {"thread_id": thread_id, "newly_created": False}

        # 检查群组是否为论坛群组
        try:
            chat = await self.bot.get_chat(self.group_id)
            logger.info("forum_check_result", {"group_id": self.group_id, "is_forum": chat.is_forum if chat else False})
            if not chat or not chat.is_forum:
                logger.error("group_not_forum", {"group_id": self.group_id})
                return None
        except Exception as e:
            logger.error("check_forum_failed", {"group_id": self.group_id, "error": str(e)})
            return None

        username = ""
        display_name = ""
        if profile:
            username = (profile.get("username") or "").strip()
            first_name = profile.get("first_name") or ""
            last_name = profile.get("last_name") or ""
            display_name = f"{first_name} {last_name}".strip()

        if username:
            title_name = f"@{username}"
        elif display_name:
            title_name = display_name
        else:
            title_name = "访客"

        title = f"{title_name}（{user_id}）"[:60]

        try:
            topic = await self.bot.create_forum_topic(self.group_id, title)
            if not topic:
                logger.error("create_topic_failed", {"user_id": user_id})
                return None
            new_thread_id = topic.id

            await self.db.set_user_topic(user_id, new_thread_id)
            await self.db.set_thread_mapping(new_thread_id, user_id)

            first_name = (profile or {}).get("first_name") or ""
            last_name = (profile or {}).get("last_name") or ""
            full_name = f"{first_name} {last_name}".strip() or "未知用户"
            welcome_lines = [
                "👤 新访客对话",
                f"UID：<code>{user_id}</code>",
                f"昵称：<a href=\"tg://user?id={user_id}\">{html.escape(full_name)}</a>",
                "\n请在此话题内回复用户消息。",
            ]

            await self.bot.send_message(
                self.group_id,
                "\n".join(welcome_lines),
                message_thread_id=new_thread_id,
                parse_mode=ParseMode.HTML,
            )

            logger.info("user_topic_created", {"user_id": user_id, "thread_id": new_thread_id})
            return {"thread_id": new_thread_id, "newly_created": True}

        except Exception as e:
            logger.error("create_topic_error", {"user_id": user_id, "error": str(e)})
            return None
