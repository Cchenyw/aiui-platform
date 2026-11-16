# -*- coding: utf-8 -*-
"""生成交付文档：AI UI 自动化测试 平台化调度 技术说明（docx）"""
import os
from pathlib import Path

try:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement, parse_xml
    from docx.oxml.ns import nsdecls, qn
    from docx.shared import Cm, Pt, RGBColor
except ImportError as e:  # 可选依赖，不在 requirements.txt 里
    raise SystemExit("生成 docx 需要 python-docx：python -m pip install python-docx") from e

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
OUT = ARTIFACTS / "AIUI平台化调度_技术说明_v1.0.docx"

REQUIRED_FIGURES = ("arch-aiui-platform.png", "seq-aiui-dispatch.png",
                    "dashboard_running.png", "dashboard_done.png")
missing = [name for name in REQUIRED_FIGURES if not (ARTIFACTS / name).exists()]
if missing:
    raise SystemExit("缺少文档图片，请先准备到 artifacts/：" + "、".join(missing)
                     + "（看板图可用 scripts/shot_dashboard.py 生成）")

INK = RGBColor(0x2D, 0x31, 0x42)
MUTED = RGBColor(0x6B, 0x70, 0x80)
ACCENT = RGBColor(0xBF, 0x45, 0x20)

doc = Document()

# ── 页面 ───────────────────────────────────────────────
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.top_margin = Cm(2.2); sec.bottom_margin = Cm(2.0)
sec.left_margin = Cm(2.2); sec.right_margin = Cm(2.2)

# ── 默认样式 ───────────────────────────────────────────
st = doc.styles["Normal"]
st.font.name = "宋体"; st.font.size = Pt(10.5)
st._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
st.paragraph_format.line_spacing = 1.55
st.paragraph_format.space_after = Pt(4)


def set_run(run, size=10.5, bold=False, color=INK, font="宋体", italic=False):
    run.font.name = font
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = color
    for k in ("w:eastAsia", "w:ascii", "w:hAnsi"):
        run._element.rPr.rFonts.set(qn(k), font)
    return run


def para(text="", size=10.5, bold=False, color=INK, font="宋体", align=None,
         before=0, after=4, indent=0, spacing=1.55, italic=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = spacing
    if indent:
        p.paragraph_format.first_line_indent = Cm(indent)
    if align is not None:
        p.alignment = align
    if text:
        set_run(p.add_run(text), size, bold, color, font, italic)
    return p


def h1(text, num=None):
    para("", after=2)
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.keep_with_next = True
    if num:
        set_run(p.add_run(num + "  "), 15, True, ACCENT, "黑体")
    set_run(p.add_run(text), 15, True, INK, "黑体")
    # 下划细线
    pPr = p._p.get_or_add_pPr()
    pPr.append(parse_xml(r'<w:pBdr {}><w:bottom w:val="single" w:sz="6" w:color="E2DFD6"/></w:pBdr>'.format(nsdecls("w"))))
    return p


def h2(text, num=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    if num:
        set_run(p.add_run(num + "  "), 11.5, True, MUTED, "黑体")
    set_run(p.add_run(text), 11.5, True, INK, "黑体")
    return p


def bullet(text, size=10.5):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.7)
    p.paragraph_format.first_line_indent = Cm(-0.35)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.5
    set_run(p.add_run("· "), size, True, ACCENT)
    set_run(p.add_run(text), size)
    return p


def shade(cell, hexcolor):
    cell._tc.get_or_add_tcPr().append(
        parse_xml(r'<w:shd {} w:val="clear" w:fill="{}"/>'.format(nsdecls("w"), hexcolor)))


def table(headers, rows, widths, header_fill="F5F3EE", size=8.8, first_col_bold=False):
    MAXW = 16.4                                # 页面可用宽度（A4 21cm − 左右各 2.2cm）
    tot = sum(widths)
    if tot > MAXW:                             # 超宽自动等比压缩，避免表格溢出版心
        widths = [w * MAXW / tot for w in widths]
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        p = c.paragraphs[0]
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        set_run(p.add_run(h), size, True, INK, "黑体")
        shade(c, header_fill)
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.25
            set_run(p.add_run(str(v)), size, first_col_bold and i == 0)
    for row in t.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)
    return t


