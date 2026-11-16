# -*- coding: utf-8 -*-
"""
效果图采集：起服务 → 下发 12 条用例 → 抓「执行中」与「已完成」两张看板截图 → 关掉全部进程
用法：python scripts/shot_dashboard.py
输出：artifacts/dashboard_running.png、artifacts/dashboard_done.png
"""
import glob
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
PY = sys.executable
APP, PLAT, AGENT = 8081, 8000, 8001
BASE = f"http://127.0.0.1:{APP}"


def post(path, payload, port=PLAT):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode() or "{}")


def get(path, port=PLAT):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read().decode() or "{}")


def wait_port(port, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def case(name, user="admin", pwd="123456", expect="登录成功"):
    return {"name": name, "timeout_s": 60, "steps": [
        {"tool": "navigate", "args": {"url": BASE + "/login"}},
        {"tool": "type_text", "args": {"selector": "#username", "text": user}},
        {"tool": "type_text", "args": {"selector": "#password", "text": pwd}},
        {"tool": "click", "args": {"selector": "#submit"}},
        {"tool": "expect_text", "args": {"text": expect, "timeout_ms": 4000}},
    ]}


CASES = [case("登录-管理员"), case("登录-普通用户", user="tester"),
         case("登录-密码错误", pwd="000000", expect="密码错误"),
         case("登录-空用户名", user="", expect="请输入用户名"),
         case("登录-再次验证", user="qa01"), case("登录-采集正文", user="ops"),
         case("登录-不同文案", user="guest"), case("登录-回归-1", user="r1"),
         case("登录-回归-2", user="r2"), case("登录-回归-3", user="r3"),
         {"name": "异常-元素不存在(预期失败)", "timeout_s": 60, "steps": [
             {"tool": "navigate", "args": {"url": BASE + "/login"}},
             {"tool": "click", "args": {"selector": "#does-not-exist"}}]}]


def main():
    procs = []
    try:
        procs.append(subprocess.Popen([PY, "-m", "aiui.testapp.app", "--port", str(APP)], cwd=str(ROOT),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_port(APP)
        procs.append(subprocess.Popen([PY, "-m", "aiui.platform.server", "--port", str(PLAT)], cwd=str(ROOT),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_port(PLAT)
        procs.append(subprocess.Popen([PY, "-m", "aiui.agent.runner",
                                       "--platform", f"http://127.0.0.1:{PLAT}",
                                       "--port", str(AGENT), "--slots", "2",
                                       "--agent-id", "agent-local-1"], cwd=str(ROOT),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_port(AGENT)
        time.sleep(1.2)

        post("/api/tasks", {"cases": CASES, "priority": 5, "max_retry": 2})

        from playwright.sync_api import sync_playwright
        bins = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")))
        kw = {"executable_path": bins[-1]} if bins else {}
        with sync_playwright() as pw:
            b = pw.chromium.launch(**kw)
            page = b.new_page(viewport={"width": 1240, "height": 980}, device_scale_factor=2)
            # 看板每 2s 自动刷新，刷新会销毁执行上下文导致 evaluate 报错。
            # 做法：加载后 1.2s 内先摘掉 <meta refresh>，再截图；两次截图各自重新 goto。
            def shoot(path, wait=1.2):
                page.goto(f"http://127.0.0.1:{PLAT}/", wait_until="domcontentloaded")
                time.sleep(wait)
                try:
                    page.evaluate("document.querySelector('meta[http-equiv=refresh]')?.remove();")
                except Exception:
                    pass
                page.screenshot(path=path, full_page=True)

            shoot(str(ARTIFACTS / "dashboard_running.png"), 1.4)
            print("已抓：执行中状态")

            t0 = time.time()
            while time.time() - t0 < 120:
                s2 = get("/api/status")
                if s2["finished"] >= s2["total"]:
                    break
                time.sleep(1)
            time.sleep(0.8)
            shoot(str(ARTIFACTS / "dashboard_done.png"), 1.2)
            print("已抓：完成状态")
            b.close()

        s = get("/api/status")
        c = s["counts"]
        print(f"结果：通过 {c.get('done',0)} / 失败 {c.get('failed',0)} / 总计 {s['total']}")
    finally:
        for p in reversed(procs):
            if p.poll() is None:
                p.terminate()
        for p in reversed(procs):
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()
        time.sleep(0.6)
        left = []
        for pt in (APP, PLAT, AGENT):
            try:
                with socket.create_connection(("127.0.0.1", pt), timeout=0.4):
                    left.append(pt)
            except OSError:
                pass
        print("端口状态: " + "  ".join(
            f"{pt}={'仍占用' if pt in left else '已释放'}" for pt in (APP, PLAT, AGENT)))


if __name__ == "__main__":
    main()
