# -*- coding: utf-8 -*-
"""平台管理界面（服务端渲染，零前端构建）。

所有页面共用 layout()，日志详情页用一小段原生 JS 轮询增量日志。
用户输入一律 html.escape，避免把用例名/日志内容直接拼进 HTML。
"""
import html
import json
from datetime import datetime
from urllib.parse import quote

from aiui.common import budget, limits, profiles

STATE_CN = {
    "queued": "排队", "running": "执行中", "retry": "重试中",
    "done": "通过", "failed": "失败",
    "awaiting_result": "待回传",
}
LEVEL_CN = {"info": "INFO", "warn": "WARN", "error": "ERROR", "debug": "DEBUG"}
VERDICT_CN = {"passed": "通过", "failed": "失败", "bug_suspected": "疑似 BUG",
              "inconclusive": "无法判定"}
PROFILE_STATUS_CN = {"empty": "未采集", "capturing": "采集中",
                     "ready": "可用", "error": "异常"}


def verdict_badge(v):
    if not v:
        return "-"
    label = VERDICT_CN.get(v, v)
    return f'<span class="badge {esc(v)}">{esc(label)}</span>'


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def fmt_ts(ts):
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M:%S")
    except Exception:
        return "-"


def fmt_ms(v):
    return f"{int(v)} ms" if v not in (None, "") else "-"


def badge(state):
    s = esc(state)
    return f'<span class="badge {s}">{esc(STATE_CN.get(state, state))}</span>'


def agent_badge(state):
    return f'<span class="badge {esc(state)}">{esc(state)}</span>'


def profile_badge(state):
    return (f'<span class="badge {esc(state)}">'
            f'{esc(PROFILE_STATUS_CN.get(state, state))}</span>')


CSS = """
:root{--ink:#2d3142;--muted:#8a8f9c;--line:#e6e4dd;--bg:#f5f5f5;--accent:#bf4520;
--green:#1e7d32;--red:#c0392b;--blue:#1565c0;--amber:#b26a00}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px}
header.top{background:#fff;border-bottom:1px solid var(--line);padding:0 28px;display:flex;
align-items:center;gap:28px;height:58px;position:sticky;top:0;z-index:10}
.brand{font-weight:700;font-size:16px;color:var(--ink);text-decoration:none;white-space:nowrap}
.brand span{color:var(--accent)}
nav{display:flex;gap:4px;flex:1;flex-wrap:wrap}
nav a{padding:8px 14px;border-radius:6px;color:#5c6170;text-decoration:none;font-size:13.5px}
nav a:hover{background:#f3f1ea}
nav a.active{background:#2d3142;color:#fff}
.wrap{max-width:1240px;margin:0 auto;padding:26px 28px 60px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:15px;margin:0 0 12px}
.sub{color:var(--muted);font-size:13px;margin-bottom:20px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:22px}
.card{background:#fff;border:1px solid var(--line);border-radius:8px;padding:13px 18px;min-width:100px}
.card span{font-size:12px;color:var(--muted)}
.card b{display:block;font-size:23px;margin-top:3px}
.panel{background:#fff;border:1px solid var(--line);border-radius:8px;padding:18px 20px;margin-bottom:20px}
table{width:100%;border-collapse:collapse;font-size:13px;background:#fff}
th{background:#faf9f6;text-align:left;padding:9px 10px;color:#666;font-weight:600;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #f2f0ea;vertical-align:top}
tr:last-child td{border-bottom:0}
a{color:#1565c0}
.btn{display:inline-block;padding:7px 13px;border-radius:6px;border:1px solid #d5d2c8;
background:#fff;color:var(--ink);text-decoration:none;font-size:13px;cursor:pointer;
font-family:inherit;line-height:1.3}
.btn:hover{background:#faf9f6}
.btn.primary{background:#2d3142;border-color:#2d3142;color:#fff}
.btn.primary:hover{background:#3b4055;color:#fff}
.btn.danger{color:var(--red);border-color:#e7c6c1}
.btn.small{padding:4px 9px;font-size:12px}
.btn[disabled]{opacity:.45;cursor:not-allowed}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.toolbar .spacer{flex:1}
.toolbar input[type=text]{width:260px}
input[type=text],input[type=number],textarea,select{width:100%;padding:8px 10px;
border:1px solid #d5d3cc;border-radius:6px;font:inherit;background:#fff;color:var(--ink)}
textarea{font-family:Consolas,"DejaVu Sans Mono",monospace;font-size:12.5px;line-height:1.55;
resize:vertical}
label.field{display:block;margin-bottom:14px;font-size:13px;color:#5c6170}
label.field > span{display:block;margin-bottom:5px;font-weight:600;color:var(--ink)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 18px}
.hint{color:var(--muted);font-size:12px;line-height:1.6;margin-top:4px}
pre{background:#fbfaf7;border:1px solid var(--line);border-radius:6px;padding:12px;
overflow:auto;font-family:Consolas,"DejaVu Sans Mono",monospace;font-size:12px;line-height:1.55}
code{font-family:Consolas,"DejaVu Sans Mono",monospace;background:#f3f1ea;
padding:1px 5px;border-radius:4px;font-size:12px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11.5px;
font-weight:600;white-space:nowrap}
.badge.done,.badge.online,.badge.passed{background:#e7f3e8;color:#1e7d32}
.badge.failed,.badge.offline{background:#fbe9e7;color:#c0392b}
.badge.running,.badge.retry{background:#fff4dd;color:#9a5b00}
.badge.queued,.badge.stopped,.badge.scripted{background:#eef0f3;color:#5c6170}
.badge.starting{background:#e7effb;color:#1565c0}
.badge.ai{background:#ede7f6;color:#5e35b1}
.badge.bug_suspected{background:#fff4dd;color:#9a5b00}
.badge.inconclusive{background:#eef0f3;color:#5c6170}
.badge.ready{background:#e7f3e8;color:#1e7d32}
.badge.capturing{background:#fff4dd;color:#9a5b00}
.badge.empty{background:#eef0f3;color:#5c6170}
.badge.error{background:#fbe9e7;color:#c0392b}
.badge.setup{background:#ede7f6;color:#5e35b1}
.msg{border-radius:6px;padding:10px 14px;margin-bottom:16px;font-size:13px}
.msg.ok{background:#e7f3e8;color:#1e7d32;border:1px solid #c8e2ca}
.msg.err{background:#fbe9e7;color:#a93226;border:1px solid #f0c9c3}
.msg.info{background:#e7effb;color:#1565c0;border:1px solid #c9dbf5}
.empty{color:var(--muted);text-align:center;padding:26px 0}
.logs{background:#1e222d;color:#d7dae2;border-radius:8px;padding:14px 16px;height:430px;
overflow:auto;font-family:Consolas,"DejaVu Sans Mono",monospace;font-size:12px;line-height:1.6}
.logs .t{color:#7e8598;margin-right:8px}.logs .src{color:#9aa0b3;margin-right:6px}
.logs .lvl-error{color:#ff8a80}.logs .lvl-warn{color:#ffd180}.logs .lvl-info{color:#b3e5fc}
.steps td{font-size:12.5px}.steps details summary{cursor:pointer;color:#1565c0;font-size:12px}
.step-shot{display:block;width:180px;max-height:135px;object-fit:contain;
border:1px solid var(--line);border-radius:6px;background:#f5f5f5}
.shot-cell{min-width:190px;max-width:210px}.shot-cell a{display:block}
.shot-cell .hint{overflow-wrap:anywhere}
.steps-scroll{overflow-x:auto}.steps-scroll table{min-width:1100px}
.mono{font-family:Consolas,"DejaVu Sans Mono",monospace}
.muted{color:var(--muted)}.nowrap{white-space:nowrap}
.actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
form.inline{display:inline}
.checks{width:16px;height:16px}
@media(max-width:760px){.grid2,.grid3{grid-template-columns:1fr}
header.top{padding:0 14px;gap:12px}nav a{padding:8px 9px}
.wrap{padding:18px 14px 50px}.toolbar input[type=text]{width:100%}}
"""

NAV = [
    ("/", "调度看板"), ("/cases", "用例管理"), ("/profiles", "环境 Profile"),
    ("/agents", "执行节点管理"), ("/mcps", "MCP 管理"), ("/budget", "AI 预算"),
    ("/llm", "LLM 配置"), ("/runs", "执行记录"),
]


def layout(title, active, body, msg="", err="", info=""):
    nav = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{esc(label)}</a>'
        for href, label in NAV
    )
    flash = ""
    if msg:
        flash += f'<div class="msg ok">{esc(msg)}</div>'
    if info:
        flash += f'<div class="msg info">{esc(info)}</div>'
    if err:
        flash += f'<div class="msg err">{esc(err)}</div>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · AI UI 测试平台</title><style>{CSS}</style></head><body>
<header class="top"><a class="brand" href="/">AI UI <span>测试平台</span></a>
<nav>{nav}</nav></header>
<div class="wrap"><h1>{esc(title)}</h1>{flash}{body}</div></body></html>"""


def _state_options(selected=""):
    return "".join(
        f'<option value="{k}"{" selected" if k == selected else ""}>{v}</option>'
        for k, v in STATE_CN.items()
    )


# ── 看板 ───────────────────────────────────────────────────────────
def dashboard(ctx):
    s = ctx["status"]
    c = s["counts"]
    agents = list(s["agents"])
    for node in s.get("remote_nodes") or []:
        agents.append({"agent_id": node["node_id"],
                       "addr": "主动领取 / " + node.get("machine_id", "-"),
                       "status": node["status"], "running": node.get("running", 0),
                       "slots": node.get("slots", 0), "done": "-", "failed": "-"})
    online = [a for a in agents if a["status"] == "online"]
    agent_rows = "".join(
        f"<tr><td>{esc(a['agent_id'])}</td><td class=mono>{esc(a['addr'])}</td>"
        f"<td>{agent_badge(a['status'])}</td><td>{a['running']}/{a['slots']}</td>"
        f"<td>{a['done']}</td><td>{a['failed']}</td></tr>"
        for a in agents
    ) or "<tr><td colspan=6 class=empty>暂无执行节点注册。去 <a href='/agents'>执行节点管理</a> 启动一个本地执行节点。</td></tr>"
    runs = ctx["runs"]
    run_rows = "".join(
        f"<tr><td><a class=mono href='/runs/{esc(r['task_id'])}'>{esc(r['task_id'])}</a></td>"
        f"<td>{esc(r['name'])}</td><td>{badge(r['state'])}</td><td>{r['attempt']}</td>"
        f"<td>{esc(r['agent_id'] or '-')}</td><td>{fmt_ms(r['elapsed_ms'])}</td>"
        f"<td class=nowrap>{fmt_ts(r['created'])}</td></tr>"
        for r in runs
    ) or "<tr><td colspan=7 class=empty>还没有执行记录。</td></tr>"
    body = f"""
