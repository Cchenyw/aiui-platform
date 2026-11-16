# -*- coding: utf-8 -*-
"""执行环境 Profile：storage state 的校验、注入与采集。

首期只支持 Playwright storage state（cookies + localStorage）：
  · builtin-browser：通过 AIUI_PROFILE_STATE 环境变量在 new_context 时加载，
    通过内部工具 profile_capture 采集；
  · 官方 playwright-mcp：通过 --isolated --storage-state 启动参数加载，
    通过 browser_run_code_unsafe 调用 page.context().storageState() 采集。

Profile 原文是敏感数据：本模块只返回状态和摘要，调用方不得把它写日志、入库或
放进 AI 工具上下文。
"""
import hashlib
import json

from aiui.common import mcp_runtime

MAX_STATE_BYTES = 1024 * 1024
SCHEMA_VERSION = 1

BUILTIN_CAPTURE_TOOL = "profile_capture"
PLAYWRIGHT_CAPTURE_TOOL = "browser_run_code_unsafe"
PLAYWRIGHT_CAPTURE_CODE = "async (page) => await page.context().storageState()"
SENSITIVE_TOOLS = {BUILTIN_CAPTURE_TOOL, PLAYWRIGHT_CAPTURE_TOOL}


def detect_adapter(mcp):
    """根据 MCP 启动配置识别 Profile 适配器；返回 builtin / playwright / None。"""
    mcp = mcp or {}
    args = " ".join(str(a) for a in (mcp.get("args") or []))
    command = str(mcp.get("command") or "")
    joined = f"{command} {args}"
    if "aiui.toolservers.builtin_browser" in joined or mcp.get("id") == "builtin-browser":
        return "builtin"
    if "@playwright/mcp" in joined:
        return "playwright"
    return None


def adapter_label(mcp):
    return {"builtin": "内置浏览器 MCP", "playwright": "官方 Playwright MCP"}.get(
        detect_adapter(mcp), "不支持")


def injectable(mcp):
    """当前 MCP 是否支持启动时注入 Profile。"""
    return detect_adapter(mcp) is not None


def apply_to_mcp(mcp, state_path):
    """返回带 Profile 注入信息的新 MCP 配置，不修改入参。

    调用方负责保证 state_path 指向仅包含 Playwright storage state 的文件，
    并在 MCP 会话结束后清理。
    """
    adapter = detect_adapter(mcp)
    if not adapter:
        raise ValueError("当前 MCP 不支持执行环境 Profile 注入")
    out = dict(mcp or {})
    out["args"] = list((mcp or {}).get("args") or [])
    out["env"] = dict((mcp or {}).get("env") or {})
    if adapter == "builtin":
        out["env"]["AIUI_PROFILE_STATE"] = str(state_path)
        return out

    # 官方 Playwright MCP：移除旧的 storage-state 参数，再追加本次快照。
    cleaned = []
    skip_next = False
    for arg in out["args"]:
        if skip_next:
            skip_next = False
            continue
        text = str(arg)
        if text == "--isolated":
            continue
        if text == "--storage-state":
            skip_next = True
            continue
        if text.startswith("--storage-state="):
            continue
        cleaned.append(arg)
    cleaned.extend(["--isolated", "--storage-state", str(state_path)])
    out["args"] = cleaned
    return out


def validate_state(state):
    """校验并规范化 storage state；返回 (state, summary)，失败抛 ValueError。"""
    if not isinstance(state, dict):
        raise ValueError("storage state 必须是 JSON 对象")
    cookies = state.get("cookies")
    origins = state.get("origins")
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise ValueError("storage state 必须包含 cookies 和 origins 数组")
    for cookie in cookies:
        if (not isinstance(cookie, dict) or not isinstance(cookie.get("name"), str)
                or not cookie.get("name") or not isinstance(cookie.get("domain"), str)):
            raise ValueError("cookies 中存在无效条目")
        if "value" in cookie and not isinstance(cookie["value"], str):
            raise ValueError("cookie value 必须是字符串")
    ls_count = 0
    origin_names = set()
    for entry in origins:
        if (not isinstance(entry, dict) or not isinstance(entry.get("origin"), str)
                or not isinstance(entry.get("localStorage", []), list)):
            raise ValueError("origins 中存在无效条目")
        origin_names.add(entry["origin"])
        for item in entry.get("localStorage") or []:
            if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                    or not item.get("name")):
                raise ValueError("localStorage 中存在无效条目")
            if "value" in item and not isinstance(item["value"], str):
                raise ValueError("localStorage value 必须是字符串")
            ls_count += 1
    if not cookies and not ls_count:
        raise ValueError("storage state 为空，未捕获到 cookies 或 localStorage")
    encoded = _canonical(state).encode("utf-8")
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError(f"storage state 超过 {MAX_STATE_BYTES} 字节限制")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "cookie_count": len(cookies),
        "origin_count": len(origin_names),
        "local_storage_keys": ls_count,
        "origins": sorted(origin_names),
        "size_bytes": len(encoded),
    }
    return state, summary


def state_hash(state):
    return hashlib.sha256(_canonical(state).encode("utf-8")).hexdigest()


def _canonical(state):
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def capture(session, tools):
    """从当前 MCP 会话采集 storage state；失败抛 RuntimeError。"""
    names = {getattr(t, "name", "") for t in tools}
    if BUILTIN_CAPTURE_TOOL in names:
        text, is_error = await mcp_runtime.call_tool(
            session, BUILTIN_CAPTURE_TOOL, {}, timeout=30)
        if is_error:
            raise RuntimeError(text or "profile_capture 执行失败")
        obj = _first_json(text)
        if not isinstance(obj, dict):
            raise RuntimeError("profile_capture 返回的不是合法 JSON")
        if obj.get("error") or obj.get("ok") is False:
            raise RuntimeError(str(obj.get("error") or "profile_capture 采集失败")[:500])
        state = obj.get("storage_state", obj)
        if not isinstance(state, dict):
            raise RuntimeError("profile_capture 未返回 storage_state")
        return state
    if PLAYWRIGHT_CAPTURE_TOOL in names:
        text, is_error = await mcp_runtime.call_tool(
            session, PLAYWRIGHT_CAPTURE_TOOL,
            {"code": PLAYWRIGHT_CAPTURE_CODE}, timeout=30)
        if is_error:
            raise RuntimeError(text or "browser_run_code_unsafe 执行失败")
        obj = _first_json(text, required=("cookies", "origins"))
        if not isinstance(obj, dict):
            raise RuntimeError("未能从 Playwright MCP 返回中解析 storage state")
        return obj
    raise RuntimeError(
        "当前 MCP 不支持环境采集（缺少 profile_capture / browser_run_code_unsafe）")


def _first_json(text, required=()):
    """从工具返回文本中提取第一个满足 required 的 JSON 对象。"""
    text = text or ""
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        start = text.find("{", pos)
        if start < 0:
            return None
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            pos = start + 1
            continue
        if isinstance(obj, dict) and all(k in obj for k in required):
            return obj
        pos = end
    return None
