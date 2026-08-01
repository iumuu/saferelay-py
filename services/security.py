"""安全服务 — 封禁、白名单、欺诈检测、垃圾过滤。"""

import json
import re
import time
from typing import Any, Dict, List, Optional

from core.bot import Bot, Message
from core.database import Database
from core.http import HttpClient
from core.logger import get_logger
from utils.helpers import TTLCache, escape_html

logger = get_logger("services.security")

# 默认垃圾过滤规则
DEFAULT_SPAM_RULES: Dict[str, Any] = {
    "maxLinks": 3,
    "keywords": [
        "加群", "进群", "推广", "广告", "返利", "博彩", "代投", "套利",
        "USDT", "BTC", "ETH", "币圈", "空投", "交易所", "稳赚", "客服", "开户链接",
        "刷单", "兼职", "日赚", "高回报", "零风险", "投资", "理财", "赚钱",
    ],
    "regexes": [
        r"\b(?:usdt|btc|eth|trx|bnb)\b",
        r"(?:t\.me/\w+|telegram\.me/\w+)",
        r"(?:免费|稳赚|日赚|高回报|带单|私聊我|加我)",
    ],
    "allowKeywords": [],
    "allowRegexes": [],
}


class SecurityService:
    """安全服务。"""

    def __init__(self, db: Database, bot: Bot, http: HttpClient,
                 admin_ids: List[int], admin_uid: Optional[int] = None):
        self.db = db
        self.bot = bot
        self.http = http
        self.admin_ids = admin_ids
        self.admin_uid = admin_uid
        self._blocked_cache = TTLCache(default_ttl_ms=60000)
        self._whitelist_cache = TTLCache(default_ttl_ms=30000)
        self._fraud_cache = TTLCache(default_ttl_ms=3600000)
        self._user_profile_cache = TTLCache(default_ttl_ms=86400000)

        self.fraud_db_url = "https://raw.githubusercontent.com/qianqi32/SafeRelay/main/data/fraud.db"
        self._fraud_list: Optional[List[str]] = None
        self._fraud_loaded_at: float = 0

    # ---- 管理员检查 ----

    def is_admin(self, user_id: int) -> bool:
        """检查用户是否为管理员。"""
        # env 中的管理员是字符串，支持数字格式
        return user_id in self.admin_ids or str(user_id) in [str(a) for a in self.admin_ids]

    # ---- 封禁 ----

    async def ban_user(self, user_id: int, reason: str = "") -> None:
        """封禁用户。"""
        await self.db.ban_user(user_id, reason)
        self._blocked_cache.delete(f"blocked:{user_id}")

    async def unban_user(self, user_id: int) -> None:
        """解封用户。"""
        await self.db.unban_user(user_id)
        self._blocked_cache.delete(f"blocked:{user_id}")

    async def is_banned(self, user_id: int) -> bool:
        """检查用户是否被封禁（带缓存）。"""
        cached = self._blocked_cache.get(f"blocked:{user_id}")
        if cached is not None:
            return cached
        banned = await self.db.is_banned(user_id)
        if banned:
            logger.warn("banned_hit", {"user_id": user_id})
        self._blocked_cache.set(f"blocked:{user_id}", banned, 60000)
        return banned

    # ---- 白名单 ----

    async def add_whitelist(self, user_id: int) -> None:
        """添加白名单。"""
        await self.db.add_whitelist(user_id)
        self._whitelist_cache.delete(f"whitelist:{user_id}")

    async def remove_whitelist(self, user_id: int) -> None:
        """移除白名单。"""
        await self.db.remove_whitelist(user_id)
        self._whitelist_cache.delete(f"whitelist:{user_id}")

    async def is_whitelisted(self, user_id: int) -> bool:
        """检查白名单（带缓存）。"""
        cached = self._whitelist_cache.get(f"whitelist:{user_id}")
        if cached is not None:
            return cached
        whitelisted = await self.db.is_whitelisted(user_id)
        self._whitelist_cache.set(f"whitelist:{user_id}", whitelisted)
        return whitelisted

    # ---- 欺诈检测 ----

    async def check_fraud(self, user_id: int) -> bool:
        """检查欺诈数据库。"""
        cached = self._fraud_cache.get(f"fraud:{user_id}")
        if cached is not None:
            return cached

        if self._fraud_list is None or time.time() - self._fraud_loaded_at > 3600:
            try:
                resp = await self.http.get(self.fraud_db_url)
                # 这里 HTTP 客户端返回 JSON，但 fraud.db 实际是文本格式
                # 如果失败则 fallback
                pass
            except Exception:
                pass

            try:
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(self.fraud_db_url)
                    if r.status_code == 200:
                        text = r.text
                        self._fraud_list = [line.strip() for line in text.split("\n") if line.strip()]
                        self._fraud_loaded_at = time.time()
            except Exception as e:
                logger.error("fraud_db_fetch_failed", {"error": str(e)})
                self._fraud_list = []

        if self._fraud_list is None:
            self._fraud_list = []

        is_fraud = str(user_id) in self._fraud_list
        if is_fraud:
            logger.warn("fraud_hit", {"user_id": user_id})
        self._fraud_cache.set(f"fraud:{user_id}", is_fraud)
        return is_fraud

    # ---- 垃圾过滤 ----

    async def get_spam_rules(self) -> Dict[str, Any]:
        """获取垃圾过滤规则。"""
        raw = await self.db.get_config("spam_rules", "")
        if not raw:
            return dict(DEFAULT_SPAM_RULES)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return dict(DEFAULT_SPAM_RULES)

    async def set_spam_rules(self, rules: Dict[str, Any]) -> None:
        """设置垃圾过滤规则。"""
        await self.db.set_config("spam_rules", json.dumps(rules, ensure_ascii=False))

    async def reset_spam_rules(self) -> Dict[str, Any]:
        """重置为默认规则。"""
        await self.set_spam_rules(DEFAULT_SPAM_RULES)
        return dict(DEFAULT_SPAM_RULES)

    async def add_spam_keyword(self, keyword: str) -> bool:
        """增加垃圾拦截关键词；已存在时返回 False。"""
        keyword = keyword.strip()
        if not keyword:
            return False
        rules = await self.get_spam_rules()
        keywords = list(rules.get("keywords", []))
        if any(item.lower() == keyword.lower() for item in keywords):
            return False
        keywords.append(keyword)
        rules["keywords"] = keywords
        await self.set_spam_rules(rules)
        return True

    async def remove_spam_keyword(self, keyword: str) -> bool:
        """删除垃圾拦截关键词；不存在时返回 False。"""
        keyword = keyword.strip()
        if not keyword:
            return False
        rules = await self.get_spam_rules()
        keywords = list(rules.get("keywords", []))
        new_keywords = [item for item in keywords if item.lower() != keyword.lower()]
        if len(new_keywords) == len(keywords):
            return False
        rules["keywords"] = new_keywords
        await self.set_spam_rules(rules)
        return True

    async def is_spam_enabled(self) -> bool:
        """检查垃圾过滤是否启用。"""
        val = await self.db.get_config("spam_filter_enabled", "1")
        return val not in ("0", "false")

    async def set_spam_enabled(self, enabled: bool) -> None:
        """设置垃圾过滤开关。"""
        await self.db.set_config("spam_filter_enabled", "1" if enabled else "0")

    def count_links(self, text: str) -> int:
        """统计文本中的链接数量。"""
        if not text:
            return 0
        pattern = re.compile(r'(https?://[^\s]+|t\.me/\w+|telegram\.me/\w+)', re.IGNORECASE)
        return len(pattern.findall(text))

    async def check_spam(self, message: Message) -> Dict[str, Any]:
        """检查消息是否为垃圾消息。"""
        if not await self.is_spam_enabled():
            return {"is_spam": False, "reason": None}

        rules = await self.get_spam_rules()
        text = message.text or message.caption or ""

        if not text:
            return {"is_spam": False, "reason": None}

        lower_text = text.lower()

        # 1. 放行关键词
        for kw in rules.get("allowKeywords", []):
            if kw.lower() in lower_text:
                return {"is_spam": False, "reason": None}

        # 2. 放行正则
        for regex_str in rules.get("allowRegexes", []):
            try:
                if re.search(regex_str, text, re.IGNORECASE):
                    return {"is_spam": False, "reason": None}
            except re.error:
                pass

        # 3. 链接数量
        max_links = rules.get("maxLinks", 3)
        link_count = self.count_links(text)
        if max_links > 0 and link_count >= max_links:
            return {"is_spam": True, "reason": f"链接过多 ({link_count}/{max_links})"}

        # 4. 关键词
        for kw in rules.get("keywords", []):
            if kw.lower() in lower_text:
                return {"is_spam": True, "reason": f"命中关键词: {kw}"}

        # 5. 正则
        for regex_str in rules.get("regexes", []):
            try:
                if re.search(regex_str, text, re.IGNORECASE):
                    return {"is_spam": True, "reason": "命中正则规则"}
            except re.error:
                pass

        return {"is_spam": False, "reason": None}