def figure(path, caption, width=16.2):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(3)
    p.add_run().add_picture(str(path), width=Cm(width))
    c = doc.add_paragraph()
    c.alignment = WD_ALIGN_PARAGRAPH.CENTER
    c.paragraph_format.space_after = Pt(10)
    set_run(c.add_run(caption), 9, False, MUTED, "黑体")


def page_break():
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


# ══════════════ 封面 ══════════════
para("", after=60)
para("技术说明 · TECHNICAL NOTE", 9.5, False, MUTED, "黑体", after=10)
p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(2)
set_run(p.add_run("AI UI 自动化测试"), 27, True, INK, "黑体")
p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(12)
set_run(p.add_run("平台化调度设计与验证"), 27, True, INK, "黑体")

p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(26)
set_run(p.add_run("控制面 / 执行面解耦 · MCP 工具层 · 任务队列与执行机池"), 12, False, MUTED, "宋体")

table(["项", "内容"],
      [["文档版本", "v1.0"],
       ["验证环境", "Linux 4 核 / 3.6 GB，无 GPU；Chromium headless"],
       ["技术栈", "Python 3.11 + MCP SDK 2.0（stdio）+ Playwright 1.62"],
       ["验证结论", "11 条用例：通过 10 / 失败 1（预期失败），并发峰值 2/2，平均耗时 2.28 s"],
       ["编写日期", "2026-11-02"]],
      widths=[3.4, 13.0], size=9.5, first_col_bold=True)

para("", after=34)
para("本文件描述一个可运行的平台化调度 demo：从用例提交、执行机分配、MCP 工具调用、"
     "浏览器执行到结果回传与审计落盘，并记录每个关键技术决策的选型理由与代价。",
     10.5, False, MUTED, "宋体", indent=0, spacing=1.8)

page_break()

# ══════════════ 一、背景与目标 ══════════════
h1("项目背景与目标", "一")
h2("现状痛点", "1.1")
para("此前的 AI UI 测试验证停留在“单机可跑”：脚本能执行、模型能规划，但一旦要多人共用、多机并发、"
     "失败复盘，就暴露出三个结构性问题。", indent=0.74)
bullet("用例无法排队与限流：谁先跑、跑几台、超了怎么办，全靠人工吼。")
bullet("失败无法归因：只拿到一个 pass/fail，不知道是元素没找到、文案变了，还是环境慢。")
bullet("执行机状态不可见：掉线、卡死、残留进程，平台无从感知，任务静默丢失。")

h2("本期目标", "1.2")
para("用一个最小但真实的 demo 回答一个问题：控制面与执行面解耦之后，调度这件事能不能被“看见”和“管住”。"
     "具体落成五项能力：执行机自注册与心跳、任务队列与优先级、容量感知的最小负载分配、失败重试与死信、"
     "全链路审计与看板。", indent=0.74)

h2("验证范围", "1.3")
para("本文所有数据来自本机实跑（单机、双容量、11 条用例），不含多机压测与生产环境数据；"
     "未实现项在第七章明确列出。", indent=0.74)

# ══════════════ 二、总体架构 ══════════════
h1("总体架构", "二")
h2("四域划分", "2.1")
para("系统按职责切成四个域，任一域都可以单独替换而不影响其它域：", indent=0.74)
table(["域", "组成", "职责", "可替换性"],
      [["控制面", "测试平台 :8000", "执行机注册表、任务队列、调度器、结果解析、审计、看板", "可换 FastAPI + Redis 队列"],
       ["执行面", "执行机 Agent :8001…:800N", "注册、心跳、接任务、托管子进程、回传结果", "可换 MQ 消费端 / K8s Job"],
       ["工具层", "MCP 服务（stdio 子进程）", "navigate / type_text / click / expect_text / page_text", "可换 Selenium、自研驱动"],
       ["执行环境", "Chromium 实例 + 被测应用 :8081", "真实浏览器渲染、页面交互", "可换真机、云浏览器"]],
      widths=[2.4, 4.6, 6.6, 3.6], first_col_bold=True, size=8.8)

h2("架构图", "2.2")
figure(ARTIFACTS / "arch-aiui-platform.png", "图 1　平台化调度架构：四域与七条数据流（①心跳 ②下发 ③子进程 ④浏览器 ⑤驱动 ⑥回传 ⑦审计）")

