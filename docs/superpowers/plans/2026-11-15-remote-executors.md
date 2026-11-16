# Windows Remote Executors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Connect 2–3 independent Windows execution servers to the parent platform using authenticated outbound task polling and durable result reporting.

**Architecture:** Keep the management HTTP listener and existing local push workers. Add an opt-in execution-only gateway backed by durable remote assignments in the platform SQLite DB; remote workers reuse Agent._execute with a local SQLite inbox/outbox. Default to one browser task per node and preserve the existing per-task AI budget.

**Tech Stack:** Python standard library HTTP/SSL/SQLite/threading, existing MCP/Playwright, Windows PowerShell.

**Spec:** `docs/superpowers/specs/2026-11-15-remote-executors-design.md` (approved by the user on 2026-11-15).

## Global Constraints

- Work on `codex/research-remote-executors`; no commits to master, no push/merge without user instruction.
- Gateway protocol version 1, maximum JSON request body 64 MiB, per-node Bearer credential + socket peer IP/CIDR allowlist.
- Keep management listener default 127.0.0.1; gateway disabled unless configured. Non-loopback HTTP requires explicit experimental opt-in; HTTPS clients always validate certificates.
- Random instance_id persisted per state directory; process lock prevents two workers using one directory. Never replay an old assignment to another instance.
- Remote disconnect/restart must not automatically rerun possible business actions. Preserve pending results and original dispatch_id across parent restart.
- Raw Profile state and credentials never enter public APIs/logs/Git. Tests use isolated DBs/ports and mock LLMs; no company service access.

## Review Focus

1. A lost claim or result ACK must not cause duplicate execution, billing, or logs.
2. New node instances, revoked credentials, wrong CIDRs, and another node's dispatch must not take over a task.
3. Parent restart must accept original results; worker restart must not replay running work.
4. Local push workers must not steal a specifically targeted remote task or pull-node capacity.
5. Python/Node/browser paths and TLS trust are machine-local; Windows startup examples must not copy personal credentials or persistent browser directories.

## Protocol Contract

Requests use `Authorization: Bearer ...` and `X-AIUI-Node-ID`. Every body contains `version: 1`, `node_id`, `instance_id`.

- POST `/v1/register`: additionally machine_id, name, os, slots, mcp_ids, features; optional dry_run. Response `{ok, node_id, slots, mcp_ids, heartbeat_interval_s:3}`.
- POST `/v1/heartbeat`: active_dispatch_ids. Response `{ok:true}`; IDs acknowledge delivered work, but do not set authoritative running capacity.
- POST `/v1/claim`: active_dispatch_ids. Response `{ok:true, task:null|payload}`. Payload includes task_id, dispatch_id, instance_id, case, mcp, budget, profile, profile_capture.
- POST `/v1/events`: event_id, type (`logs`/`result`), task_id, dispatch_id, payload. Response `{ok:true,status:"received"|"duplicate"}` or explicit rejected error. Result payload follows the existing runner result shape; logs payload is `{logs:[{ts,level,source,message}]}`.
- Policy file: `{version:1,nodes:{node_id:{name,machine_id,token_sha256,allowed_ips:[CIDR],mcp_ids:[id],max_slots:1}}}`. Gateway enforces metadata and identity; registration cannot increase these grants.

## Task 1: Durable platform coordinator and execution gateway

**Files:** Create `aiui/platform/remote.py`, `aiui/common/node_protocol.py`; modify `aiui/platform/store.py`, `aiui/platform/server.py`; create `tests/test_remote_platform.py`.

**Interfaces:** `RemoteCoordinator(platform, policies)` exposes register(node_id,body), heartbeat(node_id,body), claim(node_id,body), event(node_id,body), recover(), mark_offline(node_id), list_nodes(). `make_gateway(coordinator,host,port,cert_file=None,key_file=None,allow_insecure_http=False)` returns a ThreadingHTTPServer for the caller to run/close. Policies are validated by `load_policies(path_or_dict)`. Remote coordinator owns remote_nodes/remote_assignments/remote_events records in the same Store connection and lock.