<div class=cards>
  <div class=card><span>排队</span><b>{c.get('queued',0)}</b></div>
  <div class=card><span>执行中</span><b style="color:#9a5b00">{c.get('running',0)}</b></div>
  <div class=card><span>待回传</span><b>{c.get('awaiting_result',0)}</b></div>
  <div class=card><span>重试中</span><b style="color:#9a5b00">{c.get('retry',0)}</b></div>
  <div class=card><span>通过</span><b style="color:#1e7d32">{c.get('done',0)}</b></div>
  <div class=card><span>失败</span><b style="color:#c0392b">{c.get('failed',0)}</b></div>
  <div class=card><span>本次总任务</span><b>{s['total']}</b></div>
  <div class=card><span>平均耗时</span><b>{s['avg_elapsed_ms']}<small> ms</small></b></div>
  <div class=card><span>执行节点在线</span><b>{len(online)}</b></div>
</div>
<div class=toolbar>
  <a class="btn primary" href="/cases">管理用例</a>
  <a class="btn" href="/cases/new">＋ 新建用例</a>
  <a class="btn" href="/agents">配置执行节点</a>
  <a class="btn" href="/mcps">配置 MCP</a>
  <span class=spacer></span>
  <span class=muted>用例 {ctx['case_count']} 条 ｜ 运行记录 {ctx['run_count']} 条</span>
</div>
<div class=panel><h2>执行节点池</h2>
<table><tr><th>执行节点</th><th>地址</th><th>状态</th><th>占用/容量</th><th>成功</th><th>失败</th></tr>
{agent_rows}</table></div>
<div class=panel><h2>最近运行</h2>
<table><tr><th>任务</th><th>用例</th><th>状态</th><th>尝试</th><th>执行节点</th><th>耗时</th><th>创建时间</th></tr>
{run_rows}</table></div>
"""
    return layout("调度看板", "/", body)


# ── 用例 ───────────────────────────────────────────────────────────
def execution_target_picker(ctx):
    nodes = ctx.get("execution_nodes") or []
    if not nodes:
        return ""
    options = '<option value="">自动选择</option>'
    for node in nodes:
        nid = node.get("node_id") or node.get("agent_id")
        if not nid:
            continue
        machine = node.get("machine_id") or "本地"
        state = "" if node.get("status") == "online" else " · 离线等待"
        options += (f'<option value="{esc(nid)}">{esc(nid)}'
                    f'（{esc(machine + state)}）</option>')
    return ('<label>执行节点 <select id="executionTarget" name="target_node_id">'
            + options + '</select></label>' + """
<script>
document.addEventListener('submit', function(event) {
  const form = event.target;
  if (!form.matches('form[data-run-target]')) return;
  let input = form.querySelector('input[name="target_node_id"]');
  if (!input) {
    input = document.createElement('input'); input.type = 'hidden';
    input.name = 'target_node_id'; form.appendChild(input);
  }
  input.value = document.getElementById('executionTarget').value;
});
</script>""")


def cases_page(ctx):
    mcps = {m["id"]: m for m in ctx["mcps"]}
    profiles = ctx.get("profiles") or {}
    rows = ""
    for c in ctx["cases"]:
        m = mcps.get(c["mcp_id"]) or ctx.get("default_mcp") or {}
        is_ai = c.get("mode") == "ai"
        kind_badge = ('<span class="badge setup">采集</span> '
                      if c.get("kind") == "setup" else "")
        mode_badge = kind_badge + ('<span class="badge ai">AI</span>' if is_ai
                                   else '<span class="badge scripted">脚本</span>')
        steps = (f'AI · 最多 {c.get("max_steps", limits.AI_DEFAULT_MAX_STEPS)} 步' if is_ai
                 else f'{c["step_count"]} 步')
        detail = c.get("description") or (c.get("instruction") if is_ai else "")
        profile = profiles.get(c.get("profile_id") or "")
        profile_line = (f'<div class=muted style="font-size:12px">'
                        f'Profile: {esc(profile["name"])}</div>' if profile else "")
        rows += f"""<tr>
<td><input class=checks type=checkbox name=case_ids value="{esc(c['case_id'])}" form=batch></td>
<td class=mono>{esc(c['case_id'])}</td>
<td><a href="/cases/{esc(c['case_id'])}/edit">{esc(c['name'])}</a>
<div class=muted style="font-size:12px">{esc(detail)[:90]}</div>{profile_line}</td>
<td>{mode_badge}</td>
<td>{esc(m.get('name','（默认内置）'))}</td>
<td>{steps}</td><td>{esc(c.get('tags',''))}</td>
<td>{int(c.get('timeout_s',60))} s</td>
<td>{'启用' if c['enabled'] else '停用'}</td>
<td class=nowrap>{fmt_ts(c['updated'])}</td>
<td><div class=actions>
<form class=inline data-run-target method=post action="/cases/{esc(c['case_id'])}/run"><button class="btn small primary">运行</button></form>
<a class="btn small" href="/cases/{esc(c['case_id'])}/edit">编辑</a>
<form class=inline method=post action="/cases/{esc(c['case_id'])}/delete"
 onsubmit="return confirm('确认删除该用例？')"><button class="btn small danger">删除</button></form>
</div></td></tr>"""
    if not rows:
        rows = "<tr><td colspan=11 class=empty>没有用例。点右上角“新建用例”开始。</td></tr>"
    body = f"""
<form id=batch data-run-target method=post action="/cases/run-selected"></form>
<div class=toolbar>
  <form method=get action="/cases" style="display:flex;gap:8px">
    <input type=text name=q value="{esc(ctx['q'])}" placeholder="搜索名称 / 描述 / 标签 / ID">
    <button class=btn>搜索</button>
  </form>
  <span class=spacer></span>
  {execution_target_picker(ctx)}
  <button class="btn primary" form=batch>运行选中</button>
  <form class=inline data-run-target method=post action="/cases/run-all"><button class=btn>运行全部</button></form>
  <a class="btn" href="/cases/new">＋ 新建用例</a>
</div>
<div class=panel>
<table><tr><th></th><th>ID</th><th>名称</th><th>模式</th><th>MCP</th><th>步骤</th><th>标签</th>
<th>超时</th><th>状态</th><th>更新时间</th><th>操作</th></tr>{rows}</table>
</div>
<div class=hint>“运行选中”会把勾选用例按当前顺序提交到平台队列；执行进度见 <a href="/runs">执行记录</a>。</div>
"""
    return layout("用例管理", "/cases", body, msg=ctx.get("msg"), err=ctx.get("err"))


def env_profiles_page(ctx):
    case_names = ctx.get("case_names") or {}
    mcp_names = ctx.get("mcp_names") or {}
    rows = ""
    for p in ctx.get("profiles") or []:
        summary = p.get("summary") or {}
        setup = case_names.get(p.get("setup_case_id", "")) or p.get("setup_case_id") or "-"
        mcp = mcp_names.get(p.get("source_mcp_id", "")) or p.get("source_mcp_id") or "-"
        version = p.get("version") or 0
        summary_txt = (f"{summary.get('cookie_count', 0)} cookies / "
                       f"{summary.get('origin_count', 0)} origins") if summary else "-"
        disabled = " disabled" if p.get("status") == "capturing" else ""
        rows += f"""<tr>
<td class=mono>{esc(p['profile_id'])}</td>
<td><a href="/profiles/{esc(p['profile_id'])}">{esc(p['name'])}</a>
<div class=muted style="font-size:12px">{esc(p.get('description') or '')[:90]}</div></td>
<td>{esc(setup)}</td><td>{esc(mcp)}</td>
<td>{profile_badge(p.get('status'))}</td><td>{f'v{version}' if version else '-'}</td>
<td>{esc(summary_txt)}</td><td class=nowrap>{fmt_ts(p.get('last_capture'))}</td>
<td class=nowrap>{fmt_ts(p.get('last_used'))}</td>
<td><div class=actions>
<form class=inline data-run-target method=post action="/profiles/{esc(p['profile_id'])}/capture">
<button class="btn small primary"{disabled}>采集</button></form>
<a class="btn small" href="/profiles/{esc(p['profile_id'])}">详情</a>
<a class="btn small" href="/profiles/{esc(p['profile_id'])}/edit">编辑</a>
</div></td></tr>"""
    if not rows:
        rows = ("<tr><td colspan=10 class=empty>还没有 Profile。"
                "请先创建一个 kind=setup 的采集用例，再新建 Profile。</td></tr>")
    body = f"""
<div class=toolbar>
  <form method=get action="/profiles" style="display:flex;gap:8px">
    <input type=text name=q value="{esc(ctx.get('q', ''))}" placeholder="搜索名称 / 描述 / ID">
    <button class=btn>搜索</button>
  </form>
  <span class=spacer></span>
  {execution_target_picker(ctx)}
  <a class="btn" href="/cases/new">＋ 新建采集用例</a>
  <a class="btn primary" href="/profiles/new">＋ 新建 Profile</a>
