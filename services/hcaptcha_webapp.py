"""Minimal hCaptcha Telegram WebApp server."""

import html
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from core.logger import get_logger

logger = get_logger("services.hcaptcha_webapp")


class HcaptchaWebAppServer:
    """Serve a small Telegram WebApp page that returns hCaptcha token via sendData."""

    def __init__(self, site_key: str, host: str = "0.0.0.0", port: int = 8080):
        self.site_key = site_key
        self.host = host
        self.port = int(port)
        self._server = None
        self._thread = None

    def start(self) -> None:
        if self._server:
            return
        site_key = self.site_key

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.info("hcaptcha_webapp_access", {"message": fmt % args})

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path in ("/health", "/healthz"):
                    body = b"ok"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if parsed.path not in ("/", "/hcaptcha", "/hcaptcha.html"):
                    self.send_response(404)
                    self.end_headers()
                    return

                qs = parse_qs(parsed.query)
                key = (qs.get("sitekey") or [site_key])[0] or site_key
                uid = (qs.get("uid") or [""])[0]
                page = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
  <title>人机验证</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <script src="https://js.hcaptcha.com/1/api.js?onload=onHcaptchaLoad&render=explicit" async defer></script>
  <style>
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#f6f7fb; color:#111; }}
    .box {{ max-width:420px; margin:48px auto; padding:24px; background:#fff; border-radius:16px; box-shadow:0 8px 28px rgba(0,0,0,.08); text-align:center; }}
    h1 {{ font-size:22px; margin:0 0 10px; }}
    p {{ color:#666; line-height:1.6; }}
    #captcha {{ display:flex; justify-content:center; margin:22px 0; }}
    .status {{ font-size:14px; color:#888; }}
    button {{ border:0; border-radius:10px; padding:12px 18px; background:#2481cc; color:white; font-weight:600; }}
  </style>
</head>
<body>
  <div class="box">
    <h1>🛡 人机验证</h1>
    <p>请完成下方 hCaptcha 验证，完成后会自动返回 Telegram。</p>
    <div id="captcha"></div>
    <div id="status" class="status">正在加载验证组件...</div>
    <button id="closeBtn" style="display:none" onclick="Telegram.WebApp.close()">关闭页面</button>
  </div>
  <script>
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg) {{ tg.ready(); tg.expand(); }}
    function setStatus(t) {{ document.getElementById('status').innerText = t; }}
    window.onHcaptchaLoad = function() {{
      setStatus('请完成人机验证');
      hcaptcha.render('captcha', {{
        sitekey: '{html.escape(key)}',
        callback: function(token) {{
          setStatus('验证完成，正在返回 Telegram...');
          const data = JSON.stringify({{ hcaptcha_token: token, uid: '{html.escape(uid)}' }});
          if (tg && tg.sendData) {{
            tg.sendData(data);
            setTimeout(() => tg.close(), 600);
          }} else {{
            setStatus('请在 Telegram 内打开此页面');
            document.getElementById('closeBtn').style.display = 'inline-block';
          }}
        }},
        'error-callback': function() {{ setStatus('验证加载失败，请重试'); }},
        'expired-callback': function() {{ setStatus('验证已过期，请重新完成'); }}
      }});
    }};
  </script>
</body>
</html>'''.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("hcaptcha_webapp_started", {"host": self.host, "port": self.port})

    def stop(self) -> None:
        if not self._server:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        logger.info("hcaptcha_webapp_stopped")
