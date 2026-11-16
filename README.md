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

> 说明：依赖本地 `aiui.agent` 的演示脚本（demo_platform.py / demo_ai.py /
> run_demo.py / smoke_remote_nodes.py）暂时保留在 monorepo，后续再拆到集成仓库。