</div>
<div class=panel><table>
<tr><th>ID</th><th>名称</th><th>采集用例</th><th>来源 MCP</th><th>状态</th><th>版本</th>
<th>摘要</th><th>最近采集</th><th>最近使用</th><th>操作</th></tr>{rows}</table></div>
<div class=hint>Profile 只保存 cookies + localStorage 快照，不保存浏览器进程；每个任务启动时加载一份独立副本。</div>
"""
    return layout("执行环境 Profile", "/profiles", body,
                  msg=ctx.get("msg"), err=ctx.get("err"))


def env_profile_form(ctx):
    p = ctx.get("profile") or {}
    is_new = ctx.get("is_new", True)
    action = "/profiles/new" if is_new else f"/profiles/{esc(p['profile_id'])}/edit"
    title = "新建执行环境 Profile" if is_new else f"编辑 Profile · {p.get('name', '')}"
    options = '<option value="">（请选择采集用例）</option>'
    for c in ctx.get("setup_cases") or []:
        sel = " selected" if c["case_id"] == p.get("setup_case_id") else ""
        mcp = next((m for m in (ctx.get("mcps") or []) if m["id"] == c.get("mcp_id")), None)
        mcp_name = mcp["name"] if mcp else (c.get("mcp_id") or "默认 MCP")
        options += (f'<option value="{esc(c["case_id"])}"{sel}>'
                    f'{esc(c["name"])}（{esc(mcp_name)}）</option>')
    no_cases = "" if (ctx.get("setup_cases") or []) else (
        '<div class="msg info">还没有采集用例。请先新建一个用例，并在表单中选择'
        '“用途=环境采集用例”。</div>')
    body = f"""
{no_cases}
<form method=post action="{action}">
<div class=panel>
<label class=field><span>名称 *</span>
<input type=text name=name required value="{esc(p.get('name', ''))}"
 placeholder="例如：admin 登录环境"></label>
<label class=field><span>描述</span>
<input type=text name=description value="{esc(p.get('description', ''))}"
 placeholder="说明这个环境包含什么登录态/前置条件"></label>
<label class=field><span>采集用例 *</span><select name=setup_case_id>{options}</select>
<div class=hint>采集用例必须标记为 setup；执行成功后会保存 cookies + localStorage。
来源 MCP 由采集用例的 MCP 决定。</div></label>
</div>
<div class=toolbar><button class="btn primary">保存 Profile</button>
<a class=btn href="/profiles">取消</a></div>
</form>"""
    return layout(title, "/profiles", body, msg=ctx.get("msg"), err=ctx.get("err"))


def env_profile_detail(ctx):
    p = ctx["profile"]
    summary = p.get("summary") or {}
    versions = ctx.get("versions") or []
    setup = ctx.get("setup_case") or {}
    mcp = ctx.get("mcp") or {}
    used_by = ctx.get("used_by") or []
    origins = ", ".join(esc(x) for x in (summary.get("origins") or [])) or "-"
    version_rows = ""
    for v in versions:
        run_id = v.get("capture_run_id") or ""
        run_html = f'<a href="/runs/{esc(run_id)}">{esc(run_id)}</a>' if run_id else "-"
        vs = v.get("summary") or {}
        version_rows += (
            f"<tr><td>v{esc(v.get('version'))}</td>"
            f"<td class=mono>{esc((v.get('hash') or '')[:16])}</td>"
            f"<td>{vs.get('cookie_count', 0)}</td>"
            f"<td>{vs.get('origin_count', 0)}</td>"
            f"<td>{v.get('size_bytes') or 0}</td>"
            f"<td class=nowrap>{fmt_ts(v.get('captured_at'))}</td>"
            f"<td>{run_html}</td></tr>")
    if not version_rows:
        version_rows = "<tr><td colspan=7 class=empty>还没有采集版本。</td></tr>"
    used_rows = "".join(
        f'<li><a href="/cases/{esc(c["case_id"])}/edit">{esc(c["name"])}</a></li>'
        for c in used_by) or "<li class=muted>没有被用例引用</li>"
    disabled = " disabled" if p.get("status") == "capturing" else ""
    delete_disabled = " disabled" if p.get("status") == "capturing" or used_by else ""
    last_error = (f'<div class="msg err"><b>最近错误：</b>'
                  f'{esc(p.get("last_error"))}</div>' if p.get("last_error") else "")
    setup_html = (f'<a href="/cases/{esc(setup.get("case_id", ""))}/edit">'
                  f'{esc(setup.get("name"))}</a>' if setup else
                  esc(p.get("setup_case_id")) or "-")
    body = f"""
<div class=cards>
  <div class=card><span>状态</span><b>{profile_badge(p.get('status'))}</b></div>
  <div class=card><span>版本</span><b>{p.get('version') or 0}</b></div>
  <div class=card><span>Cookies</span><b>{summary.get('cookie_count', 0)}</b></div>
  <div class=card><span>Origins</span><b>{summary.get('origin_count', 0)}</b></div>
  <div class=card><span>localStorage keys</span><b>{summary.get('local_storage_keys', 0)}</b></div>
</div>
{last_error}
<div class=toolbar>
  {execution_target_picker(ctx)}
  <form class=inline data-run-target method=post action="/profiles/{esc(p['profile_id'])}/capture">
  <button class="btn primary"{disabled}>采集 / 重新采集</button></form>
  <a class=btn href="/profiles/{esc(p['profile_id'])}/edit">编辑</a>
  <form class=inline method=post action="/profiles/{esc(p['profile_id'])}/delete"
   onsubmit="return confirm('确认删除该 Profile 及其所有版本？')">
  <button class="btn danger"{delete_disabled}>删除</button></form>
  <span class=spacer></span><a class=btn href="/profiles">← 返回</a>
</div>
<div class=panel><h2>基本信息</h2><table>
<tr><th>Profile ID</th><td class=mono>{esc(p['profile_id'])}</td></tr>
<tr><th>采集用例</th><td>{setup_html}</td></tr>
<tr><th>来源 MCP</th><td>{esc(mcp.get('name') or p.get('source_mcp_id') or '-')}</td></tr>
<tr><th>Origins</th><td>{origins}</td></tr>
<tr><th>最近采集</th><td>{fmt_ts(p.get('last_capture'))}</td></tr>
<tr><th>最近使用</th><td>{fmt_ts(p.get('last_used'))}</td></tr>
<tr><th>当前 hash</th><td class=mono>{esc((p.get('current_hash') or '-')[:32])}</td></tr>
</table></div>
<div class=panel><h2>版本历史</h2><table>
<tr><th>版本</th><th>Hash</th><th>Cookies</th><th>Origins</th><th>大小(B)</th>
<th>采集时间</th><th>采集运行</th></tr>{version_rows}</table></div>
<div class=panel><h2>被用例引用</h2><ul>{used_rows}</ul></div>
<div class=hint>Profile 原文含敏感凭证，页面只展示摘要；删除前请确认没有用例引用。</div>
"""
    return layout(f"环境 Profile · {p['name']}", "/profiles", body,
                  msg=ctx.get("msg"), err=ctx.get("err"))


CASE_EXAMPLE = [
    {"tool": "navigate", "args": {"url": "http://127.0.0.1:8081/login"}},
    {"tool": "type_text", "args": {"selector": "#username", "text": "admin"}},
    {"tool": "type_text", "args": {"selector": "#password", "text": "123456"}},
    {"tool": "click", "args": {"selector": "#submit"}},
    {"tool": "expect_text", "args": {"text": "登录成功", "timeout_ms": 4000}},
]

PLAYWRIGHT_EXAMPLE = [
    {"tool": "playwright_navigate", "args": {"url": "http://127.0.0.1:8081/login"}},
    {"tool": "playwright_fill", "args": {"selector": "#username", "value": "admin"}},
    {"tool": "playwright_fill", "args": {"selector": "#password", "value": "123456"}},
    {"tool": "playwright_click", "args": {"selector": "#submit"}},
    {"tool": "playwright_get_visible_text", "args": {}, "assert_contains": "登录成功"},
]

OFFICIAL_PLAYWRIGHT_EXAMPLE = [
    {"tool": "browser_navigate", "args": {"url": "http://127.0.0.1:8081/login"}},
    {"tool": "browser_fill_form", "args": {"fields": [
        {"name": "用户名", "target": "#username", "type": "textbox", "value": "admin"},
        {"name": "密码", "target": "#password", "type": "textbox", "value": "123456"},
    ]}},
    {"tool": "browser_click", "args": {"element": "登录按钮", "target": "#submit"}},
    {"tool": "browser_snapshot", "args": {}, "assert_contains": "登录成功"},
]


def case_form(ctx):
    c = ctx.get("case") or {}
    is_new = ctx["is_new"]
    mode = c.get("mode") or "scripted"
    mcp_opts = '<option value="">（使用平台默认 MCP）</option>'
    for m in ctx["mcps"]:
        sel = " selected" if m["id"] == c.get("mcp_id") else ""
        mcp_opts += f'<option value="{esc(m["id"])}"{sel}>{esc(m["name"])}（{esc(m["id"])}）</option>'
    example_mcps = {}
    for m in ctx["mcps"]:
        if not m.get("enabled", True):
            continue
        adapter = profiles.detect_adapter(m)
        if not adapter and "@executeautomation/playwright-mcp-server" in (
                str(m.get("command") or "") + " " + " ".join(m.get("args") or [])):
            adapter = "ea"
        if adapter and (adapter not in example_mcps or m["id"] == c.get("mcp_id")):
            example_mcps[adapter] = m["id"]
    example_specs = [
        ("builtin", "填入内置 MCP 示例", CASE_EXAMPLE),
        ("playwright", "填入官方 Playwright MCP 示例", OFFICIAL_PLAYWRIGHT_EXAMPLE),
        ("ea", "填入 Playwright MCP（EA）示例", PLAYWRIGHT_EXAMPLE),
    ]
    example_buttons = ""
    examples = {}
    for adapter, label, example in example_specs:
        mid = example_mcps.get(adapter)
        disabled = "" if mid else ' disabled title="请先添加并启用对应的 MCP"'
        example_buttons += (f'<button type=button class=btn onclick="fillMcpExample(\'{adapter}\')"'
                            f'{disabled}>{label}</button>')
        if mid:
            examples[adapter] = {"mcp_id": mid, "steps": example}
    examples_json = json.dumps(examples, ensure_ascii=False).replace("<", "\\u003c")
    kind = c.get("kind") or "test"
    profile_opts = '<option value="">（不使用）</option>'
    for p in ctx.get("profiles") or []:
        sel = " selected" if p["profile_id"] == c.get("profile_id") else ""
        status = PROFILE_STATUS_CN.get(p.get("status"), p.get("status") or "")
        profile_opts += (f'<option value="{esc(p["profile_id"])}"{sel}>'
                         f'{esc(p["name"])}（{esc(status)}）</option>')
    steps = ctx.get("steps_raw")
    if steps is None:
        steps = json.dumps(c.get("steps") or CASE_EXAMPLE, ensure_ascii=False, indent=2)
    action = "/cases/new" if is_new else f"/cases/{esc(c['case_id'])}/edit"
    title = "新建用例" if is_new else f"编辑用例 · {c.get('name','')}"
    enabled = " checked" if c.get("enabled", True) else ""
    ai_panel_style = "" if mode == "ai" else "display:none"
    scripted_panel_style = "display:none" if mode == "ai" else ""
    budget_ctx = ctx.get("budget") or {}
    max_steps_ceiling = int(budget_ctx.get("max_steps_ceiling")
                            or limits.AI_MAX_STEPS_CEILING)
    token_limit = int(budget_ctx.get("max_total_tokens")
                      or limits.AI_MAX_TOTAL_TOKENS)
    ai_example_instruction = ("打开 http://127.0.0.1:8081/login，使用用户名 admin、密码 123456 登录，"
                              "确认登录后的页面。")
    ai_example_expected = "页面显示“登录成功，欢迎 admin”，且不再停留在登录页。"
    body = f"""