- [x] Write failing tests: policy binding/CIDR/token/version/body limits; target and MCP/features/slots matching; no queue-head blocking; assignment replay only for original instance; old instance results accepted only for its dispatch; logs/result duplicates; restart restoration with frozen budget/Profile version.
- [x] Run targeted tests and confirm expected failures before implementing.
- [x] Implement durable assignment and result reconciliation. Integrate Platform.remote callbacks; `_pick_agent` excludes pull nodes, honors target_node_id; remote result path must be explicitly verified. Preserve unresolved remote runs in startup recovery; recreate them as awaiting_result.
- [x] Add opt-in CLI gateway flags to server; management listener remains separate; cleanly stop both listeners.
- [x] Run targeted tests and existing 71 tests; inspect behavior for side effects on local tasks.

## Task 2: Remote worker inbox/outbox and transport

**Files:** Create `aiui/agent/remote.py`, `aiui/agent/node_state.py`; create `tests/test_remote_worker.py`.

**Interfaces:** `GatewayClient(base_url,node_id,token,ca_file=None,allow_insecure_http=False).request(path,body)` implements the contract above; rejects redirect/userinfo/untrusted TLS. `NodeState(path)` persists stable instance_id, unique dispatches, running markers and ordered events; exposes local status. `RemoteWorker(config)` owns client/state and existing Agent; exposes step()/run()/close(), enabling real loopback tests. Worker config contains platform_url, node_id, machine_id, name, slots, mcp_ids, state_dir, token_env or token_file, optional ca_file/allow_insecure_http/chromium. Relative paths resolve against config file directory. CLI `python -m aiui.agent.remote --config FILE [--check]`.

- [x] Write failing tests: same dispatch executes once; queued/running/results survive restart; interrupted work becomes non-retryable unknown result; lost ACK leaves events; rejected/oversized events stay pending with diagnostics; identity directory lock; registration and claim metadata follow contract.
- [x] Run targeted tests and confirm failures, then implement workers without duplicating browser execution logic.
- [x] Persist accepted job before submission and running before execution. Reuse `Agent._execute`; durable reporter queues logs, then final result. Events are removed only on received/duplicate confirmation.
- [x] Keep network polling independent from browser threads; reconnect with bounded backoff and graceful shutdown. Dry-run check reports safe environment facts and validates server policy without creating an active node.
- [x] Run worker and platform tests together, including real loopback HTTP transport and parent reconstruction.

## Task 3: UI, Windows deployment and integration verification

**Files:** Modify `aiui/platform/ui.py`, `README.md`, `CLAUDE.md`; create `scripts/create_node_credentials.py`, `scripts/check_remote_node.py` if needed, `scripts/smoke_remote_nodes.py`, `configs/remote-node.example.json`, Windows PowerShell startup examples and `docs/remote-executors.md`; tests for scripts/CLI/UI routing as appropriate.

- [x] Add optional target_node_id to run/capture JSON APIs and HTML run forms; default automatic behavior remains. Test that targeting survives request parsing and does not go to another node.
- [x] Show remote nodes separately with machine/node, status, MCP, capacity, last heartbeat. Add awaiting_result state and remote run provenance; no local process start/stop controls for remote nodes.
- [x] Credential generator produces random node tokens into ignored local files and only hashes into server policy; avoid command-line tokens. Validate and refuse accidental overwrites.
- [x] Windows instructions: Python 3.12 recommendation, matching installed wheels, Playwright browser installation under execution account, Node for official MCP, per-node state directory/token, CA file, gateway firewall/CIDR, independent LLM config, headless and isolated browser defaults.
- [x] Run the full suite. Run three subprocess workers against a temporary parent and local test page; verify per-node attribution, screenshots and Profile/budget compatibility; exercise parent restart and lost ACK through deterministic integration tests.
- [x] Read the final diff and request review; address important findings and rerun affected checks. Report local evidence and on-site limitations, branch and uncommitted state. Do not push/merge, and do not commit implementation without a user instruction.

## Completion evidence

Local research implementation completed on `codex/research-remote-executors`, based on `c653df0`; all source changes remain uncommitted. Full suite: 130 tests passed in 23.225s. Three independent local worker processes matched all 8 expected browser scenarios and verified 32 screenshots. Actual Windows launcher, UI target-selection and TLS checks passed. Whole-branch review and scoped TLS fix review approved, with no remaining findings. Corporate server/network verification remains the on-site acceptance step in `docs/remote-executors.md`; shared budgets and remote lifecycle management remain deferred.
