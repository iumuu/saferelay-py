"""验证服务 — 本地题库验证。

只保留本地题库，移除 Turnstile。
"""

import json
import random
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from core.bot import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from core.database import Database
from core.http import HttpClient
from core.logger import get_logger

logger = get_logger("services.verify")

# 15 道题目（与原始 JS 一致）
LOCAL_QUIZ_QUESTIONS: List[Dict[str, Any]] = [
    {"q": "冰融化后会变成什么？", "opts": ["水", "石头", "木头", "火"], "a": 0},
    {"q": "正常人有几只眼睛？", "opts": ["1", "2", "3", "4"], "a": 1},
    {"q": "以下哪个属于水果？", "opts": ["白菜", "香蕉", "猪肉", "大米"], "a": 1},
    {"q": "1 加 2 等于几？", "opts": ["2", "3", "4", "5"], "a": 1},
    {"q": "5 减 2 等于几？", "opts": ["1", "2", "3", "4"], "a": 2},
    {"q": "2 乘以 3 等于几？", "opts": ["4", "5", "6", "7"], "a": 2},
    {"q": "10 加 5 等于几？", "opts": ["10", "12", "15", "20"], "a": 2},
    {"q": "8 减 4 等于几？", "opts": ["2", "3", "4", "5"], "a": 2},
    {"q": "在天上飞的交通工具是什么？", "opts": ["汽车", "轮船", "飞机", "自行车"], "a": 2},
    {"q": "星期一的后面是星期几？", "opts": ["星期日", "星期五", "星期二", "星期三"], "a": 2},
    {"q": "鱼通常生活在哪里？", "opts": ["树上", "土里", "水里", "火里"], "a": 2},
    {"q": "我们用什么器官来听声音？", "opts": ["眼睛", "鼻子", "耳朵", "嘴巴"], "a": 2},
    {"q": "晴朗的天空通常是什么颜色的？", "opts": ["绿色", "红色", "蓝色", "紫色"], "a": 2},
    {"q": "太阳从哪个方向升起？", "opts": ["西方", "南方", "东方", "北方"], "a": 2},
    {"q": "小狗发出的叫声通常是？", "opts": ["喵喵", "咩咩", "汪汪", "呱呱"], "a": 2},
]

# 验证配置
CHALLENGE_TTL = 60       # 单题有效期 60 秒
TRIGGER_WINDOW = 300     # 5 分钟窗口
TRIGGER_LIMIT = 3        # 5 分钟最多触发 3 次
MAX_ATTEMPTS = 3         # 每题最多尝试 3 次
VERIFICATION_TTL = 604800  # 验证有效期 7 天