<form method=post action="{action}">
<div class=panel>
<label class=field><span>用例名称 *</span>
<input type=text name=name required value="{esc(c.get('name',''))}" placeholder="例如：登录-管理员"></label>
<label class=field><span>描述</span>
<input type=text name=description value="{esc(c.get('description',''))}" placeholder="这条用例验证什么"></label>
<div class=grid3>
<label class=field><span>模式</span><select name=mode id=modeSel>
<option value="scripted"{" selected" if mode=="scripted" else ""}>scripted（人工结构化步骤）</option>
<option value="ai"{" selected" if mode=="ai" else ""}>ai（自然语言 + LLM 规划/判定）</option>
</select></label>
<label class=field><span>标签</span>
<input type=text name=tags value="{esc(c.get('tags',''))}" placeholder="登录,冒烟,回归"></label>
<label class=field><span>执行 MCP</span><select name=mcp_id>{mcp_opts}</select></label>
</div>
<div class=hint style="margin-top:-6px;margin-bottom:10px">
自定义 MCP 可在 <a href="/mcps">MCP 管理</a> 添加，留空使用平台默认。AI 模式会读取 <a href="/llm">LLM 配置</a>。
</div>
<div class=grid2>
<label class=field><span>用途</span><select name=kind>
<option value="test"{" selected" if kind=="test" else ""}>普通测试用例</option>
<option value="setup"{" selected" if kind=="setup" else ""}>环境采集用例（可被 Profile 引用）</option>
</select>
<div class=hint>采集用例用于建立登录态/前置环境，通常不参与“运行全部”。</div></label>
<label class=field><span>执行环境 Profile</span><select name=profile_id>{profile_opts}</select>
<div class=hint>选择后，执行前自动加载该 Profile 的 cookies/localStorage；采集用例不要依赖 Profile。
可在 <a href="/profiles">环境 Profile</a> 管理。</div></label>
</div>
<div class=grid2>
<label class=field><span>超时（秒）</span>
<input type=number name=timeout_s min=1 step=1 value="{esc(int(c.get('timeout_s',60)))}">
<div class=hint>AI 模式建议 600s（步数上限 {max_steps_ceiling}），给多轮 LLM 调用和浏览器留足时间；最大 3600s。</div></label>
<label class=field><span>状态</span>
<label style="display:flex;gap:8px;align-items:center;margin-top:8px">
<input type=checkbox name=enabled value=1{enabled}> 启用（停用的用例不会出现在“运行全部”里）</label></label>
</div>
</div>
<div class=panel id=ai-panel style="{ai_panel_style}">
<h2>自然语言用例 / 预期结果</h2>
<div class=toolbar><button type=button class=btn onclick="fillAI()">填入 AI 登录示例</button></div>
<label class=field><span>自然语言步骤 / 目标 *</span>
<textarea name=instruction rows=5 spellcheck=false placeholder="例如：打开登录页，使用 admin/123456 登录，确认登录后的页面。">{esc(c.get('instruction',''))}</textarea>
<div class=hint>描述“要做什么”，不需要写 CSS selector；AI 测试 Agent 会通过 MCP 工具自行观察和选择元素。</div></label>
<label class=field><span>预期结果 *</span>
<textarea name=expected rows=3 spellcheck=false placeholder="例如：页面出现“登录成功，欢迎 admin”。">{esc(c.get('expected',''))}</textarea>
<div class=hint>判定器会对照预期和执行证据，给出 passed / failed / bug_suspected / inconclusive。</div></label>
<label class=field><span>最大规划步数</span>
<input type=number name=max_steps min=1 max="{max_steps_ceiling}" value="{esc(int(c.get('max_steps', limits.AI_DEFAULT_MAX_STEPS)))}">
<div class=hint>上限 {max_steps_ceiling}；连续 3 次相同动作或总 token 超过 {token_limit:,} 会自动终止。</div></label>
</div>
<div class=panel id=scripted-panel style="{scripted_panel_style}">
<h2>步骤 JSON *</h2>
<div class=toolbar>
  {example_buttons}
</div>
<textarea name=steps rows=18 spellcheck=false>{esc(steps)}</textarea>
<div class=hint>
每步格式：<code>{{"tool": "工具名", "args": {{...}}}}</code>。
可选 <code>assert_contains</code> / <code>assert_not_contains</code> 对工具返回文本做断言（适合 Playwright MCP）。
内置工具：<code>navigate</code> <code>type_text</code> <code>click</code> <code>expect_text</code> <code>page_text</code> <code>snapshot</code>。
Playwright MCP 的工具名以“探测工具”结果为准：ExecuteAutomation 用 <code>playwright_*</code>，官方 @playwright/mcp 用 <code>browser_*</code>。
示例按钮会同步选择对应的 MCP；手动切换 MCP 或重新探测不会转换已有步骤。
Profile 采集支持内置和官方 MCP；在此运行采集用例只验证步骤，保存登录态请到“环境 Profile”页点击“采集”。
</div>
</div>
<div class=toolbar>
  <button class="btn primary">保存用例</button>
  <a class=btn href="/cases">取消</a>
  <span class=spacer></span>
  {"" if is_new else f'<span class=muted>ID：{esc(c.get("case_id",""))}</span>'}
</div>
</form>
<script>
const MCP_EXAMPLES = {examples_json};
const AI_INSTRUCTION = {json.dumps(ai_example_instruction, ensure_ascii=False)};
const AI_EXPECTED = {json.dumps(ai_example_expected, ensure_ascii=False)};
const modeSel = document.getElementById('modeSel');
const mcpSel = document.querySelector('select[name=mcp_id]');
const aiPanel = document.getElementById('ai-panel');
const scriptedPanel = document.getElementById('scripted-panel');
const stepsArea = document.querySelector('textarea[name=steps]');
const instrArea = document.querySelector('textarea[name=instruction]');
const expArea = document.querySelector('textarea[name=expected]');
const timeoutInput = document.querySelector('input[name=timeout_s]');
function syncMode() {{
  const ai = modeSel.value === 'ai';
  aiPanel.style.display = ai ? '' : 'none';
  scriptedPanel.style.display = ai ? 'none' : '';
  stepsArea.required = !ai;
  instrArea.required = ai;
  expArea.required = ai;
  if (ai && timeoutInput.value === '60') timeoutInput.value = '600';
}}
modeSel.addEventListener('change', syncMode);
function fillAI() {{
  modeSel.value = 'ai'; instrArea.value = AI_INSTRUCTION; expArea.value = AI_EXPECTED;
  if (timeoutInput.value === '60') timeoutInput.value = '600';
  syncMode();
}}
function fillMcpExample(adapter) {{
  const example = MCP_EXAMPLES[adapter];
  if (!example) return;
  mcpSel.value = example.mcp_id;
  modeSel.value = 'scripted'; stepsArea.value = JSON.stringify(example.steps, null, 2); syncMode();
}}
syncMode();
</script>
"""
    return layout(title, "/cases", body, err=ctx.get("err"), info=ctx.get("info"))


# ── 执行节点 ──────────────────────────────────────────────────────────
def agents_page(ctx):
    runtime = {a["agent_id"]: a for a in ctx["status"]["agents"]}
    procs = ctx["procs"]
    rows = ""
    for a in ctx["configs"]:
        proc = procs.get(a["id"]) or {}
        rt = runtime.get(a["id"])
        pstate = "running" if proc.get("running") else "stopped"
        ptext = "进程运行中" if proc.get("running") else "进程未启动"
        if proc.get("running") and not rt:
            ptext = "启动中 / 未注册"
            pstate = "starting"
        online = rt["status"] if rt else "离线"
        rows += f"""<tr>
