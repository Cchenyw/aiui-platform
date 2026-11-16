# -*- coding: utf-8 -*-
"""
生成时序图 HTML（一次调度的完整路径）。坐标由代码计算，避免手写错位。
用法：python scripts/build_seq.py
输出：artifacts/seq-aiui-dispatch.html
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

W = 1220
LANES = [
    ("ext",   "用例提交方", "curl / CI"),
    ("plat",  "测试平台", ":8000 控制面"),
    ("agent", "执行机 Agent", ":8001 执行面"),
    ("mcp",   "MCP 工具服务", "stdio 子进程"),
    ("chrome", "Chromium", "每任务独立"),
    ("app",   "被测应用", ":8081"),
]
X = {"ext": 92, "plat": 312, "agent": 530, "mcp": 722, "chrome": 912, "app": 1108}
TOP = 96           # lifeline 头下沿
Y0 = 150           # 第一条消息
STEP = 31

# (kind, from, to, label, style)
# kind: msg | self | dash | accent | frag | note
SEQ = [
    ("note",  None, None, "① 执行机启动即注册，之后每 3s 心跳（携带当前负载）", None),
    ("dash",  "agent", "plat", "POST /api/agents/register", None),
    ("dash",  "agent", "plat", "heartbeat · running=2", None),

    ("frag",  None, None, "一次调度的完整路径", "start"),
    ("msg",   "ext", "plat", "1  POST /api/tasks  {cases × 11, max_retry=2}", None),
    ("self",  "plat", None, "2  入队（priority + seq）· 审计 created", None),
    ("self",  "plat", None, "3  pick_agent：在线 + 容量未满 + 负载最小", None),
    ("accent", "plat", "agent", "4  POST /task  →  202 accepted", None),
    ("self",  "plat", None, "5  状态 running · 审计 dispatched", None),
    ("msg",   "agent", "mcp", "6  spawn 独立子进程（stdio）", None),
    ("msg",   "agent", "mcp", "7  initialize · list_tools", None),
    ("msg",   "agent", "mcp", "8  call_tool(navigate)", None),
    ("msg",   "mcp", "chrome", "9  page.goto(url)", None),
    ("msg",   "chrome", "app", "10  GET /login → 200", None),
    ("msg",   "agent", "mcp", "11  call_tool(type_text / click) ×3", None),
    ("msg",   "agent", "mcp", '12  call_tool(expect_text) → {"ok": true}', None),
    ("accent", "agent", "plat", "13  POST /api/results  {ok, steps[], elapsed}", None),
    ("self",  "plat", None, "14  状态 done · 审计 done", None),
    ("self",  "plat", None, "15  append audit.jsonl（事件流落盘）", None),
    ("frag",  None, None, None, "end"),

    ("frag",  None, None, "失败路径：结果 ok=false", "alt"),
    ("msg",   "agent", "plat", 'results: error="click 超时"', None),
    ("self",  "plat", None, "attempt+1 → 插回队首重新调度（超限则 failed）", None),
    ("frag",  None, None, None, "end"),
]


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build():
    parts = []
    # 头部参与者
    for k, name, sub in LANES:
        x = X[k]
        parts.append(
            f'<rect x="{x-74}" y="52" width="148" height="46" rx="6" fill="#ffffff" stroke="#d9d6cc"/>'
            f'<text x="{x}" y="71" text-anchor="middle" font-size="12.5" fill="#2d3142">{esc(name)}</text>'
            f'<text x="{x}" y="87" text-anchor="middle" font-size="9.5" fill="#8a8f9c" '
            f'font-family="DejaVu Sans Mono,monospace">{esc(sub)}</text>'
            f'<line x1="{x}" y1="{TOP}" x2="{x}" y2="__BOTTOM__" stroke="#d5d2c8" stroke-width="1" stroke-dasharray="3,4"/>'
        )

    y = Y0
    open_frag = None
    for item in SEQ:
        kind = item[0]
        if kind == "frag":
            style = item[4]
            if style == "start":
                open_frag = (y - 16, item[3], None)
                y += 36
            elif style == "alt":
                open_frag = (y - 16, item[3], "alt")
                y += 32
            else:  # end
                top, label, style = open_frag
                parts.append(
                    f'<rect x="46" y="{top}" width="{W-92}" height="{y-top+6}" rx="6" fill="none" '
                    f'stroke="{"#c9c5b8" if style is None else "#d8b9a8"}" stroke-dasharray="5,4"/>'
                )
                tag = label or ""
                if tag:
                    parts.append(
                        f'<rect x="46" y="{top}" width="14" height="18" fill="{"#f5f5f5" if style is None else "#fbeee8"}"/>'
                        f'<text x="58" y="{top+13}" font-size="10" fill="{"#6b7080" if style is None else "#bf4520"}">'
                        f'{esc(tag)}</text>'
                    )
                y += 10
                open_frag = None
                continue
            continue

        if kind == "note":
            parts.append(f'<text x="46" y="{y}" font-size="10.5" fill="#8a8f9c">{esc(item[3])}</text>')
            y += STEP
            continue

        if kind == "self":
            x = X[item[1]]
            parts.append(
                f'<path d="M{x},{y} h62 v16 h-58" fill="none" stroke="#8a8f9c" stroke-width="1.2" '
                f'marker-end="url(#sq-ar)"/>'
            )
            parts.append(f'<text x="{x+70}" y="{y+10}" font-size="10.5" fill="#4a4f60">{esc(item[3])}</text>')
            y += STEP + 8
            continue

        f, t = X[item[1]], X[item[2]]
        accent = kind == "accent"
        dash = kind == "dash"
        color = "#bf4520" if accent else "#8a8f9c"
        marker = "sq-ar-acc" if accent else "sq-ar"
        d = f' stroke-dasharray="5,4"' if dash else ""
        parts.append(
            f'<path d="M{f},{y} L{t},{y}" fill="none" stroke="{color}" stroke-width="{2 if accent else 1.3}"{d} '
            f'marker-end="url(#{marker})"/>'
        )
        lbl = item[3]
        wpx = int(len(lbl) * (6.0 if any(c.isascii() for c in lbl) else 10.5) * 0.62) + 14
        wpx = max(60, min(wpx, 300))
        cx = (f + t) / 2
        bold = ' font-weight="700"' if accent else ""
        fs_color = "#bf4520" if accent else "#4a4f60"
        parts.append(
            f'<rect x="{cx-wpx/2:.0f}" y="{y-21}" width="{wpx}" height="15" fill="#f5f5f5"/>'
            f'<text x="{cx:.0f}" y="{y-10}" text-anchor="middle" font-size="10.5" '
            f'fill="{fs_color}"{bold}>{esc(lbl)}</text>'
        )
        y += STEP

    bottom = y + 34
    body = "\n    ".join(parts).replace("__BOTTOM__", str(bottom))
    notes_y = bottom + 18
    notes = (
        f'<text x="46" y="{notes_y}" font-size="10.5" fill="#8a8f9c">'
        f'心跳超时（10s）：平台摘除该执行机，并把它手上 running 的任务重新入队 —— 不回收就会产生"幽灵任务"。</text>'
        f'<text x="46" y="{notes_y+17}" font-size="10.5" fill="#8a8f9c">'
        f'结果回传幂等：同 task_id 重复上报直接忽略，因为执行机不可信（网络重试会重发）。</text>'
    )
    h = notes_y + 34
    return body, notes, h


def main():
    body, notes, h = build()
    svg = f'''<svg viewBox="0 0 {W} {h}" role="img" aria-labelledby="seq-aiui-title seq-aiui-desc"
     width="{W}" height="{h}">
  <title id="seq-aiui-title">一次测试任务调度的时序</title>
  <desc id="seq-aiui-desc">用例提交方把批量用例提交给平台，平台入队并按最小负载分配，向执行机下发任务；执行机拉起独立 MCP 子进程与 Chromium 驱动被测应用，逐步回传 trace，平台更新状态机并写审计文件；失败时按上限重试。</desc>
  <defs>
    <marker id="sq-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto">
      <path d="M0,0 L10,5 L0,10 z" fill="#8a8f9c"/>
    </marker>
    <marker id="sq-ar-acc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto">
      <path d="M0,0 L10,5 L0,10 z" fill="#bf4520"/>
    </marker>
  </defs>
    {body}
    {notes}
</svg>'''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>AI UI 调度时序</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#f5f5f5;font-family:"WenQuanYi Zen Hei","Noto Sans CJK SC",sans-serif;
       padding:32px;display:flex;justify-content:center}}
  .frame{{width:{W+68}px;background:#fff;border:1px solid #e2dfd6;border-radius:8px;padding:30px 34px 24px}}
  .eyebrow{{font-family:"DejaVu Sans Mono",monospace;font-size:11px;letter-spacing:.22em;color:#8a8f9c}}
  h1{{font-size:32px;color:#2d3142;margin:10px 0 6px}}
  .sub{{font-size:13px;color:#8a8f9c;margin-bottom:14px;line-height:1.7}}
  svg{{display:block;overflow:visible}}
</style></head><body>
<div class="frame">
  <div class="eyebrow">SEQUENCE · ONE DISPATCH CYCLE</div>
  <h1>一次调度发生了什么</h1>
  <div class="sub">从用例提交到审计落盘：15 步主流程 + 失败重试分支。</div>
  {svg}
</div>
</body></html>'''
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS / "seq-aiui-dispatch.html"
    out.write_text(html, encoding="utf-8")
    print(f"OK {out}", len(html), "bytes | svg height", h)


if __name__ == "__main__":
    main()