h2("七条数据流的含义", "2.3")
table(["编号", "数据流", "实现方式", "设计要点"],
      [["①", "心跳注册", "POST /api/agents/register + 每 3s heartbeat", "执行机主动上报，平台不需要维护主机清单，也不需要扫网段"],
       ["②", "任务下发", "POST /task（HTTP，202 accepted）", "平台推模式：能立刻拿到接受/拒绝，便于改派"],
       ["③", "拉起子进程", "每任务一个 MCP 子进程（stdio）", "进程级隔离，失败用例可强杀"],
       ["④", "控制浏览器", "MCP 内部 Playwright 协议", "工具层屏蔽驱动差异"],
       ["⑤", "驱动页面", "Chromium 访问被测应用", "真实渲染，非接口模拟"],
       ["⑥", "结果回传", "POST /api/results（含逐步 trace）", "平台侧幂等，重复上报忽略"],
       ["⑦", "审计落盘", "append-only JSONL", "不可变事件流，可复盘可追溯"]],
      widths=[1.3, 2.6, 6.4, 6.9], size=8.8)

page_break()

# ══════════════ 三、调度时序 ══════════════
h1("调度时序", "三")
h2("一次调度的 15 步", "3.1")
para("下面是把代码路径摊平后的实际顺序，不是概念图。其中第 3、4 步是调度决策点，"
     "第 6、7、11、13 步决定隔离与可归因性。", indent=0.74)
table(["步", "动作", "要点"],
      [["1", "提交方 POST /api/tasks", "批量用例 + 优先级 + 重试上限"],
       ["2", "平台入队", "heapq(priority, seq)，审计 created"],
       ["3", "调度器 pick_agent", "在线 + 容量未满 + running 最小"],
       ["4", "平台 POST /task", "202 accepted；拒绝则改派"],
       ["5", "平台状态机 running", "审计 dispatched（含 attempt）"],
       ["6", "执行机拉起 MCP 子进程", "stdio，独立于其它任务"],
       ["7", "initialize / list_tools", "JSON-RPC 握手"],
       ["8", "call_tool(navigate)", "DOM 就绪即返回"],
       ["9", "MCP 调 Playwright", "page.goto"],
       ["10", "Chromium 请求被测页", "GET /login → 200"],
       ["11", "call_tool(type_text / click) ×3", "每步返回结构化结果"],
       ["12", "call_tool(expect_text)", '{"ok": true} 或 {"ok": false, "actual": "..."}'],
       ["13", "结果回传", "ok + steps[] + elapsed_ms"],
       ["14", "平台状态机 done", "审计 done"],
       ["15", "写 audit.jsonl", "事件流落盘，供看板与复盘"]],
      widths=[1.2, 6.4, 9.6], size=8.8)

h2("时序图", "3.2")
figure(ARTIFACTS / "seq-aiui-dispatch.png", "图 2　一次调度的完整时序（含失败重试分支）", width=14.6)

h2("两条必须守住的约束", "3.3")
para("这两条在实现里最容易被忽略，也是“平台化”与“脚本串联”的分水岭。", indent=0.74)
bullet("心跳超时要回收在飞任务：执行机掉线时，它手上 running 状态的任务永远不会回结果；"
       "不回收就会出现“队列看着空了、整体永远不完成”的幽灵任务。做法是超时 10s 摘除执行机并把其任务重新入队。")
bullet("结果回传必须幂等：执行机可能因网络超时重发结果，重复回传会让 attempt 与计数错乱。"
       "校验放在平台侧（执行机不可信），同一 task_id 且已终态的直接忽略。")

page_break()

