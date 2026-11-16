# -*- coding: utf-8 -*-
"""本地 Mock LLM：仅用于验证 AI 测试 Agent 的规划/观察/判定链路。

它实现 OpenAI 兼容的 POST /v1/chat/completions（也支持 Anthropic 的 /v1/messages），
但不具备真实语言理解能力：按固定的登录场景返回动作序列，并根据页面观察结果
返回 passed / bug_suspected。真实演示请配置 runtime/llm.json 指向真实模型。

用法：python scripts/mock_llm_server.py --port 8099
"""
import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "mock-llm"


def _text_of(msg):
    content = msg.get("content", "")
    if isinstance(content, list):
        return "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content)
    return str(content or "")


def _planner_decision(messages):
    assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
    user_msgs = [m for m in messages if m.get("role") == "user"]
    first_user = _text_of(user_msgs[0]) if user_msgs else ""
    last_user = _text_of(user_msgs[-1]) if user_msgs else ""
    if "登录" not in first_user:
        return {"thought": "Mock LLM 只覆盖登录场景", "action": "finish",
                "verdict": "inconclusive", "reason": "Mock LLM 不支持该场景"}
    urls = re.findall(r"https?://[^\s，。\"'<>]+", first_user)
    url = urls[0] if urls else "http://127.0.0.1:8081/login"
    if assistant_count == 0:
        return {"thought": "先打开目标页面", "action": "tool", "tool": "navigate",
                "args": {"url": url}}
    if assistant_count == 1:
        return {"thought": "观察登录页，确认输入框和按钮", "action": "tool",
                "tool": "snapshot", "args": {}}
    if assistant_count == 2:
        return {"thought": "填写用户名", "action": "tool", "tool": "type_text",
                "args": {"selector": "#username", "text": "admin"}}
    if assistant_count == 3:
        return {"thought": "填写密码", "action": "tool", "tool": "type_text",
                "args": {"selector": "#password", "text": "123456"}}
    if assistant_count == 4:
        return {"thought": "点击登录", "action": "tool", "tool": "click",
                "args": {"selector": "#submit"}}
    if assistant_count == 5:
        return {"thought": "观察点击后的页面", "action": "tool", "tool": "snapshot",
                "args": {}}
    if "登录成功" in last_user:
        evidence = "登录成功" if len(last_user) < 200 else last_user[:300]
        return {"thought": "页面出现了预期文案", "action": "finish", "verdict": "passed",
                "reason": "点击登录后页面显示登录成功", "evidence": evidence}
    ev = last_user[:400]
    return {"thought": "登录后没有出现预期文案", "action": "finish",
            "verdict": "bug_suspected",
            "reason": "使用正确凭据登录后未出现“登录成功”；页面表现为异常提示",
            "evidence": ev}


def _judge_decision(user_text):
    record = user_text.split("执行记录：", 1)[-1]
    if "系统内部错误" in record:
        return {"verdict": "bug_suspected", "confidence": 0.86,
                "reason": "执行记录显示正确凭据登录后页面返回系统内部错误，未出现预期成功页",
                "evidence": "系统内部错误，请稍后重试"}
    if "登录成功" in record:
        return {"verdict": "passed", "confidence": 0.95,
                "reason": "执行记录中观察到登录成功文案，符合预期",
                "evidence": "登录成功"}
    return {"verdict": "inconclusive", "confidence": 0.2,
            "reason": "Mock 判定器没有在记录中找到成功或明确的错误证据",
            "evidence": record[:300]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            body = {}
        anthropic = "messages" in self.path
        system = body.get("system", "") if anthropic else ""
        messages = body.get("messages") or []
        if not anthropic:
            for m in messages:
                if m.get("role") == "system":
                    system = _text_of(m)
                    break
        judge = "测试验收判定器" in system
        if judge:
            user_text = _text_of(messages[-1]) if messages else ""
            decision = _judge_decision(user_text)
        else:
            decision = _planner_decision(messages)
        content = json.dumps(decision, ensure_ascii=False)
        print(f"[mock-llm] {'judge' if judge else 'planner'} -> "
              f"{decision.get('action') or decision.get('verdict')}", flush=True)
        if anthropic:
            resp = {
                "id": "mock-msg", "type": "message", "role": "assistant", "model": MODEL,
                "content": [{"type": "text", "text": content}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        else:
            resp = {
                "id": "mock-completion", "object": "chat.completion",
                "created": int(time.time()), "model": MODEL,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"[mock-llm] listening http://{a.host}:{a.port}/v1/chat/completions", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
