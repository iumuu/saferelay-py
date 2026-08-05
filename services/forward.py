"""消息转发服务 — 双向转发、话题模式、媒体组、编辑同步。

对外只暴露 ForwardService 类，handlers 通过此类进行转发操作。
"""
from typing import Any, Dict, List, Optional

from core.bot import Bot, Message
from core.database import Database
from core.logger import get_logger
from services.forward_topic import MediaGroupCollector, TopicManager
from services.forward_edit import EditSyncManager

logger = get_logger("services.forward")


class ForwardService:
    """消息转发服务 — 仅支持群聊话题模式。"""

    def __init__(self, db: Database, bot: Bot, admin_ids: List[int],
                 group_id: int, protect_user_content: bool = False):
        self.db = db
        self.bot = bot
        self.admin_ids = admin_ids
        self.group_id = group_id
        self.protect_user_content = protect_user_content
        self.admin_uid = admin_ids[0] if admin_ids else None
        self.media_collector = MediaGroupCollector()

        # 子模块
        self.topic_mgr = TopicManager(db, bot, group_id)
        self.edit_sync = EditSyncManager(db, bot, self.admin_uid)

    # ---- 话题创建和管理（委托至 TopicManager） ----

    async def ensure_user_topic(self, user_id: int, profile: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """确保用户有话题，如果没有则创建。"""
        return await self.topic_mgr.ensure_user_topic(user_id, profile)

    # ---- 消息转发 ----

    async def forward_guest_message(self, message: Message) -> None:
        """转发用户消息到管理员或话题。"""
        if not message.from_user:
            return
        user_id = message.from_user.id
        logger.info("forward_guest_start", {"user_id": user_id})

        await self.db.increment_message_count()
        await self.db.record_active_user(user_id)

        await self.media_collector.collect(message, lambda msgs: self._do_forward(msgs, user_id))

    async def _do_forward(self, messages: List[Message], user_id: int) -> bool:
        """执行转发到群聊话题，返回是否成功送入对应话题。"""
        logger.info("do_forward", {"user_id": user_id})

        profile = {"first_name": messages[0].from_user.first_name if messages[0].from_user else "",
                   "last_name": messages[0].from_user.last_name if messages[0].from_user else "",
                   "username": messages[0].from_user.username if messages[0].from_user else ""}
        topic = await self.ensure_user_topic(user_id, profile)
        if topic and topic.get("thread_id"):
            ok = await self._forward_to_topic(messages, user_id, topic["thread_id"])
            if ok:
                return True
            logger.warn("topic_forward_failed_retry_recreate", {"user_id": user_id, "old_thread_id": topic.get("thread_id")})
            await self.db.remove_user_topic_by_user(user_id)
            await self.db.remove_thread_mapping(topic["thread_id"])
            topic = await self.ensure_user_topic(user_id, profile)
            if topic and topic.get("thread_id"):
                return await self._forward_to_topic(messages, user_id, topic["thread_id"])
            logger.error("topic_forward_retry_failed_no_thread", {"user_id": user_id})
            return False
        logger.error("topic_forward_failed_no_thread", {"user_id": user_id})
        return False

    async def _forward_to_topic(self, messages: List[Message], user_id: int, thread_id: int) -> bool:
        """转发消息到话题，返回是否至少成功转发一条。"""
        target = {"chat_id": self.group_id, "message_thread_id": thread_id, "label": "topic"}
        logger.info("forward_to_topic", {"user_id": user_id, "thread_id": thread_id, "group_id": self.group_id})
        ok = False
        for msg in messages:
            sent = await self._forward_single(msg, user_id, target)
            if sent:
                ok = True
        return ok

    async def _forward_single(self, msg: Message, user_id: int, target: Dict[str, Any]) -> Optional[Message]:
        """转发单条消息。"""
        try:
            fwd = await msg.forward(
                target["chat_id"],
                message_thread_id=target.get("message_thread_id"),
            )
            if fwd:
                expected_thread = target.get("message_thread_id")
                actual_thread = getattr(fwd, "message_thread_id", None)
                if expected_thread and actual_thread != expected_thread:
                    logger.warn("forward_landed_outside_topic", {
                        "user_id": user_id,
                        "fwd_id": fwd.id,
                        "expected_thread": expected_thread,
                        "actual_thread": actual_thread,
                    })
                    await self.bot.delete_messages(target["chat_id"], fwd.id)
                    return None
                logger.info("forward_single_ok", {"user_id": user_id, "fwd_id": fwd.id, "target_label": target.get("label")})
                await self.db.store_forward_mapping(
                    fwd.id, user_id, msg.id,
                    target.get("chat_id"), expected_thread,
                )
            return fwd
        except Exception as e:
            logger.error("forward_single_failed", {"user_id": user_id, "error": str(e)})
            try:
                copy = await msg.copy(
                    target["chat_id"],
                    message_thread_id=target.get("message_thread_id"),
                )
                if copy:
                    expected_thread = target.get("message_thread_id")
                    actual_thread = getattr(copy, "message_thread_id", None)
                    if expected_thread and actual_thread != expected_thread:
                        logger.warn("copy_landed_outside_topic", {
                            "user_id": user_id,
                            "copy_id": copy.id,
                            "expected_thread": expected_thread,
                            "actual_thread": actual_thread,
                        })
                        await self.bot.delete_messages(target["chat_id"], copy.id)
                        return None
                    await self.db.store_forward_mapping(
                        copy.id, user_id, msg.id,
                        target.get("chat_id"), expected_thread,
                    )
                return copy
            except Exception as e2:
                logger.error("copy_fallback_failed", {"user_id": user_id, "error": str(e2)})
                return None

    async def delete_by_admin_reply(self, message: Message) -> bool:
        """管理员回复自己发出的消息发送 /del，删除用户侧对应的 Bot 消息。

        这里处理的是：管理员在话题里回复用户 -> Bot 复制给用户 的消息映射。
        管理员需要回复自己之前发出的那条管理员消息，再发送 /del 或 /delete。
        """
        reply = message.reply_to_message
        if not reply:
            return False
        mapping = await self.db.get_reply_mapping(reply.id)
        if not mapping:
            logger.warn("delete_admin_reply_mapping_missing", {"admin_msg_id": reply.id})
            return False
        try:
            await self.bot.delete_messages(mapping["guest_chat"], mapping["guest_msg_id"])
            await self.db.delete_reply_mapping(mapping["admin_msg_id"])
            logger.info("delete_admin_sent_message_ok", {
                "admin_msg_id": mapping["admin_msg_id"],
                "guest_chat": mapping["guest_chat"],
                "guest_msg_id": mapping["guest_msg_id"],
            })
            return True
        except Exception as e:
            logger.error("delete_admin_sent_message_failed", {"mapping": mapping, "error": str(e)})
            return False

    # ---- 管理员回复 ----

    async def handle_admin_reply(self, message: Message) -> None:
        """处理管理员回复消息（仅话题模式）。"""
        reply = message.reply_to_message
        if not reply:
            return

        guest_chat_id = None

        # 通过话题 ID 查找用户
        if message.chat and self.group_id and str(message.chat.id) == str(self.group_id) and message.message_thread_id:
            guest_chat_id = await self.db.get_user_by_thread(message.message_thread_id)

        logger.info("admin_reply", {
            "source_chat": message.chat.id if message.chat else None,
            "thread_id": message.message_thread_id,
            "guest_chat_id": guest_chat_id,
        })

        if not guest_chat_id:
            await message.reply_text("⚠️ 未找到原用户映射，可能消息太旧或被清理了缓存。")
            return

        try:
            copy = await message.copy(
                guest_chat_id,
                protect_content=self.protect_user_content,
            )
            if copy:
                await self.db.store_reply_mapping(message.id, guest_chat_id, copy.id)
        except Exception as e:
            logger.error("admin_reply_failed", {"error": str(e)})
            await message.reply_text("⚠️ 回复发送失败。")

    # ---- 编辑同步（委托至 EditSyncManager） ----

    async def sync_guest_edit(self, message: Message) -> None:
        """同步用户编辑消息。"""
        await self.edit_sync.sync_guest_edit(message)

    async def sync_admin_edit(self, message: Message) -> None:
        """同步管理员编辑消息。"""
        await self.edit_sync.sync_admin_edit(message)

    # ---- 暂存队列 ----

    async def append_pending(self, user_id: int, message_id: int) -> int:
        """添加消息到暂存队列。"""
        return await self.db.append_pending(user_id, message_id)

    async def process_pending(self, user_id: int) -> Dict[str, int]:
        """验证通过后处理暂存消息。"""
        pending = await self.db.get_pending(user_id)
        forwarded = 0
        failed = 0

        for item in pending:
            msg_id = item["message_id"]
            try:
                # 确认消息存在（get_messages 返回 Message 或 None）
                orig = await self.bot.get_messages(user_id, msg_id)
                if not orig:
                    logger.warn("pending_forward_no_msg", {"user_id": user_id, "msg_id": msg_id})
                    failed += 1
                    await self.db.delete_pending_item(user_id, msg_id)
                    continue

                # 首次验证后的暂存消息必须复用普通转发路径：
                # 主动校验话题、检测误投 General、必要时重建话题。
                ok = await self._do_forward([orig], user_id)
                if ok:
                    forwarded += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error("pending_forward_failed", {"user_id": user_id, "message_id": msg_id, "error": str(e)})
                failed += 1

            await self.db.delete_pending_item(user_id, msg_id)

        if failed == 0:
            await self.db.clear_pending(user_id)

        return {"forwarded": forwarded, "failed": failed}
