# Windows 远程执行服务器接入预研

目标：一个父平台统筹，2–3 台 Windows 执行服务器主动领取任务、运行 MCP/浏览器、回传日志与截图。本文为 2026-11-15 预研交付；公司网络及服务器现场验证结果需要另外记录。

## 1. 部署关系

父平台的管理页面默认 `http://127.0.0.1:8000`；执行网关使用单独的 `8010` 端口。执行服务器只需要主动访问网关，不需要开放 8001 等任务接收端口。原有本地执行节点继续使用现有方式。

`machine_id` 是服务器标识，`node_id` 是执行进程标识；首轮每服务器部署一个节点、slots=1。多个节点必须使用不同 ID、凭证和状态目录。

远程接口不提供管理页面、用例修改、LLM 设置或父平台进程启动能力。先用父平台本机浏览器管理；远程管理页面发布和公司统一登录后续设计。

## 2. 周一接入前准备

请先确认以下现场信息，不要将示例 IP 当成真实地址：

| 项目 | 需要填写的值 |
| --- | --- |
| 父平台服务器/DNS | 执行服务器可以访问的地址；HTTPS 证书 SAN 与其匹配 |
| 执行机来源 IP | 以父平台实际看到的出站 IP 为准，配置到白名单；有 NAT 时填写 NAT 后的来源 |
| Windows 版本 | 当前 Playwright 官方要求 Windows Server 2019+；更旧系统需验证兼容版本 |
| 运行账号 | 项目目录、runtime 状态目录、浏览器目录的读写权限 |
| 浏览器/Node/npm | 首轮内置 MCP 只需 Python Playwright；官方 MCP 另需 Node/npm 及匹配浏览器 |
| LLM | 每台执行机自行配置地址、模型、密钥和出口；先跑脚本用例再验证 AI |
| 被测网站 | 执行服务器实际可访问的 URL，含内部 DNS/代理要求 |

