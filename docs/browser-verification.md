# 百度 / 淘宝两次运行的拦截诊断

检查时间：2026-11-10（北京时间）。数据来自 runtime/platform.db 最新两次运行及其逐步截图。本轮没有重新访问目标站点，也未修改当前 MCP 注册表、用例、登录状态或运行结果。

## 已确认的现场

| 运行 | 时间 | 证据 | 结论 |
| --- | --- | --- | --- |
| T002-8f92：百度未登录搜索 | 22:45:46 | 首页、输入和点击成功；第 5 步跳到 wappass.baidu.com/static/captcha/tuxing_v2.html；截图明确显示“百度安全验证”“拖动左侧滑块使图片为正”；第 7 步直接打开搜索地址仍跳验证页 | 搜索过程被服务端验证流程阻塞，无法观察结果，inconclusive 合理 |
| T001-18df：淘宝未登录搜索 | 22:40:07 | 第 4 / 10 步点击被 J_MIDDLEWARE_FRAME_WIDGET 拦截；截图为登录框；第 6 / 7 步发现 _____tmd_____/punish 验证 iframe 和 login.taobao.com 登录 iframe；一度同时出现 5 层，关闭后重新出现 | 登录要求和风控验证同时存在，5000ms 点击超时是遮挡的结果 |

淘宝执行器反复关闭浮层，并在第 12 步用 DOM remove 删除遮罩，最终耗尽 max_steps=12。删除展示元素不会使服务端验证成功，也会改变实际测试场景。应在明确识别验证码/风控或登录门禁时结束为 inconclusive 并保留证据，而不是反复关闭/删除遮罩。

## 能确认与不能确认的原因

两条运行的 MCP 参数均为 `npx -y @playwright/mcp@latest --headless`。无头模式、实际浏览器版本/自动化信号、IP/网络信誉、会话状态、访问行为都可能参与站点风控，但本地记录没有站点规则命中日志、出口 IP 或完整请求响应，不能指定其中某一项为根因，更不能保证改一个参数便可通过。

官方 Playwright MCP 默认支持持久 profile，因此不能仅根据“一任务一个 MCP 进程”推断每次都是空 Cookie。未登录用例和截图只说明此次页面处于未登录/要求验证的状态。MCP 与日常人工 Chrome 也不会自动共享同一登录状态。

## 通用交互配置

配置文件：`configs/playwright-interactive.json`；平台 MCP 配置模板：`configs/playwright-interactive.mcp.json`。它们是普通浏览器调试、人工验证和会话复用配置，没有网站通用的“免验证”保证。

- 使用本机 Chrome 的有界面模式，便于看到真实弹窗并由操作者手动完成网站要求的验证。
- 使用项目独立目录 `runtime/browser-profiles/interactive-1` 保存合法的会话状态，不接管日常浏览器个人资料。
- 中文语言、上海时区、固定视口用于让测试环境明确、可复现；不伪造 UA / webdriver，也不自动处理验证码。
- 单个 profile 同时只跑一个任务。平台按 Agent 总容量调度，不能为某个 MCP 单独限流：验证时让其他 Agent 离线，只保留一台 slots=1 的执行机。并行运行需要每个浏览器实例使用独立 profile。
- 默认 MCP 已固定为本机已安装的 0.0.85；配置参数按该版本的本地文档与官方配置定义核对。

在 MCP 管理页新建记录（不用覆盖原有官方 MCP）：

```text
ID: playwright-interactive
名称: Playwright MCP（有界面 / 独立持久会话）
传输: stdio
命令: npx
```

参数 JSON：

```json
["-y", "@playwright/mcp@0.0.85", "--config", "{here}/configs/playwright-interactive.json"]
```

不要再附加 `--headless`，CLI 参数会覆盖配置文件。保存、探测工具后，在用例中选择新 MCP。只改内置浏览器源码的 headless 对这两条使用官方 MCP 的用例无效。

## 手工建立合法会话

如需先人工处理网站要求的验证，可在项目根目录 PowerShell 执行：

```powershell
$browserProfile = Join-Path (Get-Location) 'runtime\browser-profiles\interactive-1'
& 'C:\Program Files\Google\Chrome\Application\chrome.exe' "--user-data-dir=$browserProfile" 'https://www.baidu.com/'
```

由操作者完成必要验证；如测试前置条件要求登录，由操作者使用授权测试账号登录。之后关闭这个独立 Chrome 的所有窗口，再让 MCP 使用同一个目录。验证是否仍然有效由站点决定；此配置不能替代站点的访问权限、人工验证码或测试环境白名单。

注意：原淘宝用例要求“未登录用户”。登录后搜索成功不能证明原用例通过；应将“未登录访问行为”和“已登录搜索行为”分成不同用例，使用不同 profile。不要让不同前置条件共用会话。

当前 Agent 没有“暂停并等待人工确认”功能；单纯延长 timeout 不会产生人工接管流程。手工会话准备应在运行前完成；运行时再次遇到验证就记录为 inconclusive。

## 可追加到用例指令的规则

```text
发现验证码、安全验证、访问受限或必须登录才能继续时，保存现有证据并结束，
返回 inconclusive，说明阻塞发生在哪一步。
不得删除验证/登录遮罩、强制点击或持续重试验证页面。
普通可关闭的非验证弹窗可以关闭一次；重复出现时重新判断是否为门禁。
保持用例原有的“未登录 / 已登录”前置条件。
```

## 验证边界

本轮仅验证配置能够被本机官方 Playwright MCP 加载；未运行百度/淘宝重试，未完成其人工验证码，因此没有“已经绕过”的实测结论。

参考：
- https://github.com/microsoft/playwright-mcp#user-profile
- https://github.com/microsoft/playwright-mcp#configuration-file
- https://playwright.dev/docs/auth