<td><div><b>{esc(a['name'])}</b></div><div class="mono muted">{esc(a['id'])}</div></td>
<td>{esc(a['kind'])}</td><td class=mono>{esc(a['host'])}:{a['port']}</td>
<td>{a['slots']}</td><td>{esc(a.get('tags',''))}</td>
<td><span class="badge {pstate}">{ptext}</span></td>
<td>{agent_badge(online)}</td><td>{'是' if a['autostart'] else '否'}</td>
<td>{'启用' if a['enabled'] else '停用'}</td>
<td><div class=actions>
<form class=inline method=post action="/agents/{esc(a['id'])}/start"><button class="btn small primary" {"disabled" if proc.get('running') else ""}>启动</button></form>
<form class=inline method=post action="/agents/{esc(a['id'])}/stop"><button class="btn small" {"disabled" if not proc.get('running') and not rt else ""}>停止</button></form>
<a class="btn small" href="/agents/{esc(a['id'])}/edit">编辑</a>
<form class=inline method=post action="/agents/{esc(a['id'])}/delete"
 onsubmit="return confirm('确认删除该执行节点？（会先停止进程）')"><button class="btn small danger">删除</button></form>
</div></td></tr>"""
    if not rows:
        rows = "<tr><td colspan=10 class=empty>还没有本地执行节点配置。点“添加执行节点”创建一个。</td></tr>"
    managed = {a["id"] for a in ctx["configs"]}
    ext_rows = "".join(
        f"<tr><td>{esc(a['agent_id'])}</td><td class=mono>{esc(a['addr'])}</td>"
        f"<td>{agent_badge(a['status'])}</td><td>{a['running']}/{a['slots']}</td>"
        f"<td>{a['done']}</td><td>{a['failed']}</td></tr>"
        for a in ctx["status"]["agents"] if a["agent_id"] not in managed and a["status"] == "online"
    ) or "<tr><td colspan=6 class=empty>没有未纳管的在线执行节点。</td></tr>"
    remote_rows = ""
    for node in ctx["status"].get("remote_nodes") or []:
        remote_rows += (
            f'<tr><td><b>{esc(node.get("name") or node["node_id"])}</b>'
            f'<div class=mono>{esc(node["node_id"])}</div></td>'
            f'<td class=mono>{esc(node.get("machine_id"))}</td><td>主动领取</td>'
            f'<td>{agent_badge(node.get("status", "offline"))}</td>'
            f'<td>{node.get("running", 0)}/{node.get("slots", 0)}</td>'
            f'<td>{esc(", ".join(node.get("mcp_ids") or [])) or "-"}</td>'
            f'<td class=nowrap>{fmt_ts(node.get("last_hb"))}</td></tr>')
    remote_rows = remote_rows or '<tr><td colspan=7 class=empty>暂无远程执行节点注册。</td></tr>'
    body = f"""
<div class=toolbar>
  <a class="btn primary" href="/agents/new">＋ 添加执行节点</a>
  <span class=spacer></span>
  <span class=muted>本地执行节点由平台拉起；外部执行节点可自行注册。</span>
</div>
<div class="msg info">执行节点负责接收任务、控制并发和回传结果；AI 用例由节点内部的 AI 测试 Agent 规划和执行。新增节点用于增加执行资源。</div>
<div class=panel><h2>本地执行节点配置</h2>
<table><tr><th>名称 / ID</th><th>类型</th><th>地址</th><th>容量</th><th>标签</th>
<th>进程</th><th>注册状态</th><th>自启动</th><th>启用</th><th>操作</th></tr>{rows}</table>
</div>
<div class=panel><h2>未纳管的在线执行节点</h2>
<div class="hint" style="margin-bottom:10px">这些节点已注册并可参与调度，但未添加本地配置。添加同 ID 配置后，需停止外部进程并通过平台启动，才能由平台管理进程。成功/失败为本次平台启动后的执行结果计数，失败重试会分别计数。</div>
<table><tr><th>节点 ID</th><th>地址</th><th>状态</th><th>占用/容量</th><th>成功</th><th>失败</th></tr>{ext_rows}</table>
</div>
<div class=panel><h2>远程执行节点</h2>
<div class=hint>远程节点主动领取任务，进程由所在服务器管理。失联后已领取任务等待原批次回传，不自动迁移。</div>
<table><tr><th>名称 / 节点 ID</th><th>执行机</th><th>连接方式</th><th>状态</th>
<th>占用/容量</th><th>MCP 能力</th><th>最近心跳</th></tr>{remote_rows}</table>
</div>
<div class=hint>
内置 runner 的命令由平台自动生成；自定义执行节点（如 hermes / workbuddy）可在编辑页配置启动命令，
平台会注入 <code>PLATFORM_URL</code>、<code>AGENT_ID</code>、<code>AGENT_HOST</code>、<code>AGENT_PORT</code>、
<code>AGENT_SLOTS</code> 环境变量，并支持 <code>{{platform}}</code> <code>{{agent_id}}</code>
<code>{{host}}</code> <code>{{port}}</code> <code>{{slots}}</code> <code>{{python}}</code> <code>{{here}}</code> 占位符。
</div>
"""
    return layout("执行节点管理", "/agents", body, msg=ctx.get("msg"), err=ctx.get("err"))


def agent_form(ctx):
    a = ctx.get("agent") or {}
    is_new = ctx["is_new"]
    kind = a.get("kind", "builtin")
    action = "/agents/new" if is_new else f"/agents/{esc(a['id'])}/edit"
    title = "添加执行节点" if is_new else f"编辑执行节点 · {a.get('name','')}"
    aid = a.get("id", "")
    cmd = ctx.get("command_raw")
    if cmd is None:
        cmd = json.dumps(a.get("command") or [], ensure_ascii=False, indent=2)
    env = ctx.get("env_raw")
    if env is None:
        env = json.dumps(a.get("env") or {}, ensure_ascii=False, indent=2)
    body = f"""
<div class="hint" style="margin-bottom:16px">执行节点是运行测试任务的进程，可执行脚本用例或 AI 用例。AI 测试 Agent 的规划与判定能力由 AI 用例和 LLM 配置提供。</div>
<form method=post action="{action}">
<div class=panel>
<div class=grid2>
<label class=field><span>节点 ID *</span>
<input type=text name=id required value="{esc(aid)}" {"readonly" if not is_new else ""} placeholder="hermes-local"></label>
<label class=field><span>显示名称 *</span>
<input type=text name=name required value="{esc(a.get('name',''))}" placeholder="Hermes 本地执行节点"></label>
</div>
<div class=grid3>
<label class=field><span>类型</span><select name=kind>
<option value="builtin"{" selected" if kind=="builtin" else ""}>builtin（内置 aiui.agent.runner）</option>
<option value="custom"{" selected" if kind=="custom" else ""}>custom（自定义启动命令）</option>
</select></label>
<label class=field><span>监听地址</span>
<input type=text name=host value="{esc(a.get('host','127.0.0.1'))}"></label>
<label class=field><span>端口 *</span>
<input type=number name=port min=1 max=65535 value="{esc(a.get('port',8001))}"></label>
</div>
<div class=grid3>
<label class=field><span>并发容量 *</span>
<input type=number name=slots min=1 value="{esc(a.get('slots',1))}"></label>
<label class=field><span>标签</span>
<input type=text name=tags value="{esc(a.get('tags',''))}" placeholder="local,chromium"></label>
<label class=field><span>随平台启动</span>
<label style="display:flex;gap:8px;align-items:center;margin-top:8px">
<input type=checkbox name=autostart value=1{' checked' if a.get('autostart') else ''}> 平台启动时拉起</label></label>
</div>
<label class=field><span>启用</span>
<label style="display:flex;gap:8px;align-items:center">
<input type=checkbox name=enabled value=1{' checked' if a.get('enabled',True) else ''}> 允许平台启动此节点</label></label>
</div>
<div class=panel><h2>启动配置</h2>
<label class=field><span>自定义启动命令（JSON 数组，仅 custom 类型使用）</span>
<textarea name=command rows=4 spellcheck=false placeholder='["{{python}}", "my_agent.py", "--platform", "{{platform}}", "--port", "{{port}}"]'>{esc(cmd)}</textarea>
<div class=hint>命令的第一个元素必须是可执行文件。Windows 下 <code>npx</code> / <code>.cmd</code> 可直接写。</div></label>
<label class=field><span>工作目录（留空 = 项目目录）</span>
<input type=text name=cwd value="{esc(a.get('cwd',''))}" placeholder="{esc(ctx['here'])}"></label>
<label class=field><span>环境变量（JSON 对象）</span>
<textarea name=env rows=4 spellcheck=false>{esc(env)}</textarea></label>
<label class=field><span>进程日志目录</span>
<input type=text value="{esc(ctx['log_dir'])}" readonly>
<div class=hint>平台会额外注入 PLATFORM_URL / AGENT_ID / AGENT_HOST / AGENT_PORT / AGENT_SLOTS。</div></label>
</div>
<div class=toolbar><button class="btn primary">保存执行节点</button><a class=btn href="/agents">取消</a></div>
</form>
"""
    return layout(title, "/agents", body, err=ctx.get("err"), info=ctx.get("info"))


# ── MCP ────────────────────────────────────────────────────────────
def mcps_page(ctx):
    rows = ""
    for m in ctx["mcps"]:
        if m["transport"] == "stdio":
            summary = f"{m['command']} {' '.join(m['args'])}"
        else:
            summary = m["url"]
        tools = len(m.get("tools") or [])
        probe = fmt_ts(m.get("last_probe"))
        err = f'<div class="muted" style="color:#c0392b;font-size:12px">{esc(m["last_error"])[:120]}</div>' if m.get("last_error") else ""
        dele = ("" if m["builtin"] else
                f"""<form class=inline method=post action="/mcps/{esc(m['id'])}/delete"
 onsubmit="return confirm('确认删除该 MCP？')"><button class="btn small danger">删除</button></form>""")
        rows += f"""<tr>
