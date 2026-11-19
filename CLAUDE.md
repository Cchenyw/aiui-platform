# CLAUDE.md

本文件为在本仓库中工作的 Claude Code 提供指引。

## 这个仓库是什么

`aiui-platform` 是 AI UI 测试平台的**控制面**：调度器、用例库、执行节点管理、
MCP / LLM / 预算配置、运行记录、服务端渲染管理页面，以及可选的远程执行网关。

## 目录

```
aiui/platform/server.py     调度器、HTTP 路由、AgentManager、远程网关入口
aiui/platform/store.py      SQLite：cases / agent_configs / mcps / runs / run_logs /
                            env_profiles / env_profile_versions / settings
aiui/platform/ui.py         服务端渲染的看板、用例、Agent、MCP、预算、运行详情页
aiui/platform/remote.py     远程执行协调器与网关
aiui/common/                从 aiui-common 同步的共享模块副本，不要在这里做规范修改
aiui/testapp/               被测示例应用（:8081，/buggy-login 为故意缺陷页）
scripts/                    平台启动、凭证生成、Mock LLM、截图/文档脚本
docs/                       远程接入、浏览器验证和实现文档
tests/                      标准库 unittest 回归
```

## 常用命令

在仓库根目录使用本机 `.venv`：

```bash
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m playwright install chromium
.venv/Scripts/python.exe -m aiui.platform.server --port 8000 --db runtime/platform.db
.venv/Scripts/python.exe -m aiui.platform.server --port 8000 --db runtime/platform.db \
  --node-policies runtime/remote-access/gateway-policies.json \
  --gateway-host 0.0.0.0 --gateway-port 8010 --allow-insecure-gateway-http
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

管理页是 `http://127.0.0.1:8000/`。远程执行网关与 8000 管理监听器是两个独立端口；
默认不启用。

## 硬性约束

1. **管理面默认只绑定 127.0.0.1，不要“以后再补鉴权”。** 平台可以向下发任务、
   启动本地 Agent 命令，是本地命令执行入口。跨机使用必须先做 token + IP 白名单；
   远程执行只走独立网关，不要复用管理监听器。
2. **结果回传幂等，且只接受 `running` 状态。** 以 `task_id` 为键，并校验 Agent 身份；
   内置 Agent 还要校验每次派发的 `dispatch_id`。旧执行结果和过期派发失败必须忽略。
3. **重试有上限。** 默认 `max_retry=2`；失败结果回队列优先级 1，派发失败优先级 2；
   每次重试递增 `attempt`，超过上限进入 failed，不能无界重试。
4. **心跳超时必须回收在飞任务。** Agent 心跳超过 10 秒视为离线，其 `running` 任务
   重新入队；手动停止 Agent 走同一条 retire 路径。不要留下永远不收敛的幽灵任务。
5. **MCP 配置是按任务快照。** 编辑 MCP 或用例只影响之后的新任务；Agent 内不得再查
   实时配置。
6. **托管 Agent 生命周期**：启动前检查端口；停止会终止进程并回收任务；平台关闭时
   停止所有托管 Agent；平台被硬杀后可能留下 Agent 进程，用 `scripts/stop_all.py` 清理。
7. **远程任务是持久且实例绑定的。** 保留 `node_id + instance_id + dispatch_id`；
   心跳丢失或本地停止不得重新派发 pull 任务，应进入 `awaiting_result`。节点重启只报告
   不确定的 running 工作，不重放业务动作；新状态目录不能接管旧实例的未完成任务。
8. **远程注册不是永久授权。** 每个请求都按当前节点策略校验：machine identity、MCP
   交集、slots、token 和 socket 对端 CIDR。旧注册/结果路由不能绕过网关校验。策略文件
   只在平台启动时加载；唯一的在线变更是管理监听器的 `POST /api/remote/nodes/{id}/slots`
   （只改 `max_slots`，校验 1–128 后原子写回策略文件，下一次心跳/claim 生效），
   token/CIDR/MCP 白名单等仍必须改文件并重启。该路由不得出现在执行网关。
9. **远程投递需要显式确认。** Worker 的日志/结果持久排队，只有收到
   `received`/`duplicate` 才删除；拒绝或超大事件保留诊断。平台去重和运行状态流转必须在
   同一个 SQLite 事务中完成。Profile/截图文件不是数据库事务，失败后可能需要清理孤儿文件。
10. **Profile 原始状态是凭证。** 不得写入 `runs.result_json`、`case_json`、`mcp_json`、
    日志、`/api/trace` 或任何公开 API；`/api/env-profiles` 只返回元数据和摘要。在
    `on_result` 持久化前剥离，只保留 cookie/origin 计数；AI 工具目录排除
    `profile_capture` / `browser_run_code_unsafe`。备份必须包含 `runtime/profiles/`。
11. **AI 预算是平台设置，并快照进任务。** `settings.ai_budget` 控制
    `max_total_tokens` 和 `max_steps_ceiling`；执行器使用任务快照，不能改回硬编码常量。
    价格规则只生成提示性成本估算，不得影响 passed/failed/retry；实际以供应商账单为准。
12. **`runtime/audit.jsonl` 每次平台启动都会截断**，由平台进程追加；持久历史在
    `runs` / `run_logs`。不要把 audit 当作持久数据。
13. **LLM 配置在 `runtime/llm.json`，本机 Agent 共享，绝不能记录或返回未掩码的 key。**
    `/api/llm` 只返回掩码；从环境变量导入必须是显式用户操作。页面文本是不可信输入。
14. **UI 是服务端渲染 + 少量原生 JS，不引入构建链。** 用户可见文本、注释和文档使用中文。

## 测试与验证

- 单测使用标准库 unittest：`python -m unittest discover -s tests -v`。
- 测试必须隔离运行数据：在创建 `Platform`/`Store` 前把 audit、Profile、截图和数据库
  路径改到测试专用目录；子进程集成测试在导入项目前设置 `AIUI_RUNTIME_DIR`。
- 不要用日常 `runtime/` 数据做测试；不要连接公司真实服务、真实 LLM 或真实执行机。
- 远程协议回归重点：结果幂等、重试语义、心跳回收、节点实例绑定、策略/CIDR、断线补传、
  Profile 原始状态不落库。

## 边界与约定

- 当前仍是预研实现：没有持久化队列、没有鉴权、没有多机公司网络实测、没有前端构建。
- 平台不再包含执行机代码；执行机与部署脚本在 `aiui-executor` 仓库，通过网关连接。
- 跨组件端到端演示/冒烟脚本暂时保留在原 monorepo（`scripts/demo_*.py`、
  `scripts/smoke_remote_nodes.py`），后续再拆到集成仓库。
- 修改 `aiui/common/` 前先改 `aiui-common` 仓库，再把三份副本同步一致。