官方参考：[Playwright 系统要求](https://playwright.dev/python/docs/intro)、[浏览器安装与代理](https://playwright.dev/python/docs/browsers)、[Python TLS/CA 配置](https://docs.python.org/3.12/library/ssl.html)。

建议现场使用 Python 3.10+（3.12 / 3.13 / 3.15 均可），创建自己的虚拟环境；部署脚本默认用 `py -3` 选择最新已安装的 Python 3，也可用 `-PythonVersion` / `-PythonPath` 指定。当前个人电脑验证环境为 Python 3.15.2 / mcp 2.3.0 / Playwright 1.65.0；本机验证不代表已覆盖所有 Windows Server 和 Python 组合。**不要复制个人电脑的 `.venv`**，原生扩展的 Python ABI 和系统环境可能不同。

```powershell
Set-Location 'C:\AIUI_test_v4'  # 示例目录
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m playwright install chromium
```

浏览器应由实际运行节点的账号安装。如果使用固定浏览器安装目录，请给该账号授权，并在节点配置中显式设置 `chromium` 路径。公司离线部署需要在相同 Windows/Python 架构环境准备依赖包和浏览器文件，不直接复用本机 `.venv`。

## 3. 父平台生成节点凭证

在父平台项目根目录逐个执行；下面地址都是示例，替换为公司执行服务器来源 IP：

```powershell
.\.venv\Scripts\python.exe scripts/create_node_credentials.py `
  --node-id win-exec-01 --machine-id win-server-01 `
  --allowed-ip 10.20.30.21/32 --output-dir runtime/remote-access

.\.venv\Scripts\python.exe scripts/create_node_credentials.py `
  --node-id win-exec-02 --machine-id win-server-02 `
  --allowed-ip 10.20.30.22/32 --output-dir runtime/remote-access

.\.venv\Scripts\python.exe scripts/create_node_credentials.py `
  --node-id win-exec-03 --machine-id win-server-03 `
  --allowed-ip 10.20.30.23/32 --output-dir runtime/remote-access
```

工具按顺序逐节点运行，追加到 `runtime/remote-access/gateway-policies.json`；原始 token 分别保存在同目录的 `win-exec-01.token` 等文件中，不打印到终端。相同节点再次生成会拒绝覆盖。

默认只允许 `builtin-browser`，并发上限 1。需要其他 MCP 时增加 `--mcp-id MCP_ID`；策略和执行节点声明必须都包含该 ID。节点注册不能修改服务器侧白名单、并发上限或 MCP 授权。

通过公司的受控渠道将每个 token 文件单独交付给对应服务器，不把三个文件一起发给每台机器。将文件保存在 `runtime/secrets/` 并限制 Windows ACL，只有运行账号和维护者可读。网关策略只需摘要，不需要 token 原文。修改/撤销策略后重启父平台使其生效。

## 4. 启动父平台

使用公司证书链与私钥，私钥保留在父平台本机 runtime 下：

```powershell
powershell -NoProfile -File scripts/start_remote_platform.ps1 `
  -Policies runtime/remote-access/gateway-policies.json `
  -GatewayHost 0.0.0.0 -GatewayPort 8010 `
  -Certificate runtime/certs/gateway-chain.pem `
  -PrivateKey runtime/certs/gateway.key -NoAutostart
```

此示例不自动启动父平台本地执行节点，方便首轮验证远程节点。未设置运行目录覆盖时，日常数据库默认沿用 `runtime/platform.db`，脚本不删除数据库；可用 `-DatabasePath` 显式指定路径。

隔离试验需要同时隔离运行目录、数据库和端口，不能仅切换数据库。以下使用独立的回环测试策略（来源白名单设为 `127.0.0.1/32`），不覆盖日常数据：

```powershell
$env:AIUI_RUNTIME_DIR = Join-Path (Get-Location) 'runtime/remote-acceptance'
powershell -NoProfile -File scripts/start_remote_platform.ps1 `
  -Policies runtime/lab-access/gateway-policies.json `
  -GatewayHost 127.0.0.1 -GatewayPort 18010 -AdminPort 18000 `
  -DatabasePath (Join-Path $env:AIUI_RUNTIME_DIR 'platform.db') -NoAutostart
```

该覆盖包含审计、默认数据库、日志、Profile、截图和 LLM 配置路径；只对设置它的进程及子进程生效。完成试验后，在同一 PowerShell 会话中用 `Remove-Item Env:AIUI_RUNTIME_DIR` 清除覆盖，再启动日常服务。

仅开放网关端口给执行服务器来源 IP；8000 管理端口保持本机监听。网关同时检查 token 与来源 IP/CIDR，不使用客户端提供的 X-Forwarded-For。执行服务器应使用证书中的主机名访问网关。

没有证书时，仅本机模拟可以使用回环 HTTP：

```powershell
powershell -NoProfile -File scripts/start_remote_platform.ps1 `
  -Policies runtime/remote-access/gateway-policies.json `
  -GatewayHost 127.0.0.1 -GatewayPort 8010 -NoAutostart
```

`-AllowInsecureHttp` 是显式实验选项，用于已隔离且获准的临时内网试验；它会明文传输 token、任务和可能的登录态，不作为公司正式接入默认方案。执行端需要相同实验开关，不能用它代替证书/网络排障。没有关闭 TLS 证书校验的配置项。

回环 HTTP 模拟时，将节点 `platform_url` 改为相应的 `http://127.0.0.1:端口` 并删除 `ca_file`；此时无需设置不安全 HTTP 开关。非回环 HTTP 实验则还需显式设置 `allow_insecure_http: true`。

## 5. 配置并启动执行服务器

复制代码到各服务器并安装各自依赖；不要打包个人电脑的 runtime、LLM 密钥、数据库、token 或浏览器 Profile。凭证按第 3 节单独交付。

复制 `configs/remote-node.example.json` 到本机 `runtime/remote-node.json`，以第一台为例：

```json
{
  "platform_url": "https://AIUI_PARENT.company.example:8010",
  "node_id": "win-exec-01",
  "machine_id": "win-server-01",
  "name": "Windows 执行服务器 01",
  "slots": 1,
  "mcp_ids": ["builtin-browser"],
  "state_dir": "../runtime/remote-nodes/win-exec-01",
  "token_file": "../runtime/secrets/win-exec-01.token",
  "ca_file": "../runtime/certs/company-ca.pem",
  "allow_insecure_http": false
}
```

第二、三台依次修改 ID、显示名、token 和状态目录。相对路径按配置文件所在目录解析；不要共用状态目录。使用系统已经信任的证书时可以不填 `ca_file`。如采用环境变量，将 `token_file` 替换为 `token_env: "AIUI_NODE_TOKEN"`，由运行账号安全注入变量，避免将 token 放进命令行参数。

先自检，再正式启动：

```powershell
powershell -NoProfile -File scripts/start_remote_node.ps1 `
  -Config runtime/remote-node.json -Check

powershell -NoProfile -File scripts/start_remote_node.ps1 `
  -Config runtime/remote-node.json
```

首轮在可观察的会话中跑通，再配置公司已有的进程托管或 Windows 任务计划。计划任务的账号、工作目录和环境变量必须与自检时一致；浏览器默认使用无头模式。先不增加远程进程安装/升级系统。

AI 用例使用执行机自身的 `runtime/llm.json`；父平台个人电脑上的配置不会自动同步。配置好后再运行 AI 用例，先检查模型 API 网络出口和额度。单任务预算从父平台快照下发，共享 API key 的 RPM/TPM 和总费用预算尚未统一管理。

### 官方浏览器 MCP

首轮先验证内置 MCP。使用官方 MCP 时，在父平台添加一个独立配置，例如 ID `remote-playwright`，命令 `npx`，参数：

```json
["-y", "@playwright/mcp@0.0.85", "--headless", "--isolated", "--browser", "chrome"]
```

此版本是本次个人电脑已验证的版本参考；现场还需验证 npm 可达性、Node 版本、Chrome 安装和实际工具列表。将该 ID 同时加入父平台节点策略与各执行机的 `mcp_ids`。`--browser chrome` 需要执行机已安装 Chrome；不要把 Python Playwright 的下载浏览器默认视为官方 npm MCP 的匹配浏览器。

持久浏览器目录仍可能在同一台服务器内冲突；初期使用 `--isolated` 和 slots=1。需要登录态时使用平台环境 Profile。绑定 Profile 的用例仍要求相同 MCP，且必须使用同一目标网站域名/来源。

## 6. 从父平台验收

1. 打开父平台的“执行节点管理”，确认三个远程节点的 machine/node ID、连接方式、MCP 能力、容量及心跳。
2. 使用脚本用例，按节点分别运行一次；选择目标远程节点，确认结果归属正确。自动调度仍可能分给已有本地节点，因此首轮验收使用明确目标。
3. 验证截图可从父平台运行详情打开，失败步骤与错误原因可查看。
4. 采集一次 Profile，再用新浏览器运行依赖 Profile 的用例；确认执行机有正确浏览器/MCP。
5. 最后验证 AI 用例、独立判定和预算记录。先用 Mock/最小用例，避免未知条件下重复消耗付费模型。

特别注意：`http://127.0.0.1:8081/login` 指向当前执行服务器，不是父平台。可在每台机器启动示例应用，或将用例 URL 改为执行服务器可访问的父平台测试应用/公司测试站地址；跨来源变更后需要重新采集相关 Profile。

本机模拟命令和结果记录见交付验证部分。模拟使用独立端口/数据库，不调用 `demo_platform.py` 删除日常用例。

## 7. 断线和重启时的行为

- 网关断开时，执行端保留未确认事件，恢复后补传；父平台显示待回传，不立即把已领取任务转交另一台机器。
- 停止父平台不会自动终止远程执行进程；远程节点会等待连接恢复。停止节点使用对应服务器的 Ctrl+C 或公司进程托管工具，保留状态目录。
- 父平台重启后，已领取远程任务保留原 dispatch_id，允许原批次补传；尚未领取的队列和本地执行任务暂沿用现有重启中断语义。
- 领取响应丢失时只重发相同批次，执行机按本地状态去重；结果 ACK 丢失时重传不会重复计费或重复写日志。
- 同目录重启保留 instance_id。旧 running 任务报告“执行状态未知”，不会自动重新操作被测系统；新状态目录产生新实例，不能领取旧实例的未完成任务。
- 不要删除状态目录来处理断线。它包含去重记录和未确认日志/结果，还可能包含待传的登录态；按私密运行数据管理。迁移状态目录必须停旧进程后进行。
- 网关对单次回传有 64 MiB 上限；超限需现场处理保留的本地记录，不能将大图片堆积视为已成功汇总。图片分片/对象存储后续实现。

## 8. 故障定位与现场记录

| 现象 | 优先检查 |
| --- | --- |
| 无法连接网关 | 父平台地址/端口、防火墙、DNS、代理、网关是否启用 |
| TLS 证书错误 | 主机名与 SAN、完整证书链、CA 文件/运行账号证书信任；保持校验开启 |
| 认证或白名单失败 | node_id/token 是否配套、实际出站 IP 是否匹配 CIDR、策略是否已重载 |
| 节点在线但不领任务 | 指定目标、MCP ID、能力交集、剩余容量、旧批次是否仍待回传 |
| 同 ID 在线实例冲突 | 是否另起了节点进程，是否错误共用 ID/凭证；不要清空状态目录强行绕过 |
| 浏览器启动失败 | 实际账号安装位置、路径/权限、浏览器版本、无头模式、Python 原生依赖 ABI |
| 工具不存在 | 用例 steps 与 MCP 是否匹配，执行服务器实际探测工具是否符合要求 |
| 结果待回传 | 执行端待上传事件、最近错误、原实例是否仍在运行、是否出现超限事件 |

现场记录至少包含：代码提交号/分支、服务器 OS/Python/Node/MCP/浏览器版本、网关配置与节点 ID（脱敏）、三个节点结果链接、一次断线恢复记录。不得把 token、LLM API key、Profile 原文写进工单或聊天。

## 9. 首版能力边界

本期只接收父平台分配的任务结果；外部自主运行的测试导入后续定义。尚未提供全局共享预算、租户权限、自动跨机故障迁移、共享 MCP 资源池、远程进程安装/升级或高可用数据库。失联状态不等于业务操作失败，人工重跑前需要确认旧执行是否仍在继续。

## 10. 本机交付验证

本次在个人电脑完成以下验证，尚未连接公司服务器：

| 验证 | 结果 |
| --- | --- |
| 完整自动测试 | 130 项通过，含现有本地执行功能及远程协议、持久化、路由、界面、Windows 启动参数 |
| 三个独立执行进程 | 8 个场景均符合预期；包括指定节点执行、跨节点 Profile 采集/复用、无 Profile 失败对照、Mock AI 预算、父平台断线后结果补传 |
| 截图 | 32 张图片完成存储及读取验证，运行详情接口可访问 |
| HTTPS | 受信任 CA 成功、未受信任证书被拒绝；半开 TCP/TLS 连接不会阻塞其他节点的注册、心跳和结果回传，停机不被其拖住 |
| Windows 启动脚本 | 实际启动父平台和节点、自检通过；验证含空格路径及 PowerShell 参数绑定 |
| 页面交互 | 单条、选中批次、全部运行、Profile 采集及重新采集均保留指定节点 |

本机三进程模拟使用隔离目录和本地 Mock LLM，未调用付费模型。8 个场景包括两个预期失败的对照/断线场景，“符合预期”不代表全部业务用例成功。

重复验证可使用：

```powershell
New-Item -ItemType Directory -Force runtime/test-temp | Out-Null
$env:TEMP = Join-Path (Get-Location) 'runtime/test-temp'
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -B scripts/smoke_remote_nodes.py
```

模拟报告写入 `runtime/remote-smoke-*/summary.json`；不进入 Git 或源码包。源码包中的 `BUILD_INFO.json` 记录分支、基线提交、验证计数和源文件 SHA-256；这是未提交的预研快照，现场应同时记录快照校验值。解压到独立目录后按第 2–5 节安装依赖、创建本机凭证并启动，不复制个人电脑的运行数据库、登录态或虚拟环境。

## 11. 节点能力上报与展示

注册和心跳支持可选的**展示型**能力字段；它们不参与 token、CIDR、slots、MCP 白名单等调度授权校验。

- **注册**（`POST /v1/register`）可带：
  - `runtime`：Agent 版本、协议版本、OS 版本/架构、Python、Node、npm、Playwright、Chrome 路径/检测、运行账号、计划任务、启动时间、LLM provider/model（不含 key）、CPU/内存/磁盘信息
  - `slots_recommended`：执行机根据硬件给出的建议容量，仅作参考
- **注册响应**会返回该节点有效 `mcp_ids` 对应的 MCP 启动配置；执行机据此在后台调用 `list_tools` 探测，不阻塞任务循环
- **心跳**（`POST /v1/heartbeat`）可带 `mcp_capabilities`：每个 MCP 的可用性、版本（能从启动参数解析时）、工具数量、工具名、Profile 支持、探测错误和时间
- 平台对可选字段做白名单、长度截断和有效 MCP 过滤；旧节点不传时显示“未上报”

### Slots 语义

| 值 | 来源 | 含义 |
| --- | --- | --- |
| `slots_advertised` | 执行机配置 | 本机愿意/能够同时跑几个任务 |
| `slots_granted` | 平台策略 `max_slots` | 平台允许的上限 |
| `slots_effective` | 平台计算 | `min(advertised, granted)`，实际下发给 Worker |
| `slots_recommended` | 执行机硬件提示 | 仅供运维参考，不参与调度 |

平台策略是权威值；执行机可以主动降低本机容量，但不能突破平台授权。节点注册响应中的 `slots` 保持为实际生效值，旧的 `slots` 整数协议不变。

### 查看方式

- 页面：执行节点管理 → 远程执行节点 → 点击节点名称，进入 `/agents/remote/{node_id}`
- API：`GET /api/remote/nodes`、`GET /api/remote/nodes/{node_id}`

能力探测结果只用于展示和排障，不能替代平台策略。执行机不得上报 token、API key、Profile 原文或完整环境变量；平台详情页也不展示这些内容。注册响应中的 MCP 启动配置可能包含 `env`/`headers`，执行机只能用于本地探测，不得写入日志或展示页面；平台详情页只展示探测摘要。