<td><div><b>{esc(m['name'])}</b></div><div class="mono muted">{esc(m['id'])}</div></td>
<td>{esc(m['transport'])}</td><td class=mono style="max-width:360px;word-break:break-all">{esc(summary)}</td>
<td>{'内置' if m['builtin'] else '自定义'}</td><td>{'启用' if m['enabled'] else '停用'}</td>
<td>{tools or '-'}<div class=muted style="font-size:12px">{esc(', '.join(t['name'] for t in (m.get('tools') or [])[:4]))}</div></td>
<td class=nowrap>{probe}{err}</td>
<td><div class=actions>
<form class=inline method=post action="/mcps/{esc(m['id'])}/probe"><button class="btn small primary">探测工具</button></form>
<a class="btn small" href="/mcps/{esc(m['id'])}">详情</a>
<a class="btn small" href="/mcps/{esc(m['id'])}/edit">编辑</a>
{dele}</div></td></tr>"""
    if not rows:
        rows = "<tr><td colspan=8 class=empty>还没有 MCP 配置。</td></tr>"
    body = f"""
<div class=toolbar><a class="btn primary" href="/mcps/new">＋ 添加 MCP</a>
<span class=spacer></span>
<span class=muted>内置 MCP 不可删除；Playwright MCP 首次运行需联网下载 npm 包。</span></div>
<div class=panel><table>
<tr><th>名称 / ID</th><th>传输</th><th>命令 / 地址</th><th>来源</th><th>启用</th>
<th>工具数</th><th>最近探测</th><th>操作</th></tr>{rows}</table></div>
<div class=hint>平台“探测工具”会在本机启动该 MCP 并调用 <code>list_tools</code>；执行节点执行用例时会按用例选择的 MCP 重新拉起会话。</div>
"""
    return layout("MCP 管理", "/mcps", body, msg=ctx.get("msg"), err=ctx.get("err"))


def mcp_form(ctx):
    m = ctx.get("mcp") or {}
    is_new = ctx["is_new"]
    action = "/mcps/new" if is_new else f"/mcps/{esc(m['id'])}/edit"
    title = "添加 MCP" if is_new else f"编辑 MCP · {m.get('name','')}"
    transport = m.get("transport", "stdio")
    args = ctx.get("args_raw")
    if args is None:
        args = json.dumps(m.get("args") or [], ensure_ascii=False, indent=2)
    env = ctx.get("env_raw")
    if env is None:
        env = json.dumps(m.get("env") or {}, ensure_ascii=False, indent=2)
    headers = ctx.get("headers_raw")
    if headers is None:
        headers = json.dumps(m.get("headers") or {}, ensure_ascii=False, indent=2)
    mid = m.get("id", "")
    body = f"""
<form method=post action="{action}">
<div class=panel>
<div class=grid2>
<label class=field><span>MCP ID *</span>
<input type=text name=id required value="{esc(mid)}" {"readonly" if not is_new else ""} placeholder="my-mcp"></label>
<label class=field><span>显示名称 *</span>
<input type=text name=name required value="{esc(m.get('name',''))}" placeholder="我的浏览器 MCP"></label>
</div>
<div class=grid2>
<label class=field><span>传输方式</span><select name=transport>
<option value="stdio"{" selected" if transport=="stdio" else ""}>stdio（本地子进程）</option>
<option value="sse"{" selected" if transport=="sse" else ""}>sse（HTTP/SSE）</option>
<option value="http"{" selected" if transport=="http" else ""}>http（streamable-http）</option>
</select></label>
<label class=field><span>启用</span>
<label style="display:flex;gap:8px;align-items:center;margin-top:8px">
<input type=checkbox name=enabled value=1{' checked' if m.get('enabled',True) else ''}> 允许在用例中选择</label></label>
</div>
<label class=field><span>命令（stdio）</span>
<input type=text name=command value="{esc(m.get('command',''))}" placeholder="npx 或 {{python}}"></label>
<label class=field><span>参数（JSON 数组）</span>
<textarea name=args rows=4 spellcheck=false>{esc(args)}</textarea>
<div class=hint>支持占位符 <code>{{python}}</code> <code>{{builtin_mcp}}</code> <code>{{here}}</code>。</div></label>
<label class=field><span>环境变量（JSON 对象）</span>
<textarea name=env rows=3 spellcheck=false>{esc(env)}</textarea></label>
<div class=grid2>
<label class=field><span>URL（sse / http）</span>
<input type=text name=url value="{esc(m.get('url',''))}" placeholder="http://127.0.0.1:9000/mcp"></label>
<label class=field><span>请求头（JSON 对象）</span>
<textarea name=headers rows=3 spellcheck=false>{esc(headers)}</textarea></label>
</div>
</div>
<div class=toolbar><button class="btn primary">保存 MCP</button><a class=btn href="/mcps">取消</a></div>
</form>
"""
    return layout(title, "/mcps", body, err=ctx.get("err"), info=ctx.get("info"))


def mcp_detail(ctx):
    m = ctx["mcp"]
    tools = m.get("tools") or []
    rows = ""
    for t in tools:
        schema = json.dumps(t.get("input_schema") or {}, ensure_ascii=False, indent=2)
        rows += f"""<tr><td class=mono>{esc(t['name'])}</td><td>{esc(t.get('description',''))}
<details><summary>参数 schema</summary><pre>{esc(schema)}</pre></details></td></tr>"""
    if not rows:
        rows = "<tr><td colspan=2 class=empty>还没有探测结果。点下方“立即探测”。</td></tr>"
    if m["transport"] == "stdio":
        target = f"{m['command']} {' '.join(m['args'])}"
    else:
        target = m["url"]
    body = f"""
<div class=panel>
<div class=cards>
<div class=card><span>传输</span><b style="font-size:16px">{esc(m['transport'])}</b></div>
<div class=card><span>工具数</span><b>{len(tools)}</b></div>
<div class=card><span>来源</span><b style="font-size:16px">{'内置' if m['builtin'] else '自定义'}</b></div>
<div class=card><span>最近探测</span><b style="font-size:14px">{fmt_ts(m.get('last_probe'))}</b></div>
</div>
<div class=hint style="margin-bottom:10px">连接目标：<span class=mono>{esc(target)}</span></div>
{('<div class=msg err>' + esc(m['last_error']) + '</div>') if m.get('last_error') else ''}
<div class=toolbar>
<form class=inline method=post action="/mcps/{esc(m['id'])}/probe"><button class="btn primary">立即探测</button></form>
<a class=btn href="/mcps/{esc(m['id'])}/edit">编辑</a>
<a class=btn href="/mcps">返回</a>
</div>
</div>
<div class=panel><h2>工具列表</h2>
<table><tr><th style="width:240px">工具名</th><th>说明 / 参数</th></tr>{rows}</table></div>
"""
    return layout(f"MCP · {m['name']}", "/mcps", body, msg=ctx.get("msg"), err=ctx.get("err"))


# ── LLM 配置 ──────────────────────────────────────────────────────
def budget_page(ctx):
    b = ctx.get("budget") or {}
    pricing = b.get("pricing") or []
    raw_pricing = ctx.get("pricing_raw")
    if raw_pricing is None:
        raw_pricing = json.dumps(pricing, ensure_ascii=False, indent=2)
    llm = ctx.get("llm") or {}
    model = llm.get("model") or ""
    matched = budget.match_rule(model, pricing)
    sample = budget.estimate_cost({
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 10000,
        "completion_tokens": 2000,
    }, model, pricing)
    rows = ""
    for rule in pricing:
        rows += (
            f"<tr><td>{esc(rule.get('name'))}</td>"
            f"<td class=mono>{esc(rule.get('model'))}</td>"
            f"<td>{esc(rule.get('currency'))}</td>"
            f"<td>{esc(rule.get('cache_hit_per_m'))}</td>"
            f"<td>{esc(rule.get('cache_miss_per_m'))}</td>"
            f"<td>{esc(rule.get('output_per_m'))}</td>"
            f"<td class=muted>{esc(rule.get('note') or '')}</td></tr>")
    if not rows:
        rows = ("<tr><td colspan=7 class=empty>"
                "还没有计费规则；保存空数组将不计算成本。</td></tr>")
    matched_txt = (f"{esc(matched.get('name'))}（{esc(matched.get('model'))}）"
                   if matched else "未匹配")
    if sample:
        example = (f"缓存未命中 10,000 tokens + 输出 2,000 tokens ≈ "
                   f"<b>{esc(budget.format_cost(sample['estimated'], sample['currency']))}</b>"
                   f"（规则：{esc(sample['rule'])}）")
    else:
        example = "当前模型没有匹配的计费规则，运行记录不会生成成本估算。"
    default_pricing_json = ctx.get("default_pricing_json") or json.dumps(
        budget.DEFAULT_PRICING, ensure_ascii=False, indent=2)
    try:
        max_total_tokens = int(b.get("max_total_tokens") or 0)
    except (TypeError, ValueError):
        max_total_tokens = 0
    try:
        max_steps_ceiling = int(b.get("max_steps_ceiling") or 0)
    except (TypeError, ValueError):
        max_steps_ceiling = 0
    body = f"""
<div class=cards>
  <div class=card><span>每运行 token 上限</span><b>{max_total_tokens:,}</b></div>
  <div class=card><span>最大规划步数上限</span><b>{max_steps_ceiling}</b></div>
  <div class=card><span>当前模型</span><b style="font-size:15px">{esc(model or '-')}</b></div>
  <div class=card><span>匹配计费规则</span><b style="font-size:15px">{matched_txt}</b></div>
</div>
<form method=post action="/budget">
<div class=panel><h2>预算上限</h2>
<div class=grid2>
<label class=field><span>每 AI 运行 token 上限</span>
<input type=number name=max_total_tokens min=1 max="{budget.MAX_TOKENS_LIMIT}"
 value="{esc(b.get('max_total_tokens', limits.AI_MAX_TOTAL_TOKENS))}">
<div class=hint>达到上限立即停止，结果记为 inconclusive，不再调用判定器。</div></label>
<label class=field><span>最大规划步数上限</span>
<input type=number name=max_steps_ceiling min=1 max="{budget.MAX_STEPS_LIMIT}"
 value="{esc(b.get('max_steps_ceiling', limits.AI_MAX_STEPS_CEILING))}">
