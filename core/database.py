"""数据库模块 — 封装 SQLAlchemy async，提供所有持久化方法。

所有 services/handlers 只从本模块导入，不直接使用 SQLAlchemy。
"""
import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, delete
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from core.models import (
    Base, VerifiedUser, TopicMapping, ForwardMapping, ReplyMapping,
    BannedUser, WhitelistUser, RateLimit, VerifyLock, StatsMessage,
    StatsActiveUser, AppConfig, PendingQueue, ThreadMapping, EditNotice,
)
from core.logger import get_logger

logger = get_logger("core.database")

DB_PATH = "data/saferelay.db"


class Database:
    """数据库封装，使用 SQLAlchemy async。

    所有持久化操作通过此类完成，外部不直接使用 SQLAlchemy。
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        db_parent = Path(db_path).expanduser().parent
        if str(db_parent) not in ("", "."):
            db_parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False,
        )

    async def init(self) -> None:
        """初始化数据库连接并建表。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("db_initialized", {"path": self._db_path})

    async def close(self) -> None:
        """关闭数据库连接。"""
        await self._engine.dispose()
        logger.info("db_closed")

    # ---- 内部辅助 ----

    def _session(self) -> AsyncSession:
        """获取新会话。"""
        return self._session_factory()

    async def _fetchone(self, stmt) -> Optional[Any]:
        """执行查询并返回首行。"""
        async with self._session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def _fetchall(self, stmt) -> List[Any]:
        """执行查询并返回所有行。"""
        async with self._session() as session:
            result = await session.execute(stmt)
            return result.scalars().all()

    async def _fetchall_rows(self, stmt) -> List[Any]:
        """执行查询并返回所有 Row 对象。"""
        async with self._session() as session:
            result = await session.execute(stmt)
            return result.all()

    async def _execute(self, stmt) -> None:
        """执行写入语句。"""
        async with self._session() as session:
            await session.execute(stmt)
            await session.commit()

    # ---- 用户验证 ----

    async def is_verified(self, user_id: int) -> bool:
        """检查用户是否已验证。"""
        row = await self._fetchone(
            select(VerifiedUser.user_id).where(VerifiedUser.user_id == user_id)
        )
        return row is not None

    async def mark_verified(self, user_id: int, display_name: str) -> None:
        """标记用户已验证。"""
        async with self._session() as session:
            await session.merge(VerifiedUser(
                user_id=user_id, display_name=display_name,
                verified_at=int(time.time()),
            ))
            await session.commit()

    async def remove_verified(self, user_id: int) -> None:
        """移除用户验证状态。"""
        await self._execute(
            delete(VerifiedUser).where(VerifiedUser.user_id == user_id)
        )

    # ---- 用户资料缓存 ----

    async def upsert_user_profile(self, user_id: int, profile: Dict[str, Any]) -> None:
        """更新用户资料缓存。"""
        async with self._session() as session:
            await session.merge(VerifiedUser(
                user_id=user_id,
                display_name=profile.get("display_name", ""),
                verified_at=int(time.time()),
            ))
            await session.commit()

    # ---- 话题映射 ----

    async def get_user_topic(self, user_id: int) -> Optional[int]:
        """获取用户的话题 ID。"""
        row = await self._fetchone(
            select(TopicMapping.thread_id).where(TopicMapping.user_id == user_id)
        )
        return row

    async def set_user_topic(self, user_id: int, thread_id: int) -> None:
        """设置用户的话题映射。"""
        async with self._session() as session:
            await session.merge(TopicMapping(
                user_id=user_id, thread_id=thread_id, created_at=int(time.time()),
            ))
            await session.commit()

    async def remove_user_topic(self, user_id: int, thread_id: int) -> None:
        """移除用户的话题映射。"""
        await self._execute(
            delete(TopicMapping).where(
                TopicMapping.user_id == user_id,
                TopicMapping.thread_id == thread_id,
            )
        )

    async def get_user_by_thread(self, thread_id: int) -> Optional[int]:
        """通过话题 ID 获取用户 ID。"""
        # 优先查 thread_mapping
        row = await self._fetchone(
            select(ThreadMapping.user_id).where(ThreadMapping.thread_id == thread_id)
        )
        if row is not None:
            return row
        # 回退 topic_mapping
        row = await self._fetchone(
            select(TopicMapping.user_id).where(TopicMapping.thread_id == thread_id)
        )
        return row

    async def set_thread_mapping(self, thread_id: int, user_id: int) -> None:
        """设置线程 → 用户映射。"""
        async with self._session() as session:
            await session.merge(ThreadMapping(thread_id=thread_id, user_id=user_id))
            await session.commit()

    # ---- 消息映射 ----

    async def store_forward_mapping(
        self, fwd_msg_id: int, source_chat: int, source_msg_id: int,
        target_chat: int = None, thread_id: int = None,
    ) -> None:
        """存储转发消息映射。"""
        async with self._session() as session:
            session.add(ForwardMapping(
                fwd_msg_id=fwd_msg_id, source_chat=source_chat,
                source_msg_id=source_msg_id, target_chat=target_chat,
                thread_id=thread_id, created_at=int(time.time()),
            ))
            await session.commit()

    async def get_forward_mapping(self, fwd_msg_id: int) -> Optional[Dict[str, Any]]:
        """根据转发消息 ID 获取原始映射。"""
        async with self._session() as session:
            result = await session.execute(
                select(ForwardMapping).where(ForwardMapping.fwd_msg_id == fwd_msg_id)
            )
            row = result.scalar_one_or_none()
            if row:
                return {
                    "fwd_msg_id": row.fwd_msg_id,
                    "source_chat": row.source_chat,
                    "source_msg_id": row.source_msg_id,
                    "target_chat": row.target_chat,
                    "thread_id": row.thread_id,
                }
            return None

    async def get_forward_by_orig(self, orig_msg_id: int) -> Optional[Dict[str, Any]]:
        """根据原始消息 ID 获取转发映射。"""
        async with self._session() as session:
            result = await session.execute(
                select(ForwardMapping).where(ForwardMapping.source_msg_id == orig_msg_id)
            )
            row = result.scalar_one_or_none()
            if row:
                return {
                    "fwd_msg_id": row.fwd_msg_id,
                    "source_chat": row.source_chat,
                    "source_msg_id": row.source_msg_id,
                    "target_chat": row.target_chat,
                    "thread_id": row.thread_id,
                }
            return None

    async def get_forward_by_source(self, source_chat: int, source_msg_id: int) -> Optional[Dict[str, Any]]:
        """根据源聊天和源消息 ID 获取转发映射。"""
        async with self._session() as session:
            result = await session.execute(
                select(ForwardMapping).where(
                    ForwardMapping.source_chat == source_chat,
                    ForwardMapping.source_msg_id == source_msg_id,
                )
            )
            row = result.scalar_one_or_none()
            if row:
                return {
                    "fwd_msg_id": row.fwd_msg_id,
                    "source_chat": row.source_chat,
                    "source_msg_id": row.source_msg_id,
                    "target_chat": row.target_chat,
                    "thread_id": row.thread_id,
                }
            return None

    async def delete_forward_mapping(self, fwd_msg_id: int) -> None:
        """删除转发映射。"""
        await self._execute(
            delete(ForwardMapping).where(ForwardMapping.fwd_msg_id == fwd_msg_id)
        )

    async def get_original_mapping(self, orig_msg_id: int) -> Optional[int]:
        """根据原始消息 ID 获取转发后的消息 ID。"""
        row = await self._fetchone(
            select(ForwardMapping.fwd_msg_id).where(
                ForwardMapping.source_msg_id == orig_msg_id
            )
        )
        return row

    # ---- 管理员回复映射 ----

    async def store_reply_mapping(
        self, admin_msg_id: int, guest_chat: int, guest_msg_id: int,
    ) -> None:
        """存储管理员回复映射。"""
        async with self._session() as session:
            session.add(ReplyMapping(
                admin_msg_id=admin_msg_id, guest_chat=guest_chat,
                guest_msg_id=guest_msg_id, created_at=int(time.time()),
            ))
            await session.commit()

    async def get_reply_mapping(self, admin_msg_id: int) -> Optional[Dict[str, Any]]:
        """获取管理员回复映射。"""
        async with self._session() as session:
            result = await session.execute(
                select(ReplyMapping).where(ReplyMapping.admin_msg_id == admin_msg_id)
            )
            row = result.scalar_one_or_none()
            if row:
                return {
                    "admin_msg_id": row.admin_msg_id,
                    "guest_chat": row.guest_chat,
                    "guest_msg_id": row.guest_msg_id,
                }
            return None

    # ---- 黑白名单 ----

    async def is_banned(self, user_id: int) -> bool:
        """检查用户是否被封禁。"""
        row = await self._fetchone(
            select(BannedUser.user_id).where(BannedUser.user_id == user_id)
        )
        return row is not None

    async def ban_user(self, user_id: int, reason: str = "") -> None:
        """封禁用户。"""
        async with self._session() as session:
            await session.merge(BannedUser(
                user_id=user_id, reason=reason, banned_at=int(time.time()),
            ))
            await session.commit()

    async def unban_user(self, user_id: int) -> None:
        """解封用户。"""
        await self._execute(
            delete(BannedUser).where(BannedUser.user_id == user_id)
        )

    async def is_whitelisted(self, user_id: int) -> bool:
        """检查用户是否在白名单中。"""
        row = await self._fetchone(
            select(WhitelistUser.user_id).where(WhitelistUser.user_id == user_id)
        )
        return row is not None

    async def add_whitelist(self, user_id: int) -> None:
        """添加白名单用户。"""
        async with self._session() as session:
            stmt = select(WhitelistUser).where(WhitelistUser.user_id == user_id)
            exists = (await session.execute(stmt)).scalar_one_or_none()
            if not exists:
                session.add(WhitelistUser(
                    user_id=user_id, added_at=int(time.time()),
                ))
                await session.commit()

    async def remove_whitelist(self, user_id: int) -> None:
        """移除白名单用户。"""
        await self._execute(
            delete(WhitelistUser).where(WhitelistUser.user_id == user_id)
        )

    # ---- 速率限制 ----

    async def check_rate_limit(self, key: str, window_ms: int, max_count: int) -> bool:
        """检查速率限制，返回是否允许通过。"""
        now = int(time.time() * 1000)
        cutoff = now - window_ms

        async with self._session() as session:
            result = await session.get(RateLimit, key)
            timestamps: List[int] = json.loads(result.timestamps) if result else []
            timestamps = [ts for ts in timestamps if ts > cutoff]
            if len(timestamps) >= max_count:
                return False
            timestamps.append(now)
            await session.merge(RateLimit(key=key, timestamps=json.dumps(timestamps)))
            await session.commit()
            return True

    # ---- 验证锁 ----

    async def acquire_verify_lock(self, user_id: int, ttl_seconds: int) -> bool:
        """尝试获取验证锁。"""
        expires = int(time.time()) + ttl_seconds
        async with self._session() as session:
            existing = await session.get(VerifyLock, user_id)
            if existing:
                if existing.expires_at < int(time.time()):
                    existing.expires_at = expires
                    await session.commit()
                    return True
                return False
            session.add(VerifyLock(user_id=user_id, expires_at=expires))
            await session.commit()
            return True

    async def release_verify_lock(self, user_id: int) -> None:
        """释放验证锁。"""
        await self._execute(
            delete(VerifyLock).where(VerifyLock.user_id == user_id)
        )

    # ---- 统计 ----

    async def increment_message_count(self) -> None:
        """递增消息计数。"""
        today_str = date.today().isoformat()
        async with self._session() as session:
            result = await session.get(StatsMessage, today_str)
            if result:
                result.count += 1
            else:
                session.add(StatsMessage(date=today_str, count=1))
            await session.commit()

    async def record_active_user(self, user_id: int) -> None:
        """记录活跃用户。"""
        today_str = date.today().isoformat()
        async with self._session() as session:
            stmt = select(StatsActiveUser).where(
                StatsActiveUser.date == today_str,
                StatsActiveUser.user_id == user_id,
            )
            exists = (await session.execute(stmt)).scalar_one_or_none()
            if not exists:
                session.add(StatsActiveUser(date=today_str, user_id=user_id))
                await session.commit()

    async def get_stats(self, days: int = 7) -> List[Dict[str, Any]]:
        """获取统计信息。"""
        results = []
        for i in range(days):
            d = (date.today() - timedelta(days=i)).isoformat()
            async with self._session() as session:
                msg_row = await session.get(StatsMessage, d)
                count = msg_row.count if msg_row else 0
                cnt_result = await session.execute(
                    select(func.count(StatsActiveUser.user_id)).where(
                        StatsActiveUser.date == d
                    )
                )
                active = cnt_result.scalar() or 0
            results.append({"date": d, "messages": count, "active_users": active})
        return results

    async def get_total_message_count(self) -> int:
        """获取总消息数。"""
        row = await self._fetchone(select(func.sum(StatsMessage.count)))
        return row or 0

    # ---- 已验证用户列表 ----

    async def get_verified_users(self) -> List[Dict[str, Any]]:
        """获取所有已验证用户。"""
        async with self._session() as session:
            result = await session.execute(
                select(VerifiedUser).order_by(VerifiedUser.verified_at.desc())
            )
            return [
                {"user_id": r.user_id, "display_name": r.display_name,
                 "verified_at": r.verified_at}
                for r in result.scalars().all()
            ]

    async def get_verified_count(self) -> int:
        """获取已验证用户数量。"""
        row = await self._fetchone(select(func.count(VerifiedUser.user_id)))
        return row or 0

    # ---- 配置 ----

    async def get_config(self, key: str, default: str = "") -> str:
        """获取配置值。"""
        async with self._session() as session:
            row = await session.get(AppConfig, key)
            return row.value if row else default

    async def set_config(self, key: str, value: str) -> None:
        """设置配置值。"""
        async with self._session() as session:
            await session.merge(AppConfig(key=key, value=value))
            await session.commit()

    async def delete_config(self, key: str) -> None:
        """删除配置。"""
        await self._execute(
            delete(AppConfig).where(AppConfig.key == key)
        )

    # ---- 暂存队列 ----

    async def append_pending(
        self, user_id: int, message_id: int, msg_data: str = "",
    ) -> int:
        """添加消息到暂存队列。返回队列长度。"""
        cutoff = int(time.time()) - 86400
        async with self._session() as session:
            # 清理过期
            await session.execute(
                delete(PendingQueue).where(
                    PendingQueue.user_id == user_id,
                    PendingQueue.created_at < cutoff,
                )
            )
            # 插入
            stmt = select(PendingQueue).where(
                PendingQueue.user_id == user_id,
                PendingQueue.message_id == message_id,
            )
            exists = (await session.execute(stmt)).scalar_one_or_none()
            if not exists:
                session.add(PendingQueue(
                    user_id=user_id, message_id=message_id,
                    msg_data=msg_data, created_at=int(time.time()),
                ))
            await session.commit()
            # 计数
            cnt_result = await session.execute(
                select(func.count(PendingQueue.message_id)).where(
                    PendingQueue.user_id == user_id
                )
            )
            return cnt_result.scalar() or 0

    async def get_pending(self, user_id: int) -> List[Dict[str, Any]]:
        """获取暂存消息列表。"""
        async with self._session() as session:
            result = await session.execute(
                select(PendingQueue).where(PendingQueue.user_id == user_id)
                .order_by(PendingQueue.created_at)
            )
            return [
                {"message_id": r.message_id, "msg_data": r.msg_data}
                for r in result.scalars().all()
            ]

    async def clear_pending(self, user_id: int) -> None:
        """清空暂存队列。"""
        await self._execute(
            delete(PendingQueue).where(PendingQueue.user_id == user_id)
        )

    async def delete_pending_item(self, user_id: int, message_id: int) -> None:
        """删除暂存队列中的一条消息。"""
        await self._execute(
            delete(PendingQueue).where(
                PendingQueue.user_id == user_id,
                PendingQueue.message_id == message_id,
            )
        )

    # ---- 编辑通知（仅供 forward_edit 调用） ----

    async def get_edit_notice(
        self, user_id: int, orig_msg_id: int,
    ) -> Optional[Dict[str, Any]]:
        """获取编辑通知消息。"""
        async with self._session() as session:
            stmt = select(EditNotice).where(
                EditNotice.user_id == user_id,
                EditNotice.orig_msg_id == orig_msg_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                return {
                    "user_id": row.user_id,
                    "orig_msg_id": row.orig_msg_id,
                    "notice_chat": row.notice_chat,
                    "notice_msg_id": row.notice_msg_id,
                }
            return None

    async def store_edit_notice(
        self, user_id: int, orig_msg_id: int,
        notice_chat: int, notice_msg_id: int,
    ) -> None:
        """存储编辑通知映射。"""
        async with self._session() as session:
            await session.merge(EditNotice(
                user_id=user_id, orig_msg_id=orig_msg_id,
                notice_chat=notice_chat, notice_msg_id=notice_msg_id,
                created_at=int(time.time()),
            ))
            await session.commit()
