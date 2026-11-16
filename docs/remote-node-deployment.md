# 远程执行节点部署与更新

本文描述 Windows 执行机的推荐部署方式：**代码目录与运行数据分离**，通过 GitHub
release/tag 更新代码，用 Windows 计划任务托管节点进程。

## 目录布局

```
D:\AIUI\
  deploy\                 部署脚本（不随更新替换）
    install_remote_node.ps1
    update_remote_node.ps1
    migrate_remote_layout.ps1
    remote_selfcheck.ps1
  app\                    执行机代码，更新时整体切换
  runtime\                remote-node.json、secrets、remote-nodes、logs
  browsers\               Playwright Chromium（计划任务账号可读）
```

`runtime\` 是持久数据目录，更新脚本不会修改它。通过 `AIUI_RUNTIME_DIR` 环境变量
指向该目录；计划任务实际调用 `runtime\start-node.ps1`，由它设置环境变量后启动
`app\scripts\start_remote_node.ps1`。

## Python 版本

部署脚本默认使用 `py -3` 选择本机已安装的最新 Python 3，要求 **>=3.10**；
3.12、3.13、3.15 都可以。需要固定版本时加 `-PythonVersion 3.13`，或直接传
`-PythonPath C:\...\python.exe`。远程只装了 3.13 时不需要再装 3.12。

## 首次部署（新执行机）

1. 平台侧生成凭证（当前仍使用 token 文件）：

```powershell
.\.venv\Scripts\python.exe scripts\create_node_credentials.py `
  --node-id win-exec-02 --machine-id win-server-02 `
  --allowed-ip 10.128.15.39/32 --output-dir runtime/remote-access
```

2. 把 token 文件单独传到执行机，例如 `D:\AIUI\incoming\win-exec-02.token`。

3. 把 `aiui-executor` 仓库 `deploy\` 下的四个脚本放到执行机的 `D:\AIUI\deploy\`，然后运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\AIUI\deploy\install_remote_node.ps1 `
  -PlatformUrl http://10.49.76.123:8010 `
  -NodeId win-exec-02 -MachineId win-server-02 `
  -TokenFile D:\AIUI\incoming\win-exec-02.token `
  -Mode Git `
  -RepoUrl https://github.com/Cchenyw/aiui-executor.git -Ref main `
  -AllowInsecureHttp
```

仓库已拆分，执行机只从 `aiui-executor` 拉取代码；发布 release 后改用
`-Mode Release -RepoUrl https://github.com/Cchenyw/aiui-executor.git -Ref latest`。
不要再使用 monorepo 的 `-SparsePaths` 过渡方式。

脚本会：

- 创建 `app`、`runtime`、`secrets`、`remote-nodes`、`logs`、`browsers`
- 写入 `runtime\remote-node.json` 和 token，并限制 token ACL
- 调用 `update_remote_node.ps1` 拉取代码、创建 venv、安装依赖、安装 Chromium、自检
- 写入 `runtime\start-node.ps1` 启动器
- 注册计划任务 `AIUI Remote Node`（开机启动、失败重启）并启动

需要管理员权限注册计划任务。默认以 `SYSTEM` 运行；如使用专用账号，传 `-TaskUser`，
并确保该账号能读写 `runtime` 和 `browsers`。

## 从现有目录迁移

如果执行机目前还是 `D:\ui_test\AIUI_test_v4` 这种“代码和 runtime 混在一起”的布局：

1. 停止正在运行的节点窗口（或使用 `-ForceStop`）。
2. 把 `aiui-executor` 仓库 `deploy\` 下的脚本放到 `D:\AIUI\deploy\`。
3. 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\AIUI\deploy\migrate_remote_layout.ps1 `
  -SourceDir D:\ui_test\AIUI_test_v4 -InstallDir D:\AIUI -RegisterTask
```

迁移会复制代码到 `app`、复制 `runtime` 到 `D:\AIUI\runtime`，重新创建 venv 并安装
依赖，写入启动器并注册/启动计划任务。源目录保留作为备份。

如果上一次迁移在创建 venv 阶段失败，目标目录可能已经存在；重新运行时加 `-Force`
即可继续（脚本会删除旧的 `.venv` 并重建），例如：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\AIUI\deploy\migrate_remote_layout.ps1 `
  -SourceDir D:\ui_test\AIUI_test_v4 -InstallDir D:\AIUI -RegisterTask -Force
```

## 更新执行机

```powershell
# 当前：从 aiui-executor 的 main 拉取
powershell -NoProfile -ExecutionPolicy Bypass -File D:\AIUI\deploy\update_remote_node.ps1 `
  -InstallDir D:\AIUI -Mode Git `
  -RepoUrl https://github.com/Cchenyw/aiui-executor.git -Ref main

