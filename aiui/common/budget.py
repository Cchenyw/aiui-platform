# -*- coding: utf-8 -*-
"""AI budget settings and token cost estimation.

The budget is stored by the platform (SQLite settings table) and snapshotted
into each AI task. The initial pricing rule is an editable DeepSeek reference:

  DeepSeek V4 Flash: cache hit $0.0028 / 1M, cache miss $0.14 / 1M,
  output $0.28 / 1M (USD).  Verify against the official pricing page; the
  page lets the operator change every number and add more rules.
"""
import fnmatch

from aiui.common import limits

MAX_TOKENS_LIMIT = 10_000_000
MAX_STEPS_LIMIT = 1_000

DEFAULT_PRICING = [
    {
        "name": "DeepSeek V4 Flash（参考价）",
        "model": "deepseek-*",
        "currency": "USD",
        "cache_hit_per_m": 0.0028,
        "cache_miss_per_m": 0.14,
        "output_per_m": 0.28,
        "note": "DeepSeek V4 Flash 参考价（2026-05 起）；实际以官方定价页/账单为准，可编辑。",
    }
]

DEFAULT_BUDGET = {
    "max_total_tokens": limits.AI_MAX_TOTAL_TOKENS,
    "max_steps_ceiling": limits.AI_MAX_STEPS_CEILING,
    "pricing": DEFAULT_PRICING,
}


def _number(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} 必须是数字")
    if number < 0:
        raise ValueError(f"{label} 不能为负数")
    return number


def normalize_budget(data):
    data = data or {}
    try:
        max_total = int(data.get("max_total_tokens", DEFAULT_BUDGET["max_total_tokens"]))
        max_steps = int(data.get("max_steps_ceiling", DEFAULT_BUDGET["max_steps_ceiling"]))
    except (TypeError, ValueError):
        raise ValueError("token 上限和最大规划步数必须是整数")
    if not 1 <= max_total <= MAX_TOKENS_LIMIT:
        raise ValueError(f"token 上限必须在 1~{MAX_TOKENS_LIMIT}")
    if not 1 <= max_steps <= MAX_STEPS_LIMIT:
        raise ValueError(f"最大规划步数上限必须在 1~{MAX_STEPS_LIMIT}")
    pricing = data.get("pricing", DEFAULT_BUDGET["pricing"])
    if not isinstance(pricing, list):
        raise ValueError("计费规则必须是 JSON 数组")
    rules = []
    for i, rule in enumerate(pricing, 1):
        if not isinstance(rule, dict):
            raise ValueError(f"第 {i} 条计费规则必须是对象")
        name = str(rule.get("name") or f"规则 {i}").strip()[:200]
        model = str(rule.get("model") or "").strip()
        if not model:
            raise ValueError(f"第 {i} 条计费规则缺少模型匹配")
        currency = str(rule.get("currency") or "USD").strip().upper()
        if currency not in ("USD", "CNY"):
            raise ValueError(f"第 {i} 条计费规则币种必须是 USD / CNY")
        rules.append({
            "name": name,
            "model": model,
            "currency": currency,
            "cache_hit_per_m": _number(rule.get("cache_hit_per_m", 0),
                                       f"第 {i} 条缓存命中价"),
            "cache_miss_per_m": _number(rule.get("cache_miss_per_m", 0),
                                        f"第 {i} 条缓存未命中价"),
            "output_per_m": _number(rule.get("output_per_m", 0),
                                    f"第 {i} 条输出价"),
            "note": str(rule.get("note") or "")[:500],
        })
    return {
        "max_total_tokens": max_total,
        "max_steps_ceiling": max_steps,
        "pricing": rules,
    }


def match_rule(model, rules):
    model = str(model or "")
    for rule in rules or []:
        pattern = str(rule.get("model") or "")
        if pattern and fnmatch.fnmatch(model, pattern):
            return rule
    return None


def estimate_cost(usage, model, rules):
    rule = match_rule(model, rules)
    if not rule:
        return None
    usage = usage or {}
    hit = float(usage.get("prompt_cache_hit_tokens",
                          usage.get("cache_read_input_tokens", 0)) or 0)
    prompt = float(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    miss_raw = usage.get("prompt_cache_miss_tokens")
    if miss_raw is None:
        miss = max(0.0, prompt - hit) if hit else prompt
    else:
        miss = float(miss_raw or 0)
    if hit == 0 and miss == 0 and prompt > 0:
        miss = prompt
    output = float(usage.get("completion_tokens",
                             usage.get("output_tokens", 0)) or 0)
    cost = (hit / 1_000_000 * float(rule["cache_hit_per_m"])
            + miss / 1_000_000 * float(rule["cache_miss_per_m"])
            + output / 1_000_000 * float(rule["output_per_m"]))
    return {
        "estimated": round(cost, 6),
        "currency": rule["currency"],
        "rule": rule["name"],
        "model": model,
        "tokens": {
            "cache_hit": int(hit),
            "cache_miss": int(miss),
            "output": int(output),
        },
        "prices": {
            "cache_hit_per_m": rule["cache_hit_per_m"],
            "cache_miss_per_m": rule["cache_miss_per_m"],
            "output_per_m": rule["output_per_m"],
        },
    }


def format_cost(cost, currency):
    symbol = {"USD": "$", "CNY": "¥"}.get(str(currency or "").upper(), "")
    text = f"{float(cost or 0):.6f}".rstrip("0").rstrip(".")
    return f"{symbol}{text or '0'}"