# ══════════════ 四、关键技术决策 ══════════════
h1("关键技术决策", "四")
h2("决策总览", "4.1")
para("十五条决策，每条都给出理由与代价。带 ★ 的三条是最能体现判断力的部分，4.2 单独展开。", indent=0.74)
table(["#", "决策", "选型", "原因", "代价"],
      [["1", "工具层协议", "MCP（JSON-RPC）", "工具层是契约不是实现，换驱动不改 Agent；每次调用即审计边界", "多一层进程与序列化，约 +0.3 s/任务"],
       ["2", "传输方式", "stdio", "生命周期与 client 绑定，无残留；不需端口，天然免鉴权", "只能同机部署"],
       ["3★", "任务隔离", "每任务一个子进程 + 独立 Chromium", "cookie/弹窗/坏状态零串扰，失败可强杀", "启动开销 0.5~1 s，内存 150~250 MB/实例"],
       ["4", "并发模型", "线程池 + 子进程", "线程里的坏状态杀不干净", "内存占用高，需物理限流"],
       ["5", "队列归属", "平台持队列", "集中才能做优先级、重试、容量分配", "平台单点，需持久化与主备"],
       ["6", "调度算法", "最小负载优先 + 容量上限", "轮询会把任务派给忙的机器", "未做耗时预测与加权公平"],
       ["7", "重试策略", "插回队首 + 上限 + 死信", "重试多为最长尾，放队尾拖慢整体", "失败多时可能抢占新任务"],
       ["8★", "掉线处理", "超时摘除 + 回收在飞任务", "否则产生幽灵任务，整体永不收敛", "有时间窗延迟（10 s）"],
       ["9", "结果处理", "平台侧幂等", "执行机不可信，网络会重发", "需维护终态判断"],
       ["10", "限流", "平台记账 + 执行机 semaphore", "平台记账可能乐观，执行机是最后一道闸", "参数需两处对齐"],
       ["11", "日志与错误", "stderr 输出；错误返回 JSON 不 raise", "stdout 是协议通道；raise 会破坏错误契约", "工具实现需统一包装"],
       ["12", "感知方式", "DOM/选择器，非视觉坐标", "成本低、不漂移、失败可读", "文案变更需维护，需 accessibility 兜底"],
       ["13", "Web 框架", "标准库 http.server", "零依赖、单文件可交付", "吞吐低，无连接池"],
       ["14", "看板", "服务端渲染 + meta refresh", "目的是一眼看状态，省构建链", "无交互、无历史趋势"],
       ["15", "安全默认", "只绑 127.0.0.1，无鉴权", "平台等于远程执行入口，不能默认对外", "跨机使用前必须补 token + 白名单（红线）"]],
      widths=[0.9, 2.6, 3.4, 5.7, 4.6], size=8.4)

h2("三条关键决策展开", "4.2")
h2("为什么要 MCP，而不是在 Agent 里直接调 Playwright", "4.2.1")
para("工具层是契约，不是实现。Agent 只认识 navigate / click / expect_text 这组语义，"
     "底层换 Playwright、Selenium 还是自研驱动，Agent 一行都不用改。"
     "更重要的是审计粒度：每次 call_tool 天然是一次“谁、何时、调了什么、结果如何”的记录，"
     "这正是后面接 LLM 做自愈重试所需要的失败上下文。代价是多一层进程与序列化，单任务约增加 0.3 秒。", indent=0.74)

h2("为什么每个任务要独立子进程与独立浏览器", "4.2.2")
para("会话隔离是测试红线。共享浏览器意味着 cookie、localStorage、弹窗状态互相污染，"
     "一条用例改了登录态，后面的用例就全错了；而失败用例最脏——残留的未完成等待、卡住的对话框，"
     "在线程里根本清不干净。进程级隔离的代价是每任务 0.5~1 秒启动开销和 150~250 MB 内存，"
     "换来的是可并行、可强杀、失败零污染。", indent=0.74)

h2("为什么必须回收“在飞任务”", "4.2.3")
para("执行机掉线的瞬间，它手上那些 running 状态的任务就变成了孤儿：平台还在等结果，"
     "执行机已经不存在。表现是队列长度归零、任务永远差几条不完成，而且看板上一切正常。"
     "正确做法是心跳超时（10 秒，三次未上报）立刻摘除执行机，把它名下 running 的任务重新入队并记审计事件。"
     "这是分布式调度里最不显眼、也最容易漏的一处。", indent=0.74)

page_break()

# ══════════════ 五、实测验证 ══════════════
h1("实测验证", "五")
h2("环境与参数", "5.1")
table(["项", "取值"],
      [["运行环境", "Linux 4 核 / 3.6 GB 内存，无 GPU，headless Chromium"],
       ["执行机", "1 台，容量 slots=2（模拟池化，设计支持多台）"],
       ["重试策略", "max_retry=2（同一任务最多尝试 3 次）"],
       ["用例数", "11 条（10 条正常 + 1 条故意失败）"],
       ["调度参数", "调度循环 150 ms；心跳 3 s；心跳超时 10 s"]],
      widths=[4.0, 12.4], size=9.2, first_col_bold=True)

