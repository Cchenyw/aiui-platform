# -*- coding: utf-8 -*-
"""LLM 客户端与配置。

支持两类接口：
  · anthropic   —— /v1/messages（官方或兼容网关）
  · openai      —— /v1/chat/completions（OpenAI / DeepSeek / 通义 / 内网网关等）

配置优先级：
  1. runtime/llm.json（平台 LLM 配置页保存；Agent 与本机共享）
  2. AIUI_LLM_* 环境变量
  3. 标准 OPENAI_* 环境变量（仅当 provider=openai）
  4. ANTHROPIC_* 环境变量仅在显式“从环境导入”后写入配置文件，避免误用外部工具凭证

安全约定：
  · 对外 API/页面只返回掩码后的配置；
  · 错误信息里不拼接 api_key；
  · api_key 只保存在本机 runtime/llm.json（git 忽略）。
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from aiui.common.paths import RUNTIME_DIR, ensure_runtime_dirs

LLM_CONFIG_PATH = RUNTIME_DIR / "llm.json"
DEFAULTS = {
    "provider": "openai",
    "base_url": "",
    "api_key": "",
    "model": "",
    "auth_header": "authorization",
    "max_tokens": 1024,
    "temperature": 0.2,
    "timeout_s": 60,
}


class LLMError(RuntimeError):
    pass


class LLMResponseError(LLMError):
    """非暂时性响应错误（输出被截断等），重试相同参数无意义。"""


def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return ""


def load_llm_config(use_env=True):
    cfg = dict(DEFAULTS)
    if LLM_CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(LLM_CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    if use_env:
        # 只读取本应用专属变量，避免把其他工具的凭证当成默认配置
        for key, names in {
            "provider": ("AIUI_LLM_PROVIDER",),
            "base_url": ("AIUI_LLM_BASE_URL",),
            "api_key": ("AIUI_LLM_API_KEY",),
            "model": ("AIUI_LLM_MODEL",),
            "auth_header": ("AIUI_LLM_AUTH_HEADER",),
        }.items():
            v = _env(*names)
            if v:
                cfg[key] = v
        if cfg.get("provider") == "openai":
            cfg["base_url"] = cfg.get("base_url") or _env("OPENAI_BASE_URL", "OPENAI_API_BASE")
            cfg["api_key"] = cfg.get("api_key") or _env("OPENAI_API_KEY")
            cfg["model"] = cfg.get("model") or _env("OPENAI_MODEL")
    cfg["max_tokens"] = int(cfg.get("max_tokens") or 1024)
    cfg["temperature"] = float(cfg.get("temperature", 0.2))
    cfg["timeout_s"] = float(cfg.get("timeout_s") or 60)
    return cfg


def public_llm_config(cfg=None):
    cfg = cfg or load_llm_config()
    out = dict(cfg)
    key = out.pop("api_key", "") or ""
    out["api_key_set"] = bool(key)
    out["api_key_masked"] = (key[:3] + "…" + key[-2:]) if len(key) > 6 else ("***" if key else "")
    out["configured"] = bool(cfg.get("provider") and cfg.get("base_url") and cfg.get("model") and key)
    return out


def save_llm_config(data):
    ensure_runtime_dirs()
    old = {}
    if LLM_CONFIG_PATH.exists():
        try:
            old = json.loads(LLM_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    cfg = {**DEFAULTS, **old}
    for k in ("provider", "base_url", "model", "auth_header"):
        if k in data and data[k] is not None:
            cfg[k] = str(data[k]).strip()
    if data.get("api_key"):
        cfg["api_key"] = str(data["api_key"]).strip()
    elif not cfg.get("api_key"):
        cfg["api_key"] = ""
    for k in ("max_tokens", "temperature", "timeout_s"):
        if data.get(k) not in (None, ""):
            cfg[k] = data[k]
    provider = (cfg.get("provider") or "").lower()
    if provider not in ("anthropic", "openai"):
        raise ValueError("provider 必须是 anthropic / openai")
    if not cfg.get("base_url"):
        raise ValueError("base_url 不能为空")
    if not cfg.get("model"):
        raise ValueError("model 不能为空")
    cfg["provider"] = provider
    cfg["max_tokens"] = int(cfg.get("max_tokens") or 1024)
    cfg["temperature"] = float(cfg.get("temperature", 0.2))
    cfg["timeout_s"] = float(cfg.get("timeout_s") or 60)
    LLM_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return public_llm_config(cfg)


def import_llm_env(provider=None):
    """显式把环境变量里的 LLM 配置导入 runtime/llm.json（用户点击后才执行）。"""
    p = (provider or os.environ.get("AIUI_LLM_PROVIDER") or "").lower()
    if not p:
        if os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"):
            p = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            p = "openai"
    if p == "anthropic":
        data = {
            "provider": "anthropic",
            "base_url": _env("ANTHROPIC_BASE_URL") or "https://api.anthropic.com",
            "api_key": _env("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "AIUI_LLM_API_KEY"),
            "model": _env("ANTHROPIC_MODEL") or "claude-sonnet-5-5",
            "auth_header": "authorization" if os.environ.get("ANTHROPIC_AUTH_TOKEN") else "x-api-key",
        }
    elif p == "openai":
        data = {
            "provider": "openai",
            "base_url": _env("AIUI_OPENAI_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"),
            "api_key": _env("AIUI_OPENAI_API_KEY", "OPENAI_API_KEY", "AIUI_LLM_API_KEY"),
            "model": _env("AIUI_OPENAI_MODEL", "OPENAI_MODEL"),
            "auth_header": "authorization",
        }
    else:
        raise ValueError("环境里没有可导入的 LLM 配置（需要 ANTHROPIC_* 或 OPENAI_*）")
    return save_llm_config(data)


def _endpoint(cfg):
    base = (cfg.get("base_url") or "").rstrip("/")
    provider = (cfg.get("provider") or "").lower()
    if provider == "anthropic":
        if base.endswith("/messages"):
            return base
        if base.endswith("/v1"):
            return base + "/messages"
        return base + "/v1/messages"
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _merge_messages(messages):
    out = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + content
        else:
            out.append({"role": role, "content": content})
    return out


def _openai_text(data):
    choices = data.get("choices") or []
    if not choices:
        return ""
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, list):
        return "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content)
    return content or ""


def _anthropic_text(data):
    parts = []
    for block in (data.get("content") or []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "\n".join(parts)


class LLMClient:
    def __init__(self, config):
        self.config = config or {}
        self.provider = (self.config.get("provider") or "").lower()
        self.base_url = (self.config.get("base_url") or "").rstrip("/")
        self.api_key = self.config.get("api_key") or ""
        self.model = self.config.get("model") or ""
        self.max_tokens = int(self.config.get("max_tokens") or 1024)
        self.temperature = float(self.config.get("temperature", 0.2))
        self.timeout_s = float(self.config.get("timeout_s") or 60)
        if self.provider not in ("openai", "anthropic"):
            raise LLMError("LLM provider 必须是 openai / anthropic")
        if not self.base_url or not self.api_key or not self.model:
            raise LLMError("LLM 未配置完整（base_url / api_key / model）")

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.provider == "anthropic":
            h["anthropic-version"] = "2023-06-01"
            if (self.config.get("auth_header") or "x-api-key").lower() == "authorization":
                h["Authorization"] = f"Bearer {self.api_key}"
            else:
                h["x-api-key"] = self.api_key
        else:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _body(self, messages, system, max_tokens, temperature):
        msgs = _merge_messages(messages)
        if self.provider == "anthropic":
            body = {
                "model": self.model,
                "max_tokens": int(max_tokens or self.max_tokens),
                "temperature": float(temperature if temperature is not None else self.temperature),
                "messages": msgs,
            }
            if system:
                body["system"] = system
            return body
        all_msgs = ([{"role": "system", "content": system}] if system else []) + msgs
        return {
            "model": self.model,
            "messages": all_msgs,
            "max_tokens": int(max_tokens or self.max_tokens),
            "temperature": float(temperature if temperature is not None else self.temperature),
        }

    def chat(self, messages, system=None, max_tokens=None, temperature=None):
        body = json.dumps(self._body(messages, system, max_tokens, temperature),
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(_endpoint(self.config), data=body,
                                     headers=self._headers(), method="POST")
        last_err = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    raw = r.read().decode("utf-8")
                data = json.loads(raw)
                text = _anthropic_text(data) if self.provider == "anthropic" else _openai_text(data)
                if self.provider == "anthropic":
                    finish_reason = str(data.get("stop_reason") or "")
                    truncated = finish_reason == "max_tokens"
                else:
                    choices = data.get("choices") or []
                    finish_reason = str((choices[0].get("finish_reason") if choices else "") or "")
                    truncated = finish_reason == "length"
                if truncated:
                    raise LLMResponseError(
                        f"LLM 输出被截断（finish_reason={finish_reason}），请增大 max_tokens")
                if not text:
                    raise LLMError(
                        f"LLM 返回了空内容（finish_reason={finish_reason or 'unknown'}）")
                return {
                    "text": text,
                    "model": data.get("model") or self.model,
                    "usage": data.get("usage") or {},
                    "finish_reason": finish_reason,
                }
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:800]
                last_err = LLMError(f"LLM HTTP {e.code}: {detail}")
                if e.code not in (429, 500, 502, 503, 504):
                    raise last_err
            except urllib.error.URLError as e:
                last_err = LLMError(f"LLM 连接失败: {e.reason}")
            except LLMResponseError:
                raise
            except LLMError as e:
                last_err = e
            if attempt < 2:
                time.sleep(1.2 * (attempt + 1))
        raise last_err or LLMError("LLM 调用失败")


def test_llm(config=None):
    cfg = config or load_llm_config()
    client = LLMClient(cfg)
    t0 = time.time()
    res = client.chat([{"role": "user", "content": "只回复两个字：正常"}],
                      system="这是连通性测试，请简短回复。", max_tokens=16, temperature=0)
    return {
        "ok": True,
        "latency_ms": int((time.time() - t0) * 1000),
        "model": res.get("model"),
        "reply": (res.get("text") or "")[:100],
        "usage": res.get("usage"),
    }
