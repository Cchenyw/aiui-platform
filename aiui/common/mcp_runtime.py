# -*- coding: utf-8 -*-
"""MCP 连接运行时：平台探测工具与执行机执行任务共用。

支持三种传输：
  · stdio  —— 命令 + 参数 + 环境变量（内置 MCP、官方的 Playwright MCP 都是这种）
  · sse    —— 老式 HTTP/SSE MCP
  · http   —— streamable-http MCP

配置里的占位符由本模块统一替换，避免把本机路径写死进数据库：
  {python}       -> 当前解释器
  {builtin_mcp}  -> aiui/toolservers/builtin_browser.py
  {here}         -> 项目根目录（stdio 子进程的 cwd）
"""
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import AsyncGenerator

from aiui.common.paths import ROOT

HERE = ROOT


def describe_exc(e):
    """把 ExceptionGroup / 普通异常压成一行可读文本。"""
    if isinstance(e, BaseExceptionGroup):
        parts = [describe_exc(x) for x in e.exceptions]
        return " | ".join(p for p in parts if p) or type(e).__name__
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def _subst(v, here):
    if not isinstance(v, str):
        return v
    return (v.replace("{python}", sys.executable)
             .replace("{builtin_mcp}", str(ROOT / "aiui" / "toolservers" / "builtin_browser.py"))
             .replace("{here}", str(here)))


def resolve_mcp(mcp, here=ROOT):
    """深拷贝并替换占位符；返回可直接建连的配置。"""
    if not mcp:
        mcp = {"id": "builtin-browser", "transport": "stdio", "command": "{python}",
               "args": ["-m", "aiui.toolservers.builtin_browser"], "env": {}}
    out = {
        "id": mcp.get("id", ""),
        "name": mcp.get("name", mcp.get("id", "mcp")),
        "transport": (mcp.get("transport") or "stdio").lower(),
        "command": _subst(mcp.get("command", ""), here),
        "args": [_subst(a, here) for a in (mcp.get("args") or [])],
        "env": {str(k): _subst(str(v), here) for k, v in (mcp.get("env") or {}).items()},
        "url": _subst(mcp.get("url", ""), here),
        "headers": {str(k): _subst(str(v), here) for k, v in (mcp.get("headers") or {}).items()},
    }
    return out


@contextlib.asynccontextmanager
async def open_session(mcp, here=ROOT, errlog=None) -> AsyncGenerator:
    """建立 MCP 会话并 initialize。调用方负责在 async with 内使用。"""
    from mcp import ClientSession
    cfg = resolve_mcp(mcp, here)
    transport = cfg["transport"]

    if transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        env = os.environ.copy()
        env.update(cfg["env"])
        # 让内置 Python MCP 的 stderr 也按 UTF-8 写，便于日志按 UTF-8 解码
        env.setdefault("PYTHONIOENCODING", "utf-8")
        params = StdioServerParameters(
            command=cfg["command"], args=cfg["args"], env=env,
            cwd=str(Path(here)), encoding="utf-8", encoding_error_handler="replace",
        )
        ctx = stdio_client(params, errlog=errlog or sys.stderr)
    elif transport == "sse":
        from mcp.client.sse import sse_client
        ctx = sse_client(cfg["url"], headers=cfg["headers"])
    elif transport in ("http", "streamable-http", "streamable_http"):
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        client = create_mcp_http_client(headers=cfg["headers"] or None)
        ctx = streamable_http_client(cfg["url"], http_client=client)
    else:
        raise ValueError(f"不支持的 MCP 传输: {transport}")

    async with ctx as streams:
        # 不同 SDK 版本 sse/http 可能返回二元组或 (read, write, get_session_id)
        if isinstance(streams, tuple) and len(streams) >= 2:
            read, write = streams[0], streams[1]
        else:  # pragma: no cover - 防御式兜底
            raise RuntimeError(f"无法识别的 MCP 传输返回: {type(streams)!r}")
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def content_to_text(result):
    """把 CallToolResult.content 拍平成文本；图片等非文本用占位符表示。"""
    parts = []
    for item in (getattr(result, "content", None) or []):
        t = getattr(item, "text", None)
        if t is not None:
            parts.append(t)
            continue
        typ = getattr(item, "type", "unknown")
        if typ == "image":
            mime = getattr(item, "mime_type", getattr(item, "mimeType", "image"))
            parts.append(f"[图片 {mime}]")
        else:
            parts.append(f"[{typ}]")
    text = "\n".join(parts)
    if not text and getattr(result, "structured_content", None) is not None:
        text = json.dumps(result.structured_content, ensure_ascii=False)
    return text


async def call_tool(session, tool, args=None, timeout=60.0):
    """调用工具并返回 (text, is_error)；超时单独抛出。"""
    result = await asyncio.wait_for(
        session.call_tool(tool, args or {}, read_timeout_seconds=timeout),
        timeout=timeout,
    )
    return content_to_text(result), bool(getattr(result, "is_error", False))


async def probe_tools_async(mcp, here=ROOT, timeout=45.0):
    """连接 MCP 并列出工具。返回 (tools, error)。"""
    try:
        async def run():
            async with open_session(mcp, here) as session:
                res = await session.list_tools()
                tools = []
                for t in res.tools:
                    schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
                    tools.append({
                        "name": t.name,
                        "description": (t.description or "")[:500],
                        "input_schema": schema,
                    })
                return tools
        tools = await asyncio.wait_for(run(), timeout=timeout)
        return tools, ""
    except Exception as e:
        return [], describe_exc(e)


def probe_tools(mcp, here=ROOT, timeout=45.0):
    """同步入口（平台 Handler 线程里调用）。"""
    return asyncio.run(probe_tools_async(mcp, here, timeout))