h2("用例集与执行结果", "5.2")
table(["#", "用例", "断言", "结果"],
      [["1", "登录-管理员", "页面包含“登录成功”", "通过"],
       ["2", "登录-普通用户", "同上", "通过"],
       ["3", "登录-密码错误", "页面包含“密码错误”", "通过"],
       ["4", "登录-空用户名", "页面包含“请输入用户名”", "通过"],
       ["5", "登录-再次验证", "同上（验证重复执行稳定）", "通过"],
       ["6", "登录-采集正文", "断言 + page_text 采集", "通过"],
       ["7", "登录-不同文案", "断言“登录成功”", "通过"],
       ["8~10", "登录-回归 1/2/3", "同上（并发展示）", "通过"],
       ["11", "异常-元素不存在（预期失败）", "点击 #does-not-exist", "失败（符合预期）"]],
      widths=[1.4, 5.6, 6.6, 3.2], size=8.8)

h2("结果数据", "5.3")
table(["指标", "实测值", "说明"],
      [["任务总数", "11", "全部进入终态，无卡死"],
       ["通过 / 失败", "10 / 1", "失败为设计内用例"],
       ["通过率", "90.9%", "—"],
       ["并发峰值", "2 / 2", "容量占满，验证限流生效"],
       ["单任务耗时", "2.16 s / 2.28 s / 3.11 s（最小/中位/最大）", "含子进程与浏览器启动"],
       ["重试次数", "2", "同一失败任务重试至上限后进死信"],
       ["审计事件", "created 11 / dispatched 13 / done 10 / retry 2 / failed 1", "事件数可反推调度行为"]],
      widths=[3.4, 7.4, 6.6], size=8.8, first_col_bold=True)

h2("调度看板", "5.4")
figure(ARTIFACTS / "dashboard_running.png", "图 3　执行中的看板：队列、执行中、重试、通过、失败五态，容量占用 2/2", width=13.8)
figure(ARTIFACTS / "dashboard_done.png", "图 4　完成态看板：11 条用例全部收敛，失败任务标注原因", width=13.8)

h2("失败归因示例", "5.5")
para("失败任务在平台侧可查到完整 trace 与错误文本，不需要去翻执行机日志：", indent=0.74)
p = doc.add_paragraph()
p.paragraph_format.left_indent = Cm(0.8)
p.paragraph_format.space_after = Pt(6)
set_run(p.add_run('T011-0232　click 失败(#does-not-exist): TimeoutError: Page.click: Timeout 8000ms exceeded.\n'
                  'attempt 0 → failed → retry(1) → retry(2) → failed（进入死信）'),
        9.5, False, RGBColor(0x40, 0x46, 0x5A), "宋体")

page_break()

# ══════════════ 六、交付物与运行 ══════════════
h1("交付物与运行方式", "六")
h2("文件清单", "6.1")
table(["文件", "角色", "端口"],
      [["aiui/platform/server.py", "控制面：执行机池、队列、调度、重试、审计、看板", "8000"],
       ["aiui/agent/runner.py", "执行面：注册、心跳、接任务、托管子进程、回传", "8001"],
       ["aiui/toolservers/builtin_browser.py", "工具层：5 个浏览器工具（stdio JSON-RPC）", "—"],
       ["aiui/testapp/app.py", "被测示例页（登录表单）", "8081"],
       ["scripts/run_demo.py", "一键演示：起服务 → 下发 → 观察 → 强制收尾", "—"],
       ["scripts/shot_dashboard.py", "看板效果图采集（跑完自动关进程）", "—"],
       ["scripts/stop_all.py", "按端口清理残留进程（Windows/Linux 通用）", "—"],
       ["启动.bat / 停止.bat", "Windows 一键起停", "—"],
       ["README.md", "15 条决策表与使用说明", "—"],
       ["artifacts/", "架构图、时序图、看板截图（运行时生成）", "—"]],
      widths=[5.0, 9.4, 2.0], size=8.8)