<div class=hint>用例的 max_steps 不能超过此值，执行器也会按它封顶。</div></label>
</div>
<div class=hint>新任务使用保存时的预算快照；正在运行的任务不受影响。</div>
</div>
<div class=panel><h2>Token 计费规则</h2>
<div class=hint>成本 = 缓存命中 tokens / 1M × 命中价 + 缓存未命中 tokens / 1M × 未命中价 + 输出 tokens / 1M × 输出价。金额为估算，实际以账单为准。</div>
<table><tr><th>规则</th><th>模型匹配</th><th>币种</th><th>缓存命中/1M</th>
<th>缓存未命中/1M</th><th>输出/1M</th><th>备注</th></tr>{rows}</table>
<label class=field style="margin-top:14px"><span>规则 JSON（可编辑）</span>
<textarea name=pricing_json rows=14 spellcheck=false>{esc(raw_pricing)}</textarea>
<div class=hint>字段：name、model（支持通配符，如 deepseek-*）、currency（USD/CNY）、cache_hit_per_m、cache_miss_per_m、output_per_m、note。第一条匹配的规则生效。</div></label>
<div class=toolbar>
<button class="btn primary">保存预算设置</button>
<button type=button class=btn onclick="resetPricing()">恢复 DeepSeek 默认规则</button>
<a class=btn href="/llm">LLM 配置</a>
</div>
</div>
</form>
<div class=panel><h2>计费示例</h2>
<div class=msg info>{example}</div>
<div class=hint>示例按当前模型匹配规则计算。DeepSeek 的 prompt_cache_hit_tokens / prompt_cache_miss_tokens 会分别计价；没有缓存字段时按未命中价估算。</div>
</div>
<script>
const DEFAULT_PRICING = {json.dumps(default_pricing_json, ensure_ascii=False)};
function resetPricing() {{
  document.querySelector('textarea[name=pricing_json]').value = DEFAULT_PRICING;
}}
</script>
"""
    return layout("AI 预算设置", "/budget", body,
                  msg=ctx.get("msg"), err=ctx.get("err"))


def llm_page(ctx):
    cfg = ctx["config"]
    provider = cfg.get("provider", "openai")
    auth = cfg.get("auth_header", "authorization")
    configured = cfg.get("configured")
    status = ('<span class="badge done">已配置</span>' if configured
              else '<span class="badge failed">未配置</span>')
    key_hint = (f"当前 key：{esc(cfg.get('api_key_masked',''))}，留空保持不变"
                if cfg.get("api_key_set") else "尚未设置 API Key")
    body = f"""
<div class=panel>
<div class=toolbar><div>LLM 状态：{status}</div><span class=spacer></span>
<span class=muted>配置文件：runtime/llm.json（本机执行节点共享）</span></div>
<div class=hint>
AI 用例由执行节点内部的 AI 测试 Agent 规划、调用工具并判定结果。Anthropic 使用 <code>/v1/messages</code>；
OpenAI 兼容网关使用 <code>/v1/chat/completions</code>（DeepSeek / 通义 / 内网网关均可）。
“从环境导入”只会读取 <code>ANTHROPIC_*</code> 或 <code>OPENAI_*</code> 并写入本机配置文件，属于显式操作。
</div>
</div>
<form method=post action="/llm">
<div class=panel><h2>模型接入</h2>
<div class=grid2>
<label class=field><span>Provider</span><select name=provider>
<option value="openai"{" selected" if provider=="openai" else ""}>openai（/v1/chat/completions）</option>
<option value="anthropic"{" selected" if provider=="anthropic" else ""}>anthropic（/v1/messages）</option>
</select></label>
<label class=field><span>鉴权头</span><select name=auth_header>
<option value="authorization"{" selected" if auth=="authorization" else ""}>Authorization: Bearer（多数网关 / OpenAI）</option>
<option value="x-api-key"{" selected" if auth=="x-api-key" else ""}>x-api-key（Anthropic 官方）</option>
</select></label>
</div>
<label class=field><span>Base URL *</span>
<input type=text name=base_url required value="{esc(cfg.get('base_url',''))}"
 placeholder="https://api.deepseek.com/v1 或 https://api.anthropic.com"></label>
<div class=grid2>
<label class=field><span>Model *</span>
<input type=text name=model required value="{esc(cfg.get('model',''))}" placeholder="deepseek-chat / claude-sonnet-5-5"></label>
<label class=field><span>API Key</span>
<input type=password name=api_key value="" placeholder="{esc(key_hint)}" autocomplete="new-password">
<div class=hint>页面只显示掩码；留空保存时保留原 key。</div></label>
</div>
<div class=grid3>
<label class=field><span>max_tokens</span><input type=number name=max_tokens min=16 max=8192 value="{esc(cfg.get('max_tokens',1024))}"></label>
<label class=field><span>temperature</span><input type=number name=temperature min=0 max=2 step=0.1 value="{esc(cfg.get('temperature',0.2))}"></label>
<label class=field><span>单次超时（秒）</span><input type=number name=timeout_s min=5 max=300 value="{esc(cfg.get('timeout_s',60))}"></label>
</div>
</div>
<div class=toolbar><button class="btn primary">保存配置</button>
<a class=btn href="/cases">去用例管理</a></div>
</form>
<div class=panel><h2>连通性测试 / 环境导入</h2>
<div class=toolbar>
<form class=inline method=post action="/llm/test"><button class=btn>测试已保存配置</button></form>
<form class=inline method=post action="/llm/import-env"><button class=btn>从环境变量导入</button></form>
</div>
<div class=hint>测试会真实发起一次很小的模型请求；如果 Base URL / Key / Model 不对，会在这里和运行日志里显示。</div>
</div>
"""
    return layout("LLM 配置", "/llm", body, msg=ctx.get("msg"), err=ctx.get("err"))


# ── 运行记录 ───────────────────────────────────────────────────────
def runs_page(ctx):
    tabs = [("", "全部")] + list(STATE_CN.items())
    tab_html = "".join(
        f'<a class="btn small {"primary" if ctx["state"] == k else ""}" href="/runs{("?state=" + k) if k else ""}">{v}</a>'
        for k, v in tabs
    )
    rows = "".join(
        f"""<tr><td><a class=mono href="/runs/{esc(r['task_id'])}">{esc(r['task_id'])}</a></td>
<td>{'<span class="badge setup">采集</span> ' if r.get('kind') == 'capture' else ''}{esc(r['name'])}</td>
<td>{'<span class="badge ai">AI</span>' if (r.get('case') or {}).get('mode') == 'ai' else '<span class="badge scripted">脚本</span>'}</td>
<td>{badge(r['state'])}</td><td>{verdict_badge((r.get('result') or {}).get('verdict',''))}</td>
<td>{r['attempt']}</td><td>{esc(r['agent_id'] or '-')}</td>
<td class=mono>{esc(r['mcp_id'] or '-')}</td>
<td>{fmt_ms(r['elapsed_ms'])}</td><td class=nowrap>{fmt_ts(r['created'])}</td></tr>"""
        for r in ctx["runs"]
    ) or "<tr><td colspan=10 class=empty>没有符合条件的运行记录。</td></tr>"
    body = f"""
<meta http-equiv="refresh" content="3">
<div class=toolbar>{tab_html}<span class=spacer></span>
<a class="btn" href="/cases">去运行用例</a><span class=muted>每 3 秒自动刷新</span></div>
<div class=panel><table>
<tr><th>任务</th><th>用例</th><th>模式</th><th>状态</th><th>AI 判定</th><th>尝试</th>
<th>执行节点</th><th>MCP</th><th>耗时</th><th>创建时间</th></tr>{rows}</table></div>
"""
    return layout("执行记录", "/runs", body, msg=ctx.get("msg"), err=ctx.get("err"))


RUN_JS = r"""
const box = document.getElementById('logbox');
const statusEl = document.getElementById('logstate');
let after = __AFTER__;
let terminal = __TERMINAL__;
function line(l) {
  const d = document.createElement('div');
  const t = document.createElement('span'); t.className='t';
  t.textContent = new Date(l.ts*1000).toLocaleTimeString('zh-CN', {hour12:false});
  const lv = document.createElement('span'); lv.className='lvl-'+l.level;
  lv.textContent = ('[' + (l.level||'info').toUpperCase() + '] ').padEnd(8);
  const src = document.createElement('span'); src.className='src';
  src.textContent = (l.source || 'agent') + ':';
  const m = document.createElement('span'); m.textContent = l.message;
  d.append(t, lv, src, m); box.appendChild(d);
}
async function poll() {
  let data = {logs: [], state: '', terminal: false};
  try {
    const r = await fetch('/api/runs/__TASK_ID__/logs?after=' + after, {cache:'no-store'});
    data = await r.json();
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    (data.logs || []).forEach(line);
    after = data.last_id || after;
    terminal = !!data.terminal;
    statusEl.textContent = terminal ? ('已结束 · ' + (data.state || '')) : ('实时刷新中 · ' + (data.state || ''));
    if (nearBottom) box.scrollTop = box.scrollHeight;
  } catch (e) {
    statusEl.textContent = '日志拉取失败，重试中…';
  }
  if (!terminal || (data.logs || []).length >= 2000) setTimeout(poll, 1000);
}
poll();
"""


def run_detail(ctx):
    run = ctx["run"]
    result = run.get("result") or {}
    steps = result.get("steps") or []
    mode = (run.get("case") or {}).get("mode") or "scripted"
    step_rows = ""
    for i, st in enumerate(steps, 1):
        ok = bool(st.get("ok"))
        res = st.get("result") or ""
        short = res[:300]
        extra = f"<details><summary>完整返回（{len(res)} 字符）</summary><pre>{esc(res)}</pre></details>" if len(res) > 300 else ""
        thought = st.get("thought") or ""
        thought_html = (f'<details><summary>{esc(thought[:180])}</summary><pre>{esc(thought)}</pre></details>'
                        if len(thought) > 180 else esc(thought) or "-")
        tool = st.get("tool", "")
        tool_label = "finish" if tool == "__finish__" else tool
        image = st.get("screenshot") or {}
        if isinstance(image, dict) and image.get("file"):
            url = '/api/runs/' + quote(run['task_id'], safe='') + '/screenshots/' + quote(image['file'], safe='')
            shot = (f'<a href="{esc(url)}" target="_blank" rel="noopener">'
                    f'<img class="step-shot" src="{esc(url)}" loading="lazy" alt="第 {i} 步执行后截图">'
                    '<span class="hint">点击查看原图</span></a>'
                    f'<div class="hint">执行后 · {fmt_ts(image.get("captured_at"))}</div>')
        elif isinstance(image, dict) and image.get("error"):
            shot = '<div class="hint">未能截图：' + esc(image['error']) + '</div>'
        else:
            shot = '<span class="muted">—</span>' if tool == '__finish__' else '<span class="muted">未采集</span>'
        step_rows += f"""<tr class=steps><td>{i}</td><td class=mono>{esc(tool_label)}</td>