class VerifyService:
    """验证服务 — 本地题库验证。"""

    def __init__(
        self,
        db: Database,
        bot: Bot,
        http: Optional[HttpClient] = None,
        provider: str = "quiz",
        hcaptcha_site_key: str = "",
        hcaptcha_secret: str = "",
        hcaptcha_webapp_url: str = "",
        hcaptcha_verify_url: str = "https://api.hcaptcha.com/siteverify",
    ):
        self.db = db
        self.bot = bot
        self.http = http
        self.provider = provider
        self.hcaptcha_site_key = hcaptcha_site_key
        self.hcaptcha_secret = hcaptcha_secret
        self.hcaptcha_webapp_url = hcaptcha_webapp_url
        self.hcaptcha_verify_url = hcaptcha_verify_url
        # 内存中存储活跃验证挑战
        self._challenges: Dict[str, Dict[str, Any]] = {}

    def is_hcaptcha_enabled(self) -> bool:
        """Return True when hCaptcha should replace the local quiz."""
        return (
            self.provider == "hcaptcha"
            and bool(self.hcaptcha_site_key)
            and bool(self.hcaptcha_secret)
            and bool(self.hcaptcha_webapp_url)
        )

    @staticmethod
    def generate_keyboard(question: Dict[str, Any]) -> InlineKeyboardMarkup:
        """生成题目 Inline 键盘。"""
        buttons = [
            InlineKeyboardButton(text=opt, callback_data=f"quiz_answer:{idx}")
            for idx, opt in enumerate(question["opts"])
        ]
        # 每行 2 个
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def generate_hcaptcha_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        """Build a Telegram Web App button for the configured hCaptcha page."""
        params = urllib.parse.urlencode({
            "uid": str(user_id),
            "sitekey": self.hcaptcha_site_key,
        })
        sep = "&" if "?" in self.hcaptcha_webapp_url else "?"
        url = f"{self.hcaptcha_webapp_url}{sep}{params}"
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="完成人机验证",
                web_app=WebAppInfo(url=url),
            )
        ]])

    @staticmethod
    def extract_hcaptcha_token(payload: str) -> str:
        """Extract hCaptcha token from Telegram Web App data."""
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError:
            return ""
        token = data.get("hcaptcha_token") or data.get("token") or data.get("response")
        return str(token).strip() if token else ""

    async def verify_hcaptcha_token(self, token: str, remote_ip: str = "") -> Dict[str, Any]:
        """Verify an hCaptcha response token against hCaptcha siteverify."""
        if not token:
            return {"success": False, "reason": "missing_token", "message": "验证令牌为空，请重试"}
        if not self.http:
            return {"success": False, "reason": "http_unavailable", "message": "验证服务不可用，请稍后重试"}

        payload: Dict[str, Any] = {
            "secret": self.hcaptcha_secret,
            "response": token,
        }
        if self.hcaptcha_site_key:
            payload["sitekey"] = self.hcaptcha_site_key
        if remote_ip:
            payload["remoteip"] = remote_ip

        try:
            result = await self.http.post_form(self.hcaptcha_verify_url, payload)
        except Exception as exc:
            logger.error("hcaptcha_verify_failed", {"error": str(exc)})
            return {"success": False, "reason": "verify_error", "message": "验证服务异常，请稍后重试"}

        if result.get("success") is True:
            return {"success": True}

        return {
            "success": False,
            "reason": "invalid_token",
            "message": "人机验证失败，请重新验证",
            "error_codes": result.get("error-codes", []),
        }

    def create_challenge(self, user_id: int) -> Tuple[str, Dict[str, Any]]:
        """创建新的验证挑战，返回 (challenge_id, question)。"""
        logger.info("create_challenge", {"user_id": user_id})
        question = random.choice(LOCAL_QUIZ_QUESTIONS)
        challenge = {
            "question": question,
            "correct_answer": question["a"],
            "attempts": 0,
            "created_at": __import__("time").time(),
        }
        challenge_id = f"quiz_{user_id}_{int(__import__('time').time())}"
        self._challenges[f"quiz:{user_id}"] = challenge
        return challenge_id, question

    def get_challenge(self, user_id: int) -> Optional[Dict[str, Any]]:
        """获取当前验证挑战。"""
        return self._challenges.get(f"quiz:{user_id}")

    def delete_challenge(self, user_id: int) -> None:
        """删除验证挑战。"""
        self._challenges.pop(f"quiz:{user_id}", None)

    def verify_answer(self, user_id: int, answer_index: int) -> Dict[str, Any]:
        """验证答案。

        Returns:
            dict: {"success": bool, "reason": str, "message": str}
        """
        challenge = self.get_challenge(user_id)
        if not challenge:
            logger.info("verify_failed", {"user_id": user_id, "reason": "expired"})
            return {"success": False, "reason": "expired", "message": "验证已过期，请重新获取题目"}

        if challenge["attempts"] >= MAX_ATTEMPTS:
            self.delete_challenge(user_id)
            logger.info("verify_failed", {"user_id": user_id, "reason": "max_attempts"})
            return {"success": False, "reason": "max_attempts", "message": "尝试次数过多，请重新获取题目"}

        challenge["attempts"] += 1

        if answer_index == challenge["correct_answer"]:
            self.delete_challenge(user_id)
            logger.info("verify_success", {"user_id": user_id})
            return {"success": True}

        remaining = MAX_ATTEMPTS - challenge["attempts"]
        logger.info("verify_failed", {"user_id": user_id, "reason": "wrong_answer", "remaining": remaining})
        return {
            "success": False,
            "reason": "wrong_answer",
            "message": f"答案错误，还剩 {remaining} 次机会",
            "remaining": remaining,
        }

    async def check_trigger_limit(self, user_id: int) -> bool:
        """检查触发频率限制，返回是否允许。"""
        key = f"quiz_trigger:{user_id}"
        return await self.db.check_rate_limit(key, TRIGGER_WINDOW * 1000, TRIGGER_LIMIT)