h2("运行步骤", "6.2")
para("Windows：", indent=0.74, bold=True)
p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.8)
set_run(p.add_run("pip install mcp playwright\nplaywright install chromium\npython scripts/run_demo.py"),
        9.5, False, RGBColor(0x40, 0x46, 0x5A), "宋体")
para("Linux / macOS：把 python 换成 python3，其余相同。想手动看界面用 python scripts/run_demo.py --keep，"
     "浏览器打开 http://127.0.0.1:8000/ ，Ctrl+C 结束并自动清理。", indent=0.74)

h2("收尾与安全边界", "6.3")
para("三个进程都会开监听端口，验证完必须关闭。scripts/run_demo.py 在 finally 里无条件 terminate 全部进程，"
     "并打印端口释放情况；分离启动的场景执行 python scripts/stop_all.py（按端口查 PID，Windows/Linux 通用）。"
     "本次验证结束后已确认 8081/8000/8001 全部释放、无残留进程、无残留 Chromium。", indent=0.74)
para("安全边界：平台与执行机均只绑 127.0.0.1。平台能向下发任务到执行机，等于远程执行入口，"
     "跨机使用前必须先补 token 鉴权与 IP 白名单——这是红线，不是待办。", indent=0.74, color=MUTED)

# ══════════════ 七、边界与下一步 ══════════════
h1("边界与下一步", "七")
h2("本版明确未做", "7.1")
table(["未做项", "影响", "补齐方式"],
      [["LLM 规划器", "用例是结构化步骤，不是自然语言", "在执行机准备 steps 处接模型；失败 trace 已备好可做自愈重试"],
       ["持久化", "队列与注册表在内存，重启即丢", "接 Redis/RabbitMQ + 任务表"],
       ["鉴权与白名单", "仅限本机验证", "token + IP 白名单，前置反向代理"],
       ["多机实测", "设计支持，未压测", "多挂执行机实例，观察分配均衡与心跳摘除"],
       ["产物（截图/trace 文件）", "只回传文本 trace", "MCP 加 screenshot 工具，结果带对象存储路径"]],
      widths=[4.0, 6.4, 6.0], size=8.8)

h2("落地建议（三步）", "7.2")
para("第一步：换掉内存队列（Redis List / RabbitMQ），平台可重启、任务不丢；同时把审计接进 ES 或 ClickHouse，"
     "让“查一次为什么失败”变成一次检索。", indent=0.74)
para("第二步：加鉴权与多执行机，把“容量”做成可观测指标（利用率、排队时长、P95 耗时），"
     "这时平台才真的能回答“为什么今天这么慢”。", indent=0.74)
para("第三步：接 LLM 规划器，用已沉淀的失败 trace 做自愈重试——不是让模型凭空生成用例，"
     "而是让它在已知失败上下文里做有限修正，这条路比“AI 从零写用例”可靠得多。", indent=0.74)

# ══════════════ 附录 ══════════════
h1("附录：接口清单", "附")
table(["方法", "路径", "说明"],
      [["POST", "/api/agents/register", "执行机注册（agent_id / host / port / slots / tags）"],
       ["POST", "/api/agents/heartbeat", "心跳 + 当前负载"],
       ["POST", "/api/tasks", "批量下发 {cases, priority, max_retry}"],
       ["GET", "/api/status", "队列 / 执行机 / 统计汇总"],
       ["GET", "/api/tasks", "任务列表（状态、尝试次数、耗时）"],
       ["GET", "/api/trace?task_id=", "单任务完整 trace（逐步 tool / args / result）"],
       ["POST", "/api/results", "执行机回传结果（幂等）"],
       ["GET", "/", "调度看板（2 s 自动刷新）"]],
      widths=[1.6, 6.4, 8.4], size=8.8)

# ── 页脚 ───────────────────────────────────────────────
footer = sec.footer
fp = footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
set_run(fp.add_run("AI UI 自动化测试 · 平台化调度技术说明 v1.0　｜　"), 8.5, False, MUTED)
r = fp.add_run()
fld = OxmlElement("w:fldSimple")
fld.set(qn("w:instr"), "PAGE")
fp._p.append(fld)

ARTIFACTS.mkdir(parents=True, exist_ok=True)
doc.save(OUT)
print("OK", OUT, os.path.getsize(OUT), "bytes")