<td style="max-width:300px;word-break:break-word;font-size:12px">{thought_html}</td>
<td class=mono style="max-width:340px;word-break:break-all">{esc(json.dumps(st.get('args') or {}, ensure_ascii=False))}</td>
<td>{'<span class="badge done">通过</span>' if ok else '<span class="badge failed">失败</span>'}</td>
<td class=mono style="max-width:420px;word-break:break-all">{esc(short)}{extra}</td>
<td class="shot-cell">{shot}</td></tr>"""
    if not step_rows:
        step_rows = "<tr><td colspan=7 class=empty>暂无步骤明细（任务未开始或结果未回传）。</td></tr>"
    err = result.get("error") or run.get("error") or ""
    final_text = result.get("final_text") or ""
    js = RUN_JS.replace("__TASK_ID__", run["task_id"]).replace("__AFTER__", "0").replace(
        "__TERMINAL__", "true" if run["state"] in ("done", "failed") else "false")
    mcp_name = (run.get("mcp") or {}).get("name") or run.get("mcp_id") or "-"
    verdict = result.get("verdict", "") or ""
    verdict_panel = ""
    if verdict:
        conf = result.get("confidence")
        conf_txt = f"{conf:.2f}" if isinstance(conf, (int, float)) else "-"
        reason = result.get("reason") or ""
        evidence = result.get("evidence") or ""
        vclass = {"passed": "ok", "failed": "err", "bug_suspected": "err",
                  "inconclusive": "info"}.get(verdict, "info")
        judge_status = result.get("judge_status") or ""
        judge_txt = {"ok": "正常", "failed": "调用失败", "invalid": "输出无效",
                     "skipped": "未执行", "not_run": "未执行"}.get(judge_status, "-")
        judge_card = (f'<div class=card><span>判定器</span><b>{esc(judge_txt)}</b></div>'
                      if judge_status else "")
        judge_note = ""
        if judge_status and judge_status != "ok":
            actor = result.get("actor_verdict") or "-"
            judge_note = (f'<div class="msg info"><b>判定器未完成独立判定：</b>'
                          f'{esc(result.get("judge_error") or "未执行")}；'
                          f'执行者结论 {esc(actor)} 仅供参考，不计为通过。</div>')
        cost = result.get("cost") or {}
        cost_card = ""
        cost_note = ""
        if cost:
            cost_card = (f'<div class=card><span>估算成本</span><b>'
                         f'{esc(budget.format_cost(cost.get("estimated"), cost.get("currency")))}'
                         f'</b></div>')
            tokens = cost.get("tokens") or {}
            cost_note = (f'<div class=hint>Token：缓存命中 '
                         f'{tokens.get("cache_hit", 0):,} / 未命中 '
                         f'{tokens.get("cache_miss", 0):,} / 输出 '
                         f'{tokens.get("output", 0):,}；规则：'
                         f'{esc(cost.get("rule") or "-")}。估算值，实际以账单为准。</div>')
        verdict_panel = f"""
<div class=panel><h2>AI 判定</h2>
<div class=cards>
  <div class=card><span>结论</span><b style="font-size:18px">{verdict_badge(verdict)}</b></div>
  <div class=card><span>置信度</span><b>{esc(conf_txt)}</b></div>
  <div class=card><span>模型</span><b style="font-size:14px">{esc(result.get('ai_model') or '-')}</b></div>
  {cost_card}
  {judge_card}
</div>
{judge_note}
{cost_note}
<div class="msg {vclass}"><b>判定理由：</b>{esc(reason) or '-'}</div>
<h2 style="margin-top:8px">证据</h2><pre>{esc(evidence) or '（未引用证据）'}</pre>
<div class=hint>AI 判定是对照自然语言预期的辅助结论；“疑似 BUG”建议人工复核后再定性。</div>
</div>"""
    freeze_form = ""
    if mode == "ai" and steps:
        freeze_form = (f'<form class=inline method=post action="/runs/{esc(run["task_id"])}/freeze">'
                       f'<button class=btn>固化为脚本用例</button></form>')
    profile = ctx.get("profile") or {}
    profile_panel = ""
    summary = result.get("profile_summary") or {}
    if run.get("kind") == "capture" or run.get("profile_id"):
        if run.get("kind") == "capture":
            target = profile.get("name") or run.get("profile_id") or "-"
            capture_result = "成功" if result.get("ok") else "失败"
            profile_panel = f"""
<div class=panel><h2>环境采集</h2>
<div class=cards>
  <div class=card><span>目标 Profile</span><b style="font-size:15px">{esc(target)}</b></div>
  <div class=card><span>采集结果</span><b style="font-size:16px">{esc(capture_result)}</b></div>
  <div class=card><span>Cookies</span><b>{summary.get('cookie_count', 0)}</b></div>
  <div class=card><span>Origins</span><b>{summary.get('origin_count', 0)}</b></div>
</div>
<div class=hint>成功后生成新的 Profile 版本；失败会保留旧版本。原文不会写入运行记录。</div>
</div>"""
        else:
            name = profile.get("name") if profile else run.get("profile_id")
            version = profile.get("version") if profile else "-"
            profile_panel = f"""
<div class=panel><h2>执行环境 Profile</h2>
<div class=cards>
  <div class=card><span>Profile</span><b style="font-size:15px">{esc(name or '-')}</b></div>
  <div class=card><span>版本</span><b>{esc(version)}</b></div>
  <div class=card><span>Hash</span><b class=mono style="font-size:12px">{esc((run.get('profile_hash') or '-')[:16])}</b></div>
</div>
<div class=hint>该运行启动时加载了 Profile 快照；运行中产生的状态变化不会回写。</div>
</div>"""
    remote_panel = ""
    if run.get("connection") == "pull":
        remote_panel = (
            '<div class=panel><h2>远程执行来源</h2><table>'
            f'<tr><th>执行机</th><td class=mono>{esc(run.get("machine_id") or "-")}</td></tr>'
            f'<tr><th>节点</th><td class=mono>{esc(run.get("node_id"))}</td></tr>'
            f'<tr><th>实例</th><td class=mono>{esc(run.get("instance_id"))}</td></tr>'
            f'<tr><th>执行批次</th><td class=mono>{esc(run.get("dispatch_id"))}</td></tr></table>'
            '<div class=hint>待回传表示执行状态尚待确认；人工重跑前请确认旧任务是否仍在执行。</div></div>')
    body = f"""
<div class=toolbar><a class=btn href="/runs">← 返回执行记录</a><span class=spacer></span>
{freeze_form}<a class=btn href="/cases">用例管理</a></div>
<div class=cards>
  <div class=card><span>状态</span><b style="font-size:18px">{badge(run['state'])}</b></div>
  <div class=card><span>模式</span><b style="font-size:16px">{'AI' if mode == 'ai' else '脚本'}</b></div>
  <div class=card><span>尝试次数</span><b>{run['attempt']}</b></div>
  <div class=card><span>耗时</span><b style="font-size:18px">{fmt_ms(run.get('elapsed_ms'))}</b></div>
  <div class=card><span>执行节点</span><b style="font-size:15px">{esc(run['agent_id'] or '-')}</b></div>
  <div class=card><span>MCP</span><b style="font-size:15px">{esc(mcp_name)}</b></div>
  <div class=card><span>创建</span><b style="font-size:13px">{fmt_ts(run['created'])}</b></div>
</div>
{remote_panel}
{profile_panel}
{verdict_panel}
<div class=panel><h2>执行结果</h2>
<div class=hint style="margin-bottom:8px">任务 <span class=mono>{esc(run['task_id'])}</span>
｜ 用例 <span class=mono>{esc(run.get('name',''))}</span>
｜ 错误：<span style="color:#c0392b">{esc(err) or '-'}</span></div>
<div class="hint" style="margin-bottom:8px">截图记录每步执行后的当前视口，包括失败步骤；点击缩略图可查看原图。历史运行不会补拍截图。</div>
<div class="steps-scroll"><table><tr><th style="width:45px">#</th><th style="width:150px">工具</th><th style="width:200px">AI 思考</th>
<th>参数</th><th style="width:70px">结果</th><th>返回值</th><th>执行截图</th></tr>{step_rows}</table></div>
{('<h2 style="margin-top:18px">页面正文</h2><pre>' + esc(final_text) + '</pre>') if final_text else ''}
</div>
<div class=panel><h2>执行日志</h2>
<div class=toolbar><span id=logstate class=muted>加载中…</span><span class=spacer></span>
<span class=muted>执行节点上报 + 平台调度事件</span></div>
<div id=logbox class=logs></div>
</div>
<script>{js}</script>
"""
    return layout(f"运行详情 · {run['task_id']}", "/runs", body, msg=ctx.get("msg"), err=ctx.get("err"))
