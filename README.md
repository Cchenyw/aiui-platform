# aiui-platform

AI UI 测试平台控制面：调度、用例库、执行节点管理、MCP/LLM/预算配置、运行记录。

## 启动

```powershell
py -3 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m aiui.platform.server --port 8000
```

管理页：`http://127.0.0.1:8000/`

远程执行网关（可选）：

```powershell
powershell -NoProfile -File scripts/start_remote_platform.ps1 `
  -Policies runtime/remote-access/gateway-policies.json `
  -GatewayHost 0.0.0.0 -GatewayPort 8010 -AllowInsecureHttp
```

执行机部署、更新和远程接入说明见 `docs/remote-node-deployment.md` 和
`docs/remote-executors.md`。执行机代码在 `aiui-executor` 仓库。

## 平台与本地执行机分离部署

平台仓不包含 `aiui.agent` / `aiui.toolservers`。如果平台主机还要托管本地执行机，
需要另外部署 `aiui-executor`，并通过 `AIUI_EXECUTOR_ROOT` 指向执行端代码目录：

```powershell
$env:AIUI_RUNTIME_DIR = 'D:\AIUI\platform\runtime'
$env:AIUI_EXECUTOR_ROOT = 'D:\AIUI\executor\app'
powershell -NoProfile -File scripts/start_remote_platform.ps1 `
  -Policies D:\AIUI\platform\runtime\remote-access\gateway-policies.json `
  -GatewayHost 0.0.0.0 -GatewayPort 8010 -AllowInsecureHttp `
  -RuntimeDir D:\AIUI\platform\runtime -ExecutorRoot D:\AIUI\executor\app
```

- `AIUI_EXECUTOR_ROOT` 同时用于 builtin 本地执行机启动和 MCP 探测；平台优先使用该
  目录下的 `.venv`，没有则回退到平台解释器。
- `custom` 执行机命令支持 `{python}`、`{here}`、`{executor}`、`{platform}`、
  `{agent_id}`、`{host}`、`{port}`、`{slots}` 占位符。
- 只接远程执行机、不使用本地托管执行机时，可以不设置 `AIUI_EXECUTOR_ROOT`。

> 说明：依赖本地 `aiui.agent` 的演示脚本（demo_platform.py / demo_ai.py /
> run_demo.py / smoke_remote_nodes.py）暂时保留在 monorepo，后续再拆到集成仓库。

开发协作规范见 [AGENTS.md](AGENTS.md)，平台实现约束见 [CLAUDE.md](CLAUDE.md)。
