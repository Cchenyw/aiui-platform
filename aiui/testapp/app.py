# -*- coding: utf-8 -*-
"""
被测页面（纯标准库，零依赖）
端口默认 8081
"""
import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>示例登录页</title>
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f5f5f5;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
 .card{background:#fff;border:1px solid #e2e0da;border-radius:10px;padding:36px 40px;width:340px}
 h1{font-size:20px;margin:0 0 20px;color:#2d3142}
 label{display:block;font-size:13px;color:#666;margin:12px 0 6px}
 input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #d5d3cc;border-radius:6px;font-size:14px}
 button{margin-top:20px;width:100%;padding:11px;border:0;border-radius:6px;background:#2d3142;
         color:#fff;font-size:15px;cursor:pointer}
 .msg{margin-top:16px;font-size:14px;color:{color}}
</style></head><body>
<div class="card">
  <h1>示例系统登录</h1>
  <form method="post" action="/login">
    <label>用户名</label><input id="username" name="username" placeholder="请输入用户名">
    <label>密码</label><input id="password" name="password" type="password" placeholder="请输入密码">
    <button id="submit" type="submit">登 录</button>
  </form>
  <div class="msg" id="msg">{msg}</div>
</div></body></html>"""

WELCOME = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>欢迎</title>
<style>body{font-family:system-ui,sans-serif;background:#f5f5f5;padding:60px}
h1{color:#2d3142}</style></head><body>
<h1 id="title">登录成功，欢迎 {user}</h1>
<p>这是一个用于演示 AI UI 自动化测试链路的示例页面。</p>
<script>localStorage.setItem('demo_login', '1');</script>
</body></html>"""


def render_page(msg="", color="#c0392b", action="/login"):
    return (PAGE.replace('action="/login"', f'action="{action}"')
            .replace("{msg}", msg).replace("{color}", color))


class Handler(BaseHTTPRequestHandler):
    server_version = "DemoApp/1.0"

    def log_message(self, fmt, *args):          # 静音访问日志
        pass

    def _send(self, body, code=200, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or []):
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _session_user(self):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and key == "session":
                return unquote(value)
        return ""

    def do_GET(self):
        if self.path.startswith("/buggy-login"):
            self._send(render_page(action="/buggy-login"))
        elif self.path.startswith("/login") or self.path == "/":
            self._send(render_page())
        elif self.path.startswith("/welcome"):
            user = self._session_user()
            if not user:
                self._send(render_page("请先登录"))
            else:
                self._send(WELCOME.replace("{user}", html.escape(user)))
        elif self.path == "/health":
            self._send("ok", ctype="text/plain")
        else:
            self._send("404", code=404, ctype="text/plain")

    def do_POST(self):
        buggy = self.path.startswith("/buggy-login")
        if not buggy and not self.path.startswith("/login"):
            self._send("404", code=404, ctype="text/plain")
            return
        action = "/buggy-login" if buggy else "/login"
        n = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(n).decode("utf-8"))
        user = (form.get("username", [""])[0] or "").strip()
        pwd = (form.get("password", [""])[0] or "").strip()
        if not user:
            self._send(render_page("请输入用户名", action=action))
        elif pwd != "123456":
            self._send(render_page("密码错误", action=action))
        elif buggy:
            # 故意的产品缺陷场景：凭据正确却返回系统错误，供 AI 判定演示
            self._send(render_page("系统内部错误，请稍后重试", action=action))
        else:
            self._send(WELCOME.replace("{user}", html.escape(user)),
                       headers=[("Set-Cookie",
                                 f"session={quote(user)}; Path=/; HttpOnly; SameSite=Lax")])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[testapp] 被测页面已启动 http://{a.host}:{a.port}/login", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
