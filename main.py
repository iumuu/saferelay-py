"""SafeRelay-Py 主入口。

创建数据库、Bot 实例，注册所有 handler，启动 bot。
"""
import asyncio
import sys

from pyrogram import idle

import config as cfg
from core.bot import Bot, ParseMode
from core.database import Database
from core.http import HttpClient
from core.logger import get_logger, init_logger
from services.forward import ForwardService
from services.hcaptcha_webapp import HcaptchaWebAppServer

logger = get_logger("main")


async def amain() -> None:
    """异步主函数：初始化依赖，注册 handler，启动 bot。"""
    # 验证配置
    err = cfg.config.validate()
    if err:
        print(f"[ERROR] 配置错误: {err}")
        sys.exit(1)

    # 初始化日志（文件 + 控制台）
    init_logger()

    logger.info("starting_saferelay", {"admin_count": len(cfg.config.admin_ids)})

    # 初始化数据库
    db = Database()
    await db.init()
    logger.info("db_initialized")

    protect_default = "1" if cfg.config.protect_user_content else "0"
    if await db.get_config("protect_user_content", "") == "":
        await db.set_config("protect_user_content", protect_default)

    async def _get_protect_user_content() -> bool:
        value = await db.get_config("protect_user_content", protect_default)
        return value not in ("0", "false")

    # 初始化 HTTP 客户端
    http = HttpClient()
    hcaptcha_webapp = None

    # 初始化 Bot
    proxy_cfg = None
    if cfg.config.proxy_enabled:
        proxy_cfg = {
            "scheme": cfg.config.proxy_scheme,
            "hostname": cfg.config.proxy_host,
            "port": cfg.config.proxy_port,
        }
    bot = Bot(
        bot_token=cfg.config.bot_token,
        api_id=cfg.config.api_id,
        api_hash=cfg.config.api_hash,
        proxy=proxy_cfg,
        protect_user_content=cfg.config.protect_user_content,
        protect_user_content_getter=_get_protect_user_content,
    )

    # 导入并注册 handler（延迟导入避免循环依赖）
    from services.forward import ForwardService
    from services.hcaptcha_webapp import HcaptchaWebAppServer
    from services.security import SecurityService
    from services.stats import StatsService
    from services.verify import VerifyService

    forward_svc = ForwardService(
        db=db, bot=bot, admin_ids=cfg.config.admin_ids, group_id=cfg.config.group_id,
        protect_user_content=cfg.config.protect_user_content,
    )
    verify_provider = await db.get_config("verify_provider", cfg.config.verify_provider)
    if verify_provider not in ("quiz", "hcaptcha"):
        verify_provider = cfg.config.verify_provider
    await db.set_config("verify_provider", verify_provider)

    verify_svc = VerifyService(
        db=db,
        bot=bot,
        http=http,
        provider=verify_provider,
        hcaptcha_site_key=cfg.config.hcaptcha_site_key,
        hcaptcha_secret=cfg.config.hcaptcha_secret,
        hcaptcha_webapp_url=cfg.config.hcaptcha_webapp_url,
        hcaptcha_verify_url=cfg.config.hcaptcha_verify_url,
    )
    if cfg.config.hcaptcha_site_key and cfg.config.hcaptcha_webapp_url:
        hcaptcha_webapp = HcaptchaWebAppServer(
            site_key=cfg.config.hcaptcha_site_key,
            port=cfg.config.hcaptcha_webapp_port,
        )
        hcaptcha_webapp.start()
    security_svc = SecurityService(
        db=db, bot=bot, http=http,
        admin_ids=cfg.config.admin_ids, admin_uid=cfg.config.admin_uid,
    )
    stats_svc = StatsService(db=db)

    # 注册 handler
    from handlers import user, admin, callback
    user.register(bot, db, forward_svc, verify_svc, security_svc, stats_svc)
    admin.register(bot, db, forward_svc, security_svc, stats_svc, admin_ids=cfg.config.admin_ids)
    callback.register(bot, db, forward_svc, verify_svc, security_svc, stats_svc)

    logger.info("all_handlers_registered")
    print("[INFO] SafeRelay-Py 启动完成，等待消息...")

    try:
        # 启动 bot 并保持运行
        await bot.start()

        # 发送启动通知给所有管理员
        from datetime import datetime
        from zoneinfo import ZoneInfo
        beijing_time = datetime.now(ZoneInfo("Asia/Shanghai"))
        startup_msg = (
            f"✅ <b>SafeRelay 已启动</b>\n\n"
            f"⏰ 北京时间：<code>{beijing_time.strftime('%Y-%m-%d %H:%M:%S')}</code>"
        )
        for admin_id in cfg.config.admin_ids:
            try:
                await bot.send_message(admin_id, startup_msg, parse_mode=ParseMode.HTML)
                logger.info("startup_notify_sent", {"admin_id": admin_id})
            except Exception as e:
                logger.error("startup_notify_failed", {"admin_id": admin_id, "error": str(e)})

        # 注册 Bot 命令列表（需启动后）
        await bot.set_commands(cfg.config.admin_ids, cfg.config.group_id)
        await idle()
    finally:
        await bot.stop()
        await db.close()
        await http.close()
        if hcaptcha_webapp:
            hcaptcha_webapp.stop()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("[INFO] 收到退出信号")
    except Exception as e:
        print(f"[ERROR] 启动失败: {e}")
        logger.error("startup_failed", {"error": str(e)})
        sys.exit(1)