# 发布 release 后：固定 release tag
powershell -NoProfile -ExecutionPolicy Bypass -File D:\AIUI\deploy\update_remote_node.ps1 `
  -InstallDir D:\AIUI -Mode Release `
  -RepoUrl https://github.com/Cchenyw/aiui-executor.git -Ref latest
```

更新流程：拉取到 `app.staging-*` → 校验（有 `SHA256SUMS` 时）→ 安装依赖 →
停止计划任务 → staging 自检（配置/凭证/网络）→ 切换 `app` 目录 → 启动任务；
任一步失败会保留旧版本并重新启动。

常用参数：

- `-DryRun`：只拉取和自检，不停止/切换服务（staging 目录保留供检查）。
- `-SkipDeps`：跳过依赖安装，必须同时 `-SkipSelfCheck`。
- `-Wheelhouse <目录>`：离线安装依赖。
- `-BrowsersPath <目录>`：Chromium 安装/查找目录，默认 `D:\AIUI\browsers`。
- `-SkipBrowserInstall`：跳过 `playwright install chromium`（已有浏览器或离线环境）。
- `-Chromium <exe>`：使用系统 Chrome/Edge，写入 `remote-node.json` 并跳过 Playwright 下载。
- `-KeepBackups N`：保留最近 N 个 `app.prev-*` 版本。
- `-GitHubTokenEnv <变量名>`：私有仓库只读令牌的环境变量名，默认 `AIUI_GITHUB_TOKEN`。

## Chromium 安装失败

`playwright install chromium` 默认从 Playwright CDN 下载浏览器，常见原因：

- 执行机无法访问 `cdn.playwright.dev` / `playwright.download.prss.microsoft.com`（最常见）
- 公司代理或 TLS 拦截：需要 `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY`；自签 CA 时可能还需要
  `NODE_EXTRA_CA_CERTS` 指向公司 CA
- 磁盘空间不足，或 `D:\AIUI\browsers` 权限不足
- 杀毒软件阻止解压

先手动复现并保留完整错误：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\AIUI\browsers'
& 'D:\AIUI\app\.venv\Scripts\python.exe' -m playwright install chromium
```

无法下载时的两种方案：

1. **使用系统 Chrome/Edge**（推荐，前提是执行机已安装）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\AIUI\deploy\migrate_remote_layout.ps1 `
  -SourceDir D:\ui_test\AIUI_test_v4 -InstallDir D:\AIUI -RegisterTask -Force `
  -Chromium "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

脚本会把 `chromium` 写入 `remote-node.json`，并自动跳过 Playwright 下载。

2. **离线浏览器目录或内部镜像**：把同版本 Playwright 的
   `ms-playwright\chromium-*` 目录复制到 `-BrowsersPath`，再加 `-SkipBrowserInstall`；
   或设置 `PLAYWRIGHT_DOWNLOAD_HOST` 指向公司内部镜像。

## 自检

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\AIUI\deploy\remote_selfcheck.ps1 `
  -ProjectRoot D:\AIUI\app -ConfigPath D:\AIUI\runtime\remote-node.json
```

自检结果写入脚本所在目录的 `selfcheck-output\`（部署布局下为
`D:\AIUI\deploy\selfcheck-output\`）或指定的 `-OutputDir`。

## 仓库拆分结果

正式开发仓库：

- `aiui-common`：https://github.com/Cchenyw/aiui-common
- `aiui-platform`：https://github.com/Cchenyw/aiui-platform
- `aiui-executor`：https://github.com/Cchenyw/aiui-executor

| 仓库 | 内容 |
| --- | --- |
| `aiui-common` | `aiui/common`（协议、limits、profiles、budget、screenshots、paths、llm） |
| `aiui-platform` | `aiui/platform`、平台脚本/文档/测试 |
| `aiui-executor` | `aiui/agent`、`aiui/toolservers`、`deploy/` 脚本、执行机测试 |

`aiui-platform` 和 `aiui-executor` 各自自带一份 `aiui/common/` 副本；修改共享模块先改
`aiui-common`，再把三份副本同步一致。原 monorepo（`AIUI_test_v4`）保留为集成验证与
历史归档仓库，不再作为执行机部署来源。

CI 在 executor/platform 打 tag 时，把对应 `aiui-common` tag 的源码一起打入 release
zip，并生成 `SHA256SUMS` 和 `BUILD_INFO.json`；执行机只下载 executor release，
不接触平台代码。`aiui/testapp` 已放在 executor 仓库的 `aiui/testapp` 下用于冒烟测试。

## 注意事项

- 更新前确保旧节点已停止；计划任务模式下脚本会自己停止/启动。
- 同一个 `state_dir` 只能有一个节点进程；不要手动复制或删除状态目录。
- 私有仓库需要只读 deploy key 或 fine-grained PAT；令牌不要写进脚本或命令行。
- 当前 HTTP 网关仅适合内网联调；正式使用前切换到 HTTPS。
- `runtime\start-node.ps1` 是持久启动器，更新时不会被替换；不要删除它。
