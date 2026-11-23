# -*- coding: utf-8 -*-
"""测试平台（控制面，端口默认 8000）

在原始调度 demo 上补齐了四块平台能力：
  1. 用例库 CRUD（SQLite 持久化，页面上新建/修改/查询/删除/批量运行）
  2. 本地 Agent 纳管（配置 + 一键启动/停止 + 自动注册/心跳/容量调度）
  3. MCP 注册表（内置浏览器 MCP / 官方 Playwright MCP / 自定义 stdio·sse·http）
  4. 运行记录与执行日志（执行节点批量上报，平台落库，页面增量轮询可视化）

仍然保留的硬约束：
  · 队列在平台侧，调度是"最小负载优先 + 容量上限"；
  · 心跳超时必须回收在飞任务（幽灵任务），失败重试有上限并进死信；
  · 结果回传按 task_id + running 态幂等；
  · 默认只绑 127.0.0.1，不提供鉴权。跨机使用前必须加 token + 白名单。
"""
import argparse
import hashlib
import heapq
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aiui.common import llm as llm_lib
from aiui.common import budget, limits, mcp_runtime, profiles, screenshots
from aiui.common.paths import ROOT, RUNTIME_DIR, LOGS_DIR, ensure_runtime_dirs
from aiui.platform import ui
from aiui.platform.store import Store, DEFAULT_DB

AUDIT = RUNTIME_DIR / "audit.jsonl"
LOG_DIR = LOGS_DIR
HEARTBEAT_TIMEOUT = 10.0
DISPATCH_FAIL_BACKOFF = 0.5
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

STORE = None
P = None
AM = None
PLATFORM_URL = "http://127.0.0.1:8000"
EXECUTOR_ROOT = Path(os.environ.get("AIUI_EXECUTOR_ROOT") or ROOT).expanduser().resolve()


def executor_python():
    """执行端解释器：优先使用执行机自己的 .venv，否则回退到当前解释器。"""
    candidates = (EXECUTOR_ROOT / ".venv" / "Scripts" / "python.exe",
                  EXECUTOR_ROOT / ".venv" / "bin" / "python")
    for path in candidates:
        if path.is_file():
            return str(path)
    return sys.executable


def now():
    return round(time.time(), 3)


# ══════════════════════ 平台状态 / 调度 ══════════════════════
class Platform:
    def __init__(self, store, max_retry=2):
        self.store = store
        self.remote = None
        self.agents = {}                       # agent_id -> 运行时注册信息
        self.tasks = OrderedDict()             # task_id -> 任务状态（内存）
        self.queue = []                        # heapq: (priority, seq, task_id)
        self.seq = 0
        self.lock = threading.RLock()
        self.max_retry = max_retry
        self.stop = threading.Event()
        ensure_runtime_dirs()
        AUDIT.write_text("", encoding="utf-8")

    # ── 审计 / 日志 ────────────────────────────────────────────────
    def audit(self, task_id, event, **kw):
        rec = {"ts": now(), "task_id": task_id, "event": event, **kw}
        try:
            with open(AUDIT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        t = self.tasks.get(task_id)
        if t is not None:
            t["timeline"].append(rec)

    def log(self, task_id, message, level="info", source="platform"):
        """平台侧事件也写进运行日志，和执行节点日志拼成一条时间线。"""
        try:
            self.store.add_logs(task_id, [{
                "ts": now(), "level": level, "source": source, "message": message,
            }])
        except Exception as e:
            print("[platform] 写日志失败:", e, file=sys.stderr, flush=True)

    def _save(self, t):
        try:
            self.store.save_run({
                "task_id": t["task_id"], "case_id": t.get("case_id", ""),
                "name": t.get("name", ""), "mcp_id": t.get("mcp_id", ""),
                "state": t["state"], "attempt": t["attempt"],
                "max_retry": t["max_retry"], "agent_id": t.get("agent"),
                "created": t["created"], "started": t.get("started"),
                "finished": t.get("finished"), "error": t.get("error", ""),
                "result": t.get("result"), "case": t.get("case"), "mcp": t.get("mcp"),
                "kind": t.get("kind", "test"),
                "profile_id": t.get("profile_id", ""),
                "profile_hash": t.get("profile_hash", ""),
            })
        except Exception as e:
            if self.store._transaction_depth:
                raise
            print("[platform] 保存运行记录失败:", e, file=sys.stderr, flush=True)

    # ── 注册 / 心跳 ────────────────────────────────────────────────
    def register(self, d):
        aid = str(d.get("agent_id") or f"agent-{uuid.uuid4().hex[:6]}")
        with self.lock:
            if self.remote and aid in self.remote.policies["nodes"]:
                raise ValueError("远程节点 ID 不能通过本地接口注册")
            a = self.agents.setdefault(aid, {"done": 0, "failed": 0})
            a.update({"host": d.get("host", "127.0.0.1"), "port": int(d.get("port", 8001)),
                      "slots": max(1, int(d.get("slots", 1))), "tags": d.get("tags", []),
                      "features": d.get("features") or [],
                      "status": "online", "running": a.get("running", 0), "last_hb": now()})
        return {"ok": True, "agent_id": aid}

    def heartbeat(self, d):
        with self.lock:
            a = self.agents.get(d.get("agent_id"))
            if not a:
                return {"ok": False, "error": "未注册"}
            a["last_hb"] = now()
            a["status"] = "online"
            a["running"] = int(d.get("running", a["running"]))
        return {"ok": True}

    def retire_agent(self, aid, reason="执行节点离线"):
        """本地节点回收入队；远程节点只标记离线并等待原批次结果。"""
        with self.lock:
            if self.remote and (aid in self.remote.policies['nodes'] or any(
                    t.get('connection') == 'pull' and t.get('agent') == aid
                    for t in self.tasks.values())):
                self.remote.mark_offline(aid)
                return 0
            a = self.agents.get(aid)
            if a:
                a["status"] = "offline"
                a["running"] = 0
            requeued = 0
            for tid, t in self.tasks.items():
                if t["state"] == "running" and t["agent"] == aid:
                    t["state"] = "queued"
                    t["agent"] = None
                    self._enqueue(tid, 1)
                    requeued += 1
                    self.audit(tid, "requeued_agent_offline", agent=aid)
                    self.log(tid, f"{reason}，任务重新入队", level="warn")
                    self._save(t)
            if a and requeued:
                print(f"[platform] {reason}: {aid}，回收 {requeued} 个任务", file=sys.stderr, flush=True)
            return requeued

    # ── 任务创建 ───────────────────────────────────────────────────
    def _resolve_mcp(self, case):
        mid = (case.get("mcp_id") or "").strip()
        if mid:
            m = self.store.get_mcp(mid)
            if not m:
                raise ValueError(f"用例 {case.get('name','')} 指定的 MCP 不存在: {mid}")
            if not m["enabled"]:
                raise ValueError(f"MCP 已停用: {mid}")
            return m
        m = self.store.default_mcp()
        if not m:
            raise ValueError("没有可用的 MCP，请先在 MCP 管理页添加并启用一个")
        return m

    def _budget_snapshot(self):
        b = self.store.get_budget()
        return {"max_total_tokens": b["max_total_tokens"],
                "max_steps_ceiling": b["max_steps_ceiling"]}

    def _resolve_profile(self, case, mcp):
        pid = str(case.get("profile_id") or "").strip()
        if not pid:
            return None
        profile, state = self.store.get_profile_state(pid)
        source = profile.get("source_mcp_id") or ""
        if source and source != mcp["id"]:
            raise ValueError(
                f"用例「{case.get('name','')}」选择的 Profile 由 {source} 采集，"
                f"当前用例使用 {mcp['id']}；请使用相同 MCP 或重新采集")
        return {"profile_id": pid, "version": profile["version"],
                "hash": profile["current_hash"], "state": state}

    def add_tasks(self, cases, priority=5, max_retry=None, target_node_id=None):
        prepared = []
        for c in cases:
            if not isinstance(c, dict):
                raise ValueError("用例格式必须是对象")
            mcp = self._resolve_mcp(c)
            prepared.append((c, mcp, self._resolve_profile(c, mcp)))

        ids = []
        with self.lock:
            for c, mcp, profile in prepared:
                tid = c.get("id") or c.get("task_id") or f"T{len(self.tasks)+1:03d}-{uuid.uuid4().hex}"
                if tid in self.tasks or self.store.get_run(tid):
                    raise ValueError("任务 ID 已存在，不能覆盖历史或在飞任务")
                try:
                    case_retry = int(c.get("max_retry", -1))
                except Exception:
                    case_retry = -1
                case_snapshot = {
                    "case_id": c.get("case_id", ""), "name": c.get("name", tid),
                    "description": c.get("description", ""), "tags": c.get("tags", ""),
                    "timeout_s": float(c.get("timeout_s", 60)),
                    "steps": c.get("steps") or [],
                    "mode": c.get("mode") or "scripted",
                    "instruction": c.get("instruction") or "",
                    "expected": c.get("expected") or "",
                    "max_steps": int(c.get("max_steps") or limits.AI_DEFAULT_MAX_STEPS),
                    "navigation_hint": c.get("navigation_hint") or "",
                    "max_retry": case_retry,
                    "side_effect": bool(c.get("side_effect")),
                    "kind": c.get("kind") or "test",
                    "profile_id": c.get("profile_id") or "",
                }
                if case_snapshot["side_effect"]:
                    effective_retry = 0
                elif max_retry is not None and int(max_retry) >= 0:
                    effective_retry = int(max_retry)
                elif case_snapshot["max_retry"] >= 0:
                    effective_retry = case_snapshot["max_retry"]
                else:
                    effective_retry = self.max_retry
                t = {
                    "task_id": tid, "name": case_snapshot["name"], "case": case_snapshot,
                    "case_id": case_snapshot["case_id"], "mcp_id": mcp["id"], "mcp": mcp,
                    "state": "queued", "attempt": 0, "agent": None,
                    "created": now(), "started": None, "finished": None,
                    "result": None, "error": "", "timeline": [],
                    "max_retry": effective_retry,
                    "kind": "test", "profile": profile,
                    "target_node_id": target_node_id or c.get("target_node_id") or "",
                    "profile_id": (profile or {}).get("profile_id", ""),
                    "profile_hash": (profile or {}).get("hash", ""),
                    "require_features": ["env_profile"] if profile else [],
                    "budget": (self._budget_snapshot()
                               if case_snapshot["mode"] == "ai" else None),
                }
                self.tasks[tid] = t
                self._enqueue(tid, priority)
                self.audit(tid, "created", priority=priority, mcp=mcp["id"])
                self.log(tid, f"任务已创建：用例「{t['name']}」，MCP={mcp['id']}")
                if profile:
                    self.log(tid, f"使用执行环境 Profile {profile['profile_id']}@"
                                  f"v{profile['version']} (hash={profile['hash'][:12]})")
                self._save(t)
                ids.append(tid)
        return ids

    def add_capture_task(self, profile_id, priority=1, target_node_id=None):
        """创建一个环境采集 run：执行 setup case，成功后由 Agent 回传 storage state。"""
        profile = self.store.get_env_profile(profile_id)
        if not profile:
            raise ValueError("Profile 不存在")
        case = self.store.get_case(profile.get("setup_case_id") or "")
        if not case:
            raise ValueError("Profile 引用的采集用例不存在")
        if case.get("profile_id"):
            raise ValueError("采集用例不能再依赖其他 Profile")
        mcp = self._resolve_mcp(case)
        if not profiles.detect_adapter(mcp):
            raise ValueError(f"MCP {mcp['id']} 暂不支持环境采集")
        ok, err = self.store.begin_profile_capture(profile_id)
        if not ok:
            raise ValueError(err)
        try:
            tid = f"T{len(self.tasks)+1:03d}-{uuid.uuid4().hex}"
            case_snapshot = {
                "case_id": case.get("case_id", ""), "name": case.get("name", tid),
                "description": case.get("description", ""), "tags": case.get("tags", ""),
                "timeout_s": float(case.get("timeout_s", 60)),
                "steps": case.get("steps") or [],
                "mode": case.get("mode") or "scripted",
                "instruction": case.get("instruction") or "",
                "expected": case.get("expected") or "",
                "max_steps": int(case.get("max_steps") or limits.AI_DEFAULT_MAX_STEPS),
                "kind": "setup", "profile_id": "",
            }
            t = {
                "task_id": tid, "name": f"环境采集：{profile['name']}",
                "case": case_snapshot, "case_id": case_snapshot["case_id"],
                "mcp_id": mcp["id"], "mcp": mcp,
                "state": "queued", "attempt": 0, "agent": None,
                "created": now(), "started": None, "finished": None,
                "result": None, "error": "", "timeline": [],
                "max_retry": 0,
                "kind": "capture", "profile": None,
                "target_node_id": target_node_id or "",
                "profile_id": profile_id, "profile_hash": "",
                "profile_capture": {"profile_id": profile_id},
                "require_features": ["env_profile"],
                "budget": (self._budget_snapshot()
                           if case_snapshot["mode"] == "ai" else None),
            }
            with self.lock:
                self.tasks[tid] = t
                self._enqueue(tid, priority)
                self.audit(tid, "created", priority=priority, mcp=mcp["id"],
                           profile_id=profile_id)
                self.log(tid, f"任务已创建：环境采集「{profile['name']}」，"
                              f"用例「{case['name']}」，MCP={mcp['id']}")
                self._save(t)
            return tid
        except Exception:
            self.store.mark_profile_capture_failed(profile_id, "创建环境采集任务失败")
            raise

    def _enqueue(self, tid, priority):
        self.seq += 1
        heapq.heappush(self.queue, (priority, self.seq, tid))

    def _pick_agent(self, task=None):
        need = set((task or {}).get("require_features") or [])
        best, score = None, 1e9
        for aid, a in self.agents.items():
            if a.get("connection") == "pull":
                continue
            if (task or {}).get("target_node_id") and task["target_node_id"] != aid:
                continue
            if a["status"] != "online":
                continue
            if a["running"] >= a["slots"]:
                continue
            if need and not need.issubset(set(a.get("features") or [])):
                continue
            if a["running"] < score:
                best, score = aid, a["running"]
        return best

    # ── 调度循环 ───────────────────────────────────────────────────
    def dispatch_loop(self):
        while not self.stop.is_set():
            try:
                with self.lock:
                    if self.remote:
                        self.remote.tick()
                    for aid, a in list(self.agents.items()):
                        if a["status"] == "online" and now() - a["last_hb"] > HEARTBEAT_TIMEOUT:
                            self.retire_agent(aid, "心跳超时")

                    while self.queue:
                        selected = None
                        for item in sorted(self.queue):
                            head = self.tasks.get(item[2])
                            if not head or head['state'] not in ('queued', 'retry'):
                                continue
                            aid = self._pick_agent(head)
                            if aid:
                                selected = (item, aid)
                                break
                        if selected is None:
                            break
                        item, aid = selected
                        self.queue.remove(item)
                        heapq.heapify(self.queue)
                        tid = item[2]
                        t = self.tasks.get(tid)
                        if not t or t["state"] not in ("queued", "retry"):
                            continue
                        a = self.agents[aid]
                        a["running"] += 1
                        t.update({"state": "running", "agent": aid,
                                  "started": t["started"] or now(),
                                  "dispatch_id": uuid.uuid4().hex})
                        if t.get("profile_id") and t.get("kind") != "capture":
                            try:
                                self.store.touch_profile_used(t["profile_id"])
                            except Exception:
                                pass
                        self.audit(tid, "dispatched", agent=aid, attempt=t["attempt"])
                        self.log(tid, f"派发到执行节点 {aid}（attempt={t['attempt']}）")
                        self._save(t)
                        threading.Thread(target=self._dispatch, args=(aid, tid, t["dispatch_id"]), daemon=True).start()
            except Exception as e:
                print("[platform] 调度异常:", e, file=sys.stderr, flush=True)
            time.sleep(0.15)

    def _dispatch(self, aid, tid, dispatch_id=None):
        a = self.agents.get(aid)
        if not a:
            return
        url = f"http://{a['host']}:{a['port']}/task"
        t = self.tasks.get(tid)
        if not t or (dispatch_id and t.get("dispatch_id") != dispatch_id):
            return
        payload = {"task_id": tid, "case": t["case"], "mcp": t["mcp"],
                   "dispatch_id": dispatch_id,
                   "profile": t.get("profile"),
                   "profile_capture": t.get("profile_capture"),
                   "budget": t.get("budget")}
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                resp = json.loads(r.read().decode("utf-8") or "{}")
            if not resp.get("accepted"):
                self._back(aid, tid, "capacity_rejected", dispatch_id)
        except Exception as e:
            self._back(aid, tid, f"dispatch_error:{type(e).__name__}", dispatch_id)

    def _back(self, aid, tid, reason, dispatch_id=None):
        with self.lock:
            t = self.tasks.get(tid)
            # 下发请求超时/失败时，任务可能已经被回收或已回传结果，避免把已完成任务又拉回队列
            if not t or t["state"] != "running" or t["agent"] != aid:
                return
            if dispatch_id and t.get("dispatch_id") != dispatch_id:
                return
            if aid in self.agents:
                self.agents[aid]["running"] = max(0, self.agents[aid]["running"] - 1)
            t["agent"] = None
            t["attempt"] += 1
            if t["attempt"] > t["max_retry"] + 1:
                t["state"] = "failed"
                t["error"] = reason
                t["finished"] = now()
                if t.get("kind") == "capture" and t.get("profile_id"):
                    self.store.mark_profile_capture_failed(t["profile_id"], reason)
                self.audit(tid, "failed", reason=reason)
                self.log(tid, f"下发失败且达到上限：{reason}", level="error")
            else:
                t["state"] = "retry"
                self._enqueue(tid, 2)
                self.audit(tid, "dispatch_failed_requeue", reason=reason, attempt=t["attempt"])
                self.log(tid, f"下发失败（{reason}），重新入队 attempt={t['attempt']}", level="warn")
            self._save(t)
        time.sleep(DISPATCH_FAIL_BACKOFF)

    # ── 结果回收（幂等，决策 7）─────────────────────────────────────
    def on_result(self, d, remote_verified=False):
        tid = d.get("task_id")
        with self.lock:
            t = self.tasks.get(tid)
            if not t:
                return {"ok": False, "error": "未知任务"}
            if t.get("connection") == "pull" and not remote_verified:
                return {"ok": False, "error": "远程结果必须通过执行网关验证"}
            if t["state"] in ("done", "failed"):
                return {"ok": True, "ignored": True, "reason": "任务已终态"}
            if t["state"] != "running" and not (remote_verified and t["state"] == "awaiting_result"):
                return {"ok": True, "ignored": True, "reason": "任务不在 running 态"}
            if d.get("dispatch_id") and d["dispatch_id"] != t.get("dispatch_id"):
                return {"ok": True, "ignored": True, "reason": "执行批次已过期"}
            aid = d.get("agent_id") or t["agent"]
            if t.get("agent") and aid and aid != t["agent"]:
                return {"ok": True, "ignored": True, "reason": "执行节点不匹配"}
            if aid in self.agents:
                self.agents[aid]["running"] = max(0, self.agents[aid]["running"] - 1)
            t["agent"] = aid
            profile_state = d.pop("profile_state", None)
            if t.get("kind") == "capture":
                d["retryable"] = False
                if d.get("ok") and not profile_state:
                    d["ok"] = False
                    d["error"] = "环境采集失败：结果缺少 storage state"
                if d.get("ok") and profile_state:
                    try:
                        info = self.store.finish_profile_capture(
                            t["profile_id"], profile_state, tid,
                            source_mcp_id=t.get("mcp_id", ""))
                        t["profile_hash"] = info["hash"]
                        d["profile_summary"] = info["summary"]
                        self.log(tid, f"环境采集成功：version={info['version']}，"
                                      f"cookies={info['summary']['cookie_count']}，"
                                      f"origins={info['summary']['origin_count']}")
                        self.audit(tid, "profile_captured",
                                   profile_id=t["profile_id"],
                                   version=info["version"], hash=info["hash"])
                    except Exception as e:
                        d["ok"] = False
                        d["error"] = f"环境采集保存失败: {mcp_runtime.describe_exc(e)}"
                        d["retryable"] = False
                        self.store.mark_profile_capture_failed(
                            t["profile_id"], d["error"])
                        self.log(tid, d["error"], level="error")
                elif not d.get("ok"):
                    self.store.mark_profile_capture_failed(
                        t["profile_id"], d.get("error") or "采集失败")
            screenshots.persist_steps(tid, d)
            t["result"] = d
            t["error"] = d.get("error", "") or ""
            if t.get("budget") and not d.get("budget"):
                d["budget"] = t["budget"]
            if d.get("mode") == "ai" and d.get("usage"):
                try:
                    settings = self.store.get_budget()
                    cost = budget.estimate_cost(
                        d.get("usage") or {}, d.get("ai_model") or "",
                        settings.get("pricing") or [])
                    if cost:
                        d["cost"] = cost
                        self.log(tid, "AI 成本估算："
                                      f"{budget.format_cost(cost['estimated'], cost['currency'])}"
                                      f"（{cost['rule']}）")
                except Exception:
                    pass
            verdict = d.get("verdict", "") or ""
            retryable = d.get("retryable", True) is not False
            if d.get("ok"):
                t["state"] = "done"
                t["finished"] = now()
                if aid in self.agents:
                    self.agents[aid]["done"] += 1
                self.audit(tid, "done", agent=aid, elapsed_ms=d.get("elapsed_ms"),
                           attempt=t["attempt"], verdict=verdict)
                self.log(tid, f"执行成功，耗时 {d.get('elapsed_ms', 0)} ms"
                              + (f"，判定={verdict}" if verdict else ""))
            else:
                t["attempt"] += 1
                if retryable and t["attempt"] <= t["max_retry"]:
                    t["state"] = "retry"
                    self._enqueue(tid, 1)
                    if aid in self.agents:
                        self.agents[aid]["failed"] += 1
                    self.audit(tid, "retry", agent=aid, attempt=t["attempt"],
                               error=t["error"][:160], verdict=verdict)
                    self.log(tid, f"执行失败，第 {t['attempt']} 次重试入队：{t['error'][:300]}", level="warn")
                else:
                    t["state"] = "failed"
                    t["finished"] = now()
                    if aid in self.agents:
                        self.agents[aid]["failed"] += 1
                    self.audit(tid, "failed", agent=aid, attempt=t["attempt"],
                               error=t["error"][:160], verdict=verdict)
                    why = "不可重试" if not retryable else f"达到重试上限（attempt={t['attempt']}）"
                    self.log(tid, f"执行失败且{why}：{t['error'][:300]}", level="error")
            if t["state"] in ("done", "failed"):
                t.pop("profile", None)
            self._save(t)
        return {"ok": True}

    # ── 汇总 ───────────────────────────────────────────────────────
    def status(self):
        with self.lock:
            st = {"queued": 0, "running": 0, "retry": 0, "done": 0, "failed": 0}
            elapsed = []
            for t in self.tasks.values():
                st[t["state"]] = st.get(t["state"], 0) + 1
                if t["state"] == "done" and t.get("result"):
                    elapsed.append(t["result"].get("elapsed_ms", 0))
            agents = []
            for aid, a in self.agents.items():
                agents.append({"agent_id": aid, "status": a["status"], "running": a["running"],
                               "slots": a["slots"], "done": a["done"], "failed": a["failed"],
                               "idle": a["status"] == "online" and a["running"] < a["slots"],
                               "addr": f"{a['host']}:{a['port']}"})
            return {"counts": st, "queue_len": len(self.queue), "agents": agents,
                    "remote_nodes": self.remote.list_nodes() if self.remote else [],
                    "avg_elapsed_ms": int(sum(elapsed) / len(elapsed)) if elapsed else 0,
                    "total": len(self.tasks), "finished": st["done"] + st["failed"]}

    def task_summaries(self):
        with self.lock:
            return [{
                "task_id": t["task_id"], "name": t["name"], "state": t["state"],
                "machine_id": t.get("machine_id", ""), "node_id": t.get("node_id", ""), "dispatch_id": t.get("dispatch_id", ""),
                "target_node_id": t.get("target_node_id", ""), "connection": t.get("connection", "push"),
                "attempt": t["attempt"], "agent": t["agent"], "error": t["error"][:120],
                "case_id": t.get("case_id", ""), "mcp_id": t.get("mcp_id", ""),
                "mode": (t.get("case") or {}).get("mode", "scripted"),
                "verdict": (t["result"] or {}).get("verdict", "") if t["result"] else "",
                "confidence": (t["result"] or {}).get("confidence") if t["result"] else None,
                "elapsed_ms": (t["result"] or {}).get("elapsed_ms") if t["result"] else None,
                "steps": len((t["result"] or {}).get("steps") or []),
            } for t in self.tasks.values()]

    def delete_run(self, task_id):
        """删除一条已结束的运行记录、日志和截图；在飞任务拒绝删除。"""
        task = self.tasks.get(task_id)
        run = task or self.store.get_run(task_id)
        if not run:
            return False
        if run.get("state") not in ("done", "failed"):
            raise ValueError("运行尚未结束，不能删除")
        with self.lock:
            self.tasks.pop(task_id, None)
            self.queue = [item for item in self.queue if item[2] != task_id]
            deleted = self.store.delete_run(task_id)
        if deleted:
            shots = Path(screenshots.SCREENSHOTS_DIR) / hashlib.sha256(
                str(task_id).encode("utf-8")).hexdigest()
            shutil.rmtree(shots, ignore_errors=True)
            print(f"[platform] 已删除运行记录: {task_id}", file=sys.stderr, flush=True)
        return deleted


# ══════════════════════ 本地 Agent 进程管理 ══════════════════════
class AgentManager:
    def __init__(self, store, platform, log_dir, platform_url):
        self.store = store
        self.platform = platform
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.platform_url = platform_url
        self.lock = threading.RLock()
        self.procs = {}                    # agent_id -> {proc, fh, log_path, started, exit_code}

    def _subst(self, text, cfg):
        mapping = {
            "{python}": sys.executable, "{here}": str(ROOT),
            "{executor}": str(EXECUTOR_ROOT),
            "{platform}": self.platform_url, "{agent_id}": cfg["id"],
            "{host}": cfg["host"], "{port}": str(cfg["port"]), "{slots}": str(cfg["slots"]),
        }
        for k, v in mapping.items():
            text = text.replace(k, v)
        return text

    def _cmd(self, cfg):
        if cfg["kind"] == "builtin":
            return [executor_python(), "-m", "aiui.agent.runner",
                    "--platform", self.platform_url, "--agent-id", cfg["id"],
                    "--host", cfg["host"], "--port", str(cfg["port"]),
                    "--slots", str(cfg["slots"])]
        cmd = [self._subst(str(x), cfg) for x in (cfg.get("command") or [])]
        if not cmd:
            raise ValueError("custom 类型必须配置启动命令（JSON 数组）")
        first = cmd[0]
        if first.lower().endswith(".py"):
            cmd = [sys.executable] + cmd
        elif os.name == "nt" and first.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd.exe", "/c"] + cmd
        return cmd

    def _env(self, cfg):
        env = os.environ.copy()
        for k, v in (cfg.get("env") or {}).items():
            env[str(k)] = self._subst(str(v), cfg)
        env.update({
            "PLATFORM_URL": self.platform_url, "AGENT_ID": cfg["id"],
            "AGENT_HOST": cfg["host"], "AGENT_PORT": str(cfg["port"]),
            "AGENT_SLOTS": str(cfg["slots"]),
        })
        return env

    def _cwd(self, cfg):
        cwd = (cfg.get("cwd") or "").strip()
        if not cwd:
            return str(EXECUTOR_ROOT if cfg["kind"] == "builtin" else ROOT)
        p = Path(cwd)
        return str(p if p.is_absolute() else ROOT / p)

    def _refresh_locked(self):
        for aid in list(self.procs):
            rec = self.procs[aid]
            p = rec.get("proc")
            if p is not None and p.poll() is not None:
                rec["running"] = False
                rec["exit_code"] = p.returncode
                fh = rec.get("fh")
                if fh:
                    try:
                        fh.close()
                    except Exception:
                        pass
                    rec["fh"] = None

    def snapshot(self):
        with self.lock:
            self._refresh_locked()
            return {
                aid: {"running": bool(rec.get("running")), "pid": (rec.get("proc").pid if rec.get("proc") else None),
                      "exit_code": rec.get("exit_code"), "log_path": rec.get("log_path"),
                      "started": rec.get("started")}
                for aid, rec in self.procs.items()
            }

    def _port_busy(self, host, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(0.4)
            return s.connect_ex((host, int(port))) == 0
        finally:
            s.close()

    def start(self, aid):
        with self.lock:
            self._refresh_locked()
            cfg = self.store.get_agent_config(aid)
            if not cfg:
                return False, f"执行节点配置不存在: {aid}"
            if not cfg["enabled"]:
                return False, "执行节点已停用"
            rec = self.procs.get(aid)
            if rec and rec.get("running"):
                return False, "进程已在运行"
            if self._port_busy(cfg["host"], cfg["port"]):
                return False, f"端口已被占用: {cfg['host']}:{cfg['port']}"
            try:
                cmd = self._cmd(cfg)
            except Exception as e:
                return False, mcp_runtime.describe_exc(e)
            log_path = self.log_dir / f"agent-{aid}.log"
            fh = open(log_path, "ab", buffering=0)
            header = (f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start {aid} =====\n"
                      f"$ {' '.join(cmd)}\n").encode("utf-8")
            fh.write(header)
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                proc = subprocess.Popen(cmd, cwd=self._cwd(cfg), env=self._env(cfg),
                                        stdout=fh, stderr=subprocess.STDOUT,
                                        creationflags=flags)
            except Exception as e:
                fh.close()
                return False, f"启动失败: {mcp_runtime.describe_exc(e)}"
            self.procs[aid] = {"proc": proc, "fh": fh, "log_path": str(log_path),
                               "started": now(), "running": True, "exit_code": None}
        print(f"[platform] 启动本地执行节点 {aid} pid={proc.pid} -> {log_path}", file=sys.stderr, flush=True)
        return True, f"已启动 {aid}（pid={proc.pid}）"

    def stop(self, aid, retire=True):
        with self.lock:
            rec = self.procs.get(aid)
            proc = rec.get("proc") if rec else None
            was_running = bool(proc and proc.poll() is None)
            if proc and was_running:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except Exception:
                    proc.kill()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        pass
            if rec:
                rec["running"] = False
                rec["exit_code"] = proc.returncode if proc else None
                fh = rec.get("fh")
                if fh:
                    try:
                        fh.close()
                    except Exception:
                        pass
                    rec["fh"] = None
        if retire:
            self.platform.retire_agent(aid, "执行节点被手动停止")
        return True, ("已停止 " + aid) if was_running else ("没有运行中的进程，已标记离线")

    def autostart(self):
        for cfg in self.store.list_agent_configs():
            if cfg["enabled"] and cfg["autostart"]:
                ok, msg = self.start(cfg["id"])
                print(f"[platform] autostart {cfg['id']}: {msg}", file=sys.stderr, flush=True)

    def stop_all(self):
        for aid in list(self.snapshot()):
            self.stop(aid, retire=False)


# ══════════════════════ 表单 / API 校验 ══════════════════════
def _to_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("", "0", "false", "off", "no", "none")


def _json_field(value, label, expect, default):
    if value is None or value == "":
        return default
    if isinstance(value, expect):
        return value
    if isinstance(value, str):
        try:
            obj = json.loads(value)
        except Exception:
            raise ValueError(f"{label} 不是合法 JSON")
        if not isinstance(obj, expect):
            raise ValueError(f"{label} 必须是 JSON {'数组' if expect is list else '对象'}")
        return obj
    raise ValueError(f"{label} 类型不正确")


def _str_map(value, label):
    obj = _json_field(value, label, dict, {})
    return {str(k): (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
            for k, v in obj.items()}


def _str_list(value, label):
    obj = _json_field(value, label, list, [])
    if not all(isinstance(x, str) for x in obj):
        raise ValueError(f"{label} 必须是字符串数组")
    return obj


def normalize_steps(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            raise ValueError("步骤不是合法 JSON")
    if not isinstance(value, list) or not value:
        raise ValueError("步骤必须是非空 JSON 数组")
    out = []
    for i, s in enumerate(value, 1):
        if not isinstance(s, dict):
            raise ValueError(f"第 {i} 步必须是对象")
        tool = str(s.get("tool", "")).strip()
        if not tool:
            raise ValueError(f"第 {i} 步缺少 tool")
        args = s.get("args", {})
        if not isinstance(args, dict):
            raise ValueError(f"第 {i} 步 args 必须是对象")
        for k in ("assert_contains", "assert_not_contains"):
            if k in s and not isinstance(s[k], str):
                raise ValueError(f"第 {i} 步 {k} 必须是字符串")
        if "commit" in s and not isinstance(s["commit"], bool):
            raise ValueError(f"第 {i} 步 commit 必须是布尔值")
        ns = dict(s)
        ns["tool"] = tool
        ns["args"] = args
        out.append(ns)
    return out


NAVIGATION_HINT_MAX_CHARS = 8000


def validate_case(data, existing=None):
    base = existing or {}
    name = str(data.get("name", base.get("name", ""))).strip()
    if not name:
        raise ValueError("用例名称不能为空")
    mode = str(data.get("mode", base.get("mode", "scripted")) or "scripted").lower()
    if mode not in ("scripted", "ai"):
        raise ValueError("用例模式必须是 scripted / ai")
    instruction = str(data.get("instruction", base.get("instruction", "")) or "").strip()
    expected = str(data.get("expected", base.get("expected", "")) or "").strip()
    navigation_hint = str(data.get("navigation_hint",
                                   base.get("navigation_hint", "")) or "").strip()
    if len(navigation_hint) > NAVIGATION_HINT_MAX_CHARS:
        raise ValueError(f"站点导航提示不能超过 {NAVIGATION_HINT_MAX_CHARS} 字符")
    try:
        max_retry = int(data.get("max_retry", base.get("max_retry", -1)))
    except Exception:
        raise ValueError("最大重试次数必须是整数")
    if not -1 <= max_retry <= 10:
        raise ValueError("最大重试次数必须在 -1~10 之间（-1 表示使用平台默认）")
    side_effect = _to_bool(data.get("side_effect", base.get("side_effect", False)), False)
    if side_effect:
        # 提交/删除类用例不能自动重试，避免重复产生副作用。
        max_retry = 0
    try:
        max_steps = int(data.get("max_steps",
                                 base.get("max_steps", limits.AI_DEFAULT_MAX_STEPS)))
    except Exception:
        raise ValueError("AI 最大步数必须是整数")
    ceiling = limits.AI_MAX_STEPS_CEILING
    if STORE is not None:
        try:
            ceiling = int(STORE.get_budget()["max_steps_ceiling"])
        except Exception:
            pass
    if not 1 <= max_steps <= ceiling:
        raise ValueError(f"AI 最大步数必须在 1~{ceiling}")
    raw_steps = data.get("steps", base.get("steps"))
    if mode == "ai":
        if not instruction:
            raise ValueError("AI 用例必须填写自然语言步骤")
        if not expected:
            raise ValueError("AI 用例必须填写预期结果")
        steps = []          # AI 模式不依赖隐藏的脚本步骤
    else:
        steps = normalize_steps(raw_steps)
        instruction = instruction if instruction else ""
        expected = expected if expected else ""
    default_timeout = (limits.AI_RECOMMENDED_TIMEOUT_S if mode == "ai" else 60)
    try:
        timeout_s = float(data.get("timeout_s", base.get("timeout_s", default_timeout)))
    except Exception:
        raise ValueError("超时必须是数字")
    if timeout_s <= 0 or timeout_s > 3600:
        raise ValueError("超时必须在 1~3600 秒之间")
    mcp_id = str(data.get("mcp_id", base.get("mcp_id", "")) or "").strip()
    if mcp_id:
        m = STORE.get_mcp(mcp_id)
        if not m:
            raise ValueError(f"MCP 不存在: {mcp_id}")
        if not m["enabled"]:
            raise ValueError(f"MCP 已停用: {mcp_id}")
    kind = str(data.get("kind", base.get("kind", "test")) or "test").lower()
    if kind not in ("test", "setup"):
        raise ValueError("用例用途必须是 test / setup")
    profile_id = str(data.get("profile_id", base.get("profile_id", "")) or "").strip()
    if profile_id:
        profile = STORE.get_env_profile(profile_id)
        if not profile:
            raise ValueError(f"执行环境 Profile 不存在: {profile_id}")
        if kind == "setup":
            raise ValueError("采集用例不能依赖执行环境 Profile")
        if not mcp_id:
            raise ValueError("选择执行环境 Profile 时必须指定 MCP")
        source = profile.get("source_mcp_id") or ""
        if source and source != mcp_id:
            raise ValueError(f"Profile 由 {source} 采集，当前用例使用 {mcp_id}；请使用相同 MCP")
    return {
        "name": name[:200],
        "description": str(data.get("description", base.get("description", "")) or ""),
        "tags": str(data.get("tags", base.get("tags", "")) or ""),
        "mcp_id": mcp_id,
        "timeout_s": timeout_s,
        "steps": steps,
        "mode": mode,
        "instruction": instruction,
        "expected": expected,
        "max_steps": max_steps,
        "navigation_hint": navigation_hint,
        "max_retry": max_retry,
        "side_effect": side_effect,
        "kind": kind,
        "profile_id": profile_id,
        "enabled": _to_bool(data.get("enabled", base.get("enabled", True)), True),
    }


def validate_env_profile(data, existing=None):
    base = existing or {}
    name = str(data.get("name", base.get("name", "")) or "").strip()
    if not name:
        raise ValueError("Profile 名称不能为空")
    setup_case_id = str(data.get("setup_case_id", base.get("setup_case_id", "")) or "").strip()
    if not setup_case_id:
        raise ValueError("必须选择采集用例")
    case = STORE.get_case(setup_case_id)
    if not case:
        raise ValueError(f"采集用例不存在: {setup_case_id}")
    if case.get("kind") != "setup":
        raise ValueError("采集用例必须标记为 setup")
    if case.get("profile_id"):
        raise ValueError("采集用例不能依赖执行环境 Profile")
    mcp = STORE.get_mcp(case.get("mcp_id") or "")
    if not mcp:
        mcp = STORE.default_mcp()
    if not mcp:
        raise ValueError("没有可用的 MCP")
    if not mcp.get("enabled"):
        raise ValueError(f"MCP 已停用: {mcp['id']}")
    if not profiles.detect_adapter(mcp):
        raise ValueError(f"MCP {mcp['id']} 暂不支持环境采集/注入")
    return {
        "name": name[:200],
        "description": str(data.get("description", base.get("description", "")) or "")[:2000],
        "setup_case_id": setup_case_id,
        "source_mcp_id": mcp["id"],
    }


def validate_mcp(data, existing=None, mid=None):
    base = existing or {}
    mcp_id = mid or str(data.get("id", base.get("id", ""))).strip()
    if not ID_RE.match(mcp_id):
        raise ValueError("MCP ID 只能包含字母、数字、下划线、点、短横线（1~64 字符）")
    if not existing and STORE.get_mcp(mcp_id):
        raise ValueError(f"MCP ID 已存在: {mcp_id}")
    name = str(data.get("name", base.get("name", ""))).strip()
    if not name:
        raise ValueError("MCP 名称不能为空")
    transport = str(data.get("transport", base.get("transport", "stdio"))).lower()
    if transport == "streamable-http":
        transport = "http"
    if transport not in ("stdio", "sse", "http"):
        raise ValueError("传输方式必须是 stdio / sse / http")
    command = str(data.get("command", base.get("command", "")) or "").strip()
    url = str(data.get("url", base.get("url", "")) or "").strip()
    if transport == "stdio" and not command:
        raise ValueError("stdio MCP 必须填写命令")
    if transport in ("sse", "http") and not url:
        raise ValueError(f"{transport} MCP 必须填写 URL")
    return {
        "id": mcp_id, "name": name[:200], "transport": transport, "command": command,
        "args": _str_list(data.get("args", base.get("args")), "参数"),
        "env": _str_map(data.get("env", base.get("env")), "环境变量"),
        "url": url,
        "headers": _str_map(data.get("headers", base.get("headers")), "请求头"),
        "enabled": _to_bool(data.get("enabled", base.get("enabled", True)), True),
        "builtin": bool(base.get("builtin", False)),
    }


def validate_agent(data, existing=None, aid=None):
    base = existing or {}
    agent_id = aid or str(data.get("id", base.get("id", ""))).strip()
    if not ID_RE.match(agent_id):
        raise ValueError("节点 ID 只能包含字母、数字、下划线、点、短横线（1~64 字符）")
    if not existing and STORE.get_agent_config(agent_id):
        raise ValueError(f"节点 ID 已存在: {agent_id}")
    name = str(data.get("name", base.get("name", ""))).strip()
    if not name:
        raise ValueError("执行节点名称不能为空")
    kind = str(data.get("kind", base.get("kind", "builtin"))).lower()
    if kind not in ("builtin", "custom"):
        raise ValueError("执行节点类型必须是 builtin / custom")
    host = str(data.get("host", base.get("host", "127.0.0.1")) or "127.0.0.1").strip()
    try:
        port = int(data.get("port", base.get("port", 8001)))
        slots = int(data.get("slots", base.get("slots", 1)))
    except Exception:
        raise ValueError("端口和容量必须是整数")
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1~65535")
    if not 1 <= slots <= 64:
        raise ValueError("容量必须在 1~64")
    command = _str_list(data.get("command", base.get("command")), "启动命令")
    if kind == "custom" and not command:
        raise ValueError("custom 类型必须填写启动命令")
    for other in STORE.list_agent_configs():
        if other["id"] != agent_id and other["host"] == host and other["port"] == port:
            raise ValueError(f"端口 {host}:{port} 已被执行节点 {other['id']} 使用")
    return {
        "id": agent_id, "name": name[:200], "kind": kind, "host": host,
        "port": port, "slots": slots,
        "tags": str(data.get("tags", base.get("tags", "")) or ""),
        "command": command, "cwd": str(data.get("cwd", base.get("cwd", "")) or ""),
        "env": _str_map(data.get("env", base.get("env")), "环境变量"),
        "autostart": _to_bool(data.get("autostart", base.get("autostart", False))),
        "enabled": _to_bool(data.get("enabled", base.get("enabled", True)), True),
    }


OBSERVATION_TOOLS = {"snapshot", "page_text", "playwright_get_visible_text",
                     "playwright_get_visible_html", "browser_snapshot",
                     "inspect_page", "expect_visible_text"}


def freeze_run(task_id):
    """把 AI 运行里的真实动作固化成 scripted 用例（观察步骤不固化）。"""
    run = STORE.get_run(task_id)
    if not run:
        raise ValueError("运行记录不存在")
    steps = []
    for s in ((run.get("result") or {}).get("steps") or []):
        tool = s.get("tool")
        if not tool or tool == "__finish__" or tool in OBSERVATION_TOOLS:
            continue
        args = s.get("args") or {}
        if isinstance(args, dict):
            steps.append({"tool": tool, "args": args})
    if not steps:
        raise ValueError("这次运行没有可固化的动作步骤（可能只有观察/判定）")
    case = run.get("case") or {}
    verdict = (run.get("result") or {}).get("verdict", "")
    evidence = ((run.get("result") or {}).get("evidence") or "")[:200]
    data = {
        "name": (run.get("name") or task_id) + "（固化）",
        "description": f"由 AI 运行 {task_id} 固化；verdict={verdict}；证据：{evidence}。"
                       f"只固化动作步骤，建议按业务补充断言。",
        "tags": "frozen,ai",
        "mcp_id": run.get("mcp_id") or case.get("mcp_id", ""),
        "timeout_s": case.get("timeout_s", 180),
        "steps": steps,
        "mode": "scripted",
        "enabled": True,
        "side_effect": bool(case.get("side_effect")),
        "max_retry": (0 if case.get("side_effect")
                      else int(case.get("max_retry", -1))),
    }
    return STORE.create_case(validate_case(data))


def run_cases(case_ids, priority=5, max_retry=None, target_node_id=None):
    cases = []
    for cid in case_ids:
        c = STORE.get_case(cid)
        if not c:
            raise ValueError(f"用例不存在: {cid}")
        cases.append(c)
    if not cases:
        raise ValueError("没有选择用例")
    return P.add_tasks(cases, priority, max_retry, target_node_id)


# ══════════════════════ HTTP 服务 ══════════════════════
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ── 基础响应 ──────────────────────────────────────────────────
    def _send(self, body, code=200, ctype="application/json; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False), code)

    def _html(self, text, code=200):
        self._send(text, code, ctype="text/html; charset=utf-8")

    def _redirect(self, location, code=303):
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _raw(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json_body(self):
        raw = self._raw()
        try:
            d = json.loads(raw.decode("utf-8") or "{}")
            return d if isinstance(d, dict) else {}
        except Exception:
            raise ValueError("请求体不是合法 JSON")

    def _form(self):
        return urllib.parse.parse_qs(self._raw().decode("utf-8"), keep_blank_values=True)

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)

    def _flash(self, path, msg="", err=""):
        q = {}
        if msg:
            q["msg"] = msg
        if err:
            q["err"] = err
        if q:
            path += ("&" if "?" in path else "?") + urllib.parse.urlencode(q)
        self._redirect(path)

    def _api_exc(self, e, code=400):
        self._json({"ok": False, "error": mcp_runtime.describe_exc(e) if not isinstance(e, ValueError) else str(e)}, code)

    # ── GET ───────────────────────────────────────────────────────
    def do_GET(self):
        try:
            path, parts, q = self._parts()
            if parts and parts[0] == "api":
                try:
                    return self._api_get(parts, q)
                except Exception as e:
                    return self._api_exc(e, 400)
            return self._page_get(parts, q)
        except Exception as e:
            self._server_error(e)

    def _parts(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        parts = [p for p in path.split("/") if p]
        return path, parts, self._query()

    def _execution_nodes(self):
        status = P.status()
        return (status.get("agents") or []) + (status.get("remote_nodes") or [])

    def _api_get(self, parts, q):
        if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "screenshots":
            run = STORE.get_run(parts[2])
            referenced = run and any(
                isinstance(step.get("screenshot"), dict)
                and step["screenshot"].get("file") == parts[4]
                for step in (run.get("result") or {}).get("steps", []))
            if not referenced:
                return self._json({"error": "截图不存在"}, 404)
            try:
                data, mime = screenshots.read_image(parts[2], parts[4])
            except (OSError, ValueError):
                return self._json({"error": "截图不存在"}, 404)
            return self._send(data, ctype=mime)
        if parts == ["api", "health"]:
            return self._json({"ok": True, "service": "platform"})
        if parts == ["api", "status"]:
            return self._json(P.status())
        if parts == ["api", "remote", "nodes"]:
            return self._json({"nodes": P.remote.list_nodes() if P.remote else []})
        if len(parts) == 4 and parts[:3] == ["api", "remote", "nodes"]:
            nodes = P.remote.list_nodes() if P.remote else []
            node = next((n for n in nodes if n.get("node_id") == parts[3]), None)
            return self._json(node, 200) if node else self._json({"error": "节点不存在"}, 404)
        if parts == ["api", "tasks"]:
            return self._json({"tasks": P.task_summaries()})
        if parts == ["api", "trace"]:
            tid = _q1(q, "task_id")
            with P.lock:
                t = P.tasks.get(tid)
            if not t:
                r = STORE.get_run(tid)
                return self._json({"task": r}, 200) if r else self._json({"error": "未知任务"}, 404)
            public = dict(t)
            public.pop("profile", None)  # storage state 只用于下发，不暴露到调试 API
            return self._json({"task": public})
        if parts == ["api", "cases"]:
            return self._json({"cases": STORE.list_cases(_q1(q, "q"))})
        if len(parts) == 3 and parts[:2] == ["api", "cases"]:
            c = STORE.get_case(parts[2])
            return self._json(c, 200) if c else self._json({"error": "用例不存在"}, 404)
        if parts == ["api", "env-profiles"]:
            return self._json({"profiles": STORE.list_env_profiles(_q1(q, "q"))})
        if len(parts) == 3 and parts[:2] == ["api", "env-profiles"]:
            p = STORE.get_env_profile(parts[2])
            if not p:
                return self._json({"error": "Profile 不存在"}, 404)
            p = dict(p)
            p["versions"] = STORE.list_profile_versions(parts[2])
            return self._json(p)
        if parts == ["api", "budget"]:
            return self._json({"budget": STORE.get_budget()})
        if parts == ["api", "llm"]:
            return self._json({"config": llm_lib.public_llm_config()})
        if parts == ["api", "mcps"]:
            return self._json({"mcps": STORE.list_mcps()})
        if len(parts) == 3 and parts[:2] == ["api", "mcps"]:
            m = STORE.get_mcp(parts[2])
            return self._json(m, 200) if m else self._json({"error": "MCP 不存在"}, 404)
        if parts == ["api", "agents", "configs"]:
            return self._json({"configs": STORE.list_agent_configs(),
                               "procs": AM.snapshot(), "runtime": P.status()["agents"]})
        if len(parts) == 4 and parts[:3] == ["api", "agents", "configs"]:
            c = STORE.get_agent_config(parts[3])
            return self._json(c, 200) if c else self._json({"error": "执行节点不存在"}, 404)
        if parts == ["api", "runs"]:
            try:
                limit = min(500, max(1, int(_q1(q, "limit") or 100)))
            except Exception:
                limit = 100
            return self._json({"runs": STORE.list_runs(limit, _q1(q, "state"))})
        if len(parts) == 3 and parts[:2] == ["api", "runs"]:
            r = STORE.get_run(parts[2])
            return self._json(r, 200) if r else self._json({"error": "运行记录不存在"}, 404)
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "logs":
            return self._logs_api(parts[2], q)
        return self._json({"error": "not found"}, 404)

    def _logs_api(self, task_id, q):
        try:
            after = int(_q1(q, "after") or 0)
        except Exception:
            after = 0
        logs = STORE.list_logs(task_id, after)
        r = STORE.get_run(task_id)
        if not r:
            return self._json({"error": "运行记录不存在"}, 404)
        last = logs[-1]["id"] if logs else after
        return self._json({
            "logs": logs, "last_id": last, "state": r["state"],
            "terminal": r["state"] in ("done", "failed"),
        })

    def _page_get(self, parts, q):
        msg, err = _q1(q, "msg"), _q1(q, "err")
        if not parts:
            return self._html(ui.dashboard({
                "status": P.status(), "runs": STORE.list_runs(15),
                "case_count": len(STORE.list_cases()), "run_count": STORE.count_runs(),
            }), 200)
        if parts == ["cases"]:
            return self._html(ui.cases_page({
                "cases": STORE.list_cases(_q1(q, "q")), "q": _q1(q, "q"),
                "mcps": STORE.list_mcps(), "default_mcp": STORE.default_mcp(),
                "profiles": {p["profile_id"]: p for p in STORE.list_env_profiles()},
                "execution_nodes": self._execution_nodes(),
                "msg": msg, "err": err,
            }))
        if parts == ["cases", "new"]:
            return self._html(ui.case_form({
                "case": {}, "is_new": True, "mcps": STORE.list_mcps(),
                "profiles": STORE.list_env_profiles(),
                "budget": STORE.get_budget(), "err": err,
            }))
        if len(parts) == 3 and parts[0] == "cases" and parts[2] == "edit":
            c = STORE.get_case(parts[1])
            if not c:
                return self._html("<h1>用例不存在</h1>", 404)
            return self._html(ui.case_form({
                "case": c, "is_new": False, "mcps": STORE.list_mcps(),
                "profiles": STORE.list_env_profiles(),
                "budget": STORE.get_budget(), "err": err,
            }))
        if parts == ["profiles"]:
            return self._html(ui.env_profiles_page({
                "profiles": STORE.list_env_profiles(_q1(q, "q")),
                "q": _q1(q, "q"), "msg": msg, "err": err,
                "case_names": {c["case_id"]: c["name"] for c in STORE.list_cases()},
                "mcp_names": {m["id"]: m["name"] for m in STORE.list_mcps()},
                "execution_nodes": self._execution_nodes(),
            }))
        if parts == ["profiles", "new"]:
            return self._html(ui.env_profile_form({
                "profile": {}, "is_new": True,
                "setup_cases": STORE.list_cases(kind="setup"),
                "mcps": STORE.list_mcps(), "err": err,
            }))
        if len(parts) == 2 and parts[0] == "profiles":
            p = STORE.get_env_profile(parts[1])
            if not p:
                return self._html("<h1>Profile 不存在</h1>", 404)
            return self._html(ui.env_profile_detail({
                "profile": p, "versions": STORE.list_profile_versions(parts[1]),
                "setup_case": STORE.get_case(p.get("setup_case_id") or ""),
                "mcp": STORE.get_mcp(p.get("source_mcp_id") or ""),
                "used_by": STORE.cases_using_profile(parts[1]),
                "execution_nodes": self._execution_nodes(),
                "msg": msg, "err": err,
            }))
        if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "edit":
            p = STORE.get_env_profile(parts[1])
            if not p:
                return self._html("<h1>Profile 不存在</h1>", 404)
            return self._html(ui.env_profile_form({
                "profile": p, "is_new": False,
                "setup_cases": STORE.list_cases(kind="setup"),
                "mcps": STORE.list_mcps(), "err": err,
            }))
        if parts == ["agents"]:
            return self._html(ui.agents_page({
                "configs": STORE.list_agent_configs(), "status": P.status(),
                "procs": AM.snapshot(), "here": str(ROOT), "log_dir": str(LOG_DIR),
                "msg": msg, "err": err,
            }))
        if len(parts) == 3 and parts[:2] == ["agents", "remote"]:
            nodes = P.remote.list_nodes() if P.remote else []
            node = next((n for n in nodes if n.get("node_id") == parts[2]), None)
            if not node:
                return self._html("<h1>远程节点不存在</h1>", 404)
            runs = [r for r in STORE.list_runs(50) if r.get("node_id") == parts[2]][:10]
            return self._html(ui.remote_node_page({"node": node, "runs": runs}))
        if parts == ["agents", "new"]:
            return self._html(ui.agent_form({
                "agent": {}, "is_new": True, "here": str(ROOT),
                "log_dir": str(LOG_DIR), "err": err,
            }))
        if len(parts) == 3 and parts[0] == "agents" and parts[2] == "edit":
            c = STORE.get_agent_config(parts[1])
            if not c:
                return self._html("<h1>执行节点不存在</h1>", 404)
            return self._html(ui.agent_form({
                "agent": c, "is_new": False, "here": str(ROOT),
                "log_dir": str(LOG_DIR), "err": err,
            }))
        if parts == ["budget"]:
            return self._html(ui.budget_page({
                "budget": STORE.get_budget(),
                "llm": llm_lib.public_llm_config(),
                "default_pricing_json": json.dumps(
                    budget.DEFAULT_PRICING, ensure_ascii=False, indent=2),
                "msg": msg, "err": err,
            }))
        if parts == ["llm"]:
            return self._html(ui.llm_page({
                "config": llm_lib.public_llm_config(), "msg": msg, "err": err,
            }))
        if parts == ["mcps"]:
            return self._html(ui.mcps_page({
                "mcps": STORE.list_mcps(), "msg": msg, "err": err,
            }))
        if parts == ["mcps", "new"]:
            return self._html(ui.mcp_form({
                "mcp": {}, "is_new": True, "err": err,
            }))
        if len(parts) == 2 and parts[0] == "mcps":
            m = STORE.get_mcp(parts[1])
            if not m:
                return self._html("<h1>MCP 不存在</h1>", 404)
            return self._html(ui.mcp_detail({"mcp": m, "msg": msg, "err": err}))
        if len(parts) == 3 and parts[0] == "mcps" and parts[2] == "edit":
            m = STORE.get_mcp(parts[1])
            if not m:
                return self._html("<h1>MCP 不存在</h1>", 404)
            return self._html(ui.mcp_form({"mcp": m, "is_new": False, "err": err}))
        if parts == ["runs"]:
            return self._html(ui.runs_page({
                "runs": STORE.list_runs(200, _q1(q, "state")),
                "state": _q1(q, "state"), "msg": msg, "err": err,
            }))
        if len(parts) == 2 and parts[0] == "runs":
            r = STORE.get_run(parts[1])
            if not r:
                return self._html("<h1>运行记录不存在</h1>", 404)
            return self._html(ui.run_detail({
                "run": r, "profile": STORE.get_env_profile(r.get("profile_id") or ""),
                "msg": msg, "err": err,
            }))
        return self._html("<h1>404</h1><p><a href='/'>返回看板</a></p>", 404)

    # ── POST ──────────────────────────────────────────────────────
    def do_POST(self):
        try:
            path, parts, q = self._parts()
            if parts and parts[0] == "api":
                try:
                    return self._api_post(parts)
                except ValueError as e:
                    return self._api_exc(e, 400)
                except Exception as e:
                    return self._api_exc(e, 500)
            return self._page_post(parts)
        except Exception as e:
            self._server_error(e)

    def _api_post(self, parts):
        if parts == ["api", "tasks"]:
            d = self._json_body()
            cases = d.get("cases") or []
            if d.get("mcp_id"):
                for c in cases:
                    c.setdefault("mcp_id", d["mcp_id"])
            try:
                ids = P.add_tasks(cases, int(d.get("priority", 5)), d.get("max_retry"), d.get("target_node_id"))
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "task_ids": ids}, 201)
        if parts == ["api", "runs"]:
            d = self._json_body()
            try:
                ids = run_cases(d.get("case_ids") or [], int(d.get("priority", 5)), d.get("max_retry"), d.get("target_node_id"))
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "task_ids": ids}, 201)
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "freeze":
            try:
                case = freeze_run(parts[2])
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "case": case}, 201)
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "delete":
            try:
                deleted = P.delete_run(parts[2])
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            if not deleted:
                return self._json({"ok": False, "error": "运行记录不存在"}, 404)
            return self._json({"ok": True})
        if len(parts) == 5 and parts[:3] == ["api", "remote", "nodes"] and parts[4] == "slots":
            # 管理监听器专属：只允许修改平台授权上限，token/CIDR/MCP 仍离线维护。
            if not P.remote:
                return self._json({"ok": False, "error": "远程网关未启用"}, 400)
            if parts[3] not in P.remote.policies["nodes"]:
                return self._json({"ok": False, "error": "节点不存在"}, 404)
            d = self._json_body()
            try:
                result = P.remote.update_grant(parts[3], d.get("max_slots"))
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, **result})
        if parts == ["api", "budget"]:
            try:
                b = STORE.save_budget(self._json_body())
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "budget": b})
        if parts == ["api", "llm"]:
            try:
                cfg = llm_lib.save_llm_config(self._json_body())
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "config": cfg})
        if parts == ["api", "llm", "test"]:
            try:
                res = llm_lib.test_llm()
            except Exception as e:
                return self._json({"ok": False, "error": mcp_runtime.describe_exc(e)}, 400)
            return self._json({"ok": True, **res})
        if parts == ["api", "llm", "import-env"]:
            try:
                cfg = llm_lib.import_llm_env()
            except Exception as e:
                return self._json({"ok": False, "error": mcp_runtime.describe_exc(e)}, 400)
            return self._json({"ok": True, "config": cfg})
        if parts == ["api", "logs"]:
            d = self._json_body()
            tid = str(d.get("task_id") or "")
            if not tid or not STORE.get_run(tid):
                return self._json({"ok": False, "error": "未知任务"}, 404)
            logs = d.get("logs") or []
            if not isinstance(logs, list):
                return self._json({"ok": False, "error": "logs 必须是数组"}, 400)
            STORE.add_logs(tid, logs[:500])
            return self._json({"ok": True, "count": len(logs[:500])})
        if parts == ["api", "results"]:
            return self._json(P.on_result(self._json_body()))
        if len(parts) == 3 and parts[:2] == ["api", "agents"]:
            if parts[2] == "register":
                return self._json(P.register(self._json_body()))
            if parts[2] == "heartbeat":
                return self._json(P.heartbeat(self._json_body()))
            if parts[2] == "results":
                return self._json(P.on_result(self._json_body()))
        if len(parts) == 5 and parts[:3] == ["api", "agents", "configs"] and parts[4] == "start":
            ok, msg = AM.start(parts[3])
            return self._json({"ok": ok, "msg": msg}, 200 if ok else 400)
        if len(parts) == 5 and parts[:3] == ["api", "agents", "configs"] and parts[4] == "stop":
            ok, msg = AM.stop(parts[3])
            return self._json({"ok": ok, "msg": msg})
        if parts == ["api", "agents", "configs"]:
            d = self._json_body()
            v = validate_agent(d)
            try:
                cfg = STORE.create_agent_config(v)
            except Exception as e:
                return self._json({"ok": False, "error": f"保存失败: {e}"}, 400)
            return self._json({"ok": True, "config": cfg}, 201)
        if len(parts) == 4 and parts[:3] == ["api", "agents", "configs"]:
            old = STORE.get_agent_config(parts[3])
            if not old:
                return self._json({"ok": False, "error": "执行节点不存在"}, 404)
            v = validate_agent(self._json_body(), old, parts[3])
            return self._json({"ok": True, "config": STORE.update_agent_config(parts[3], v)})
        if parts == ["api", "mcps"]:
            d = self._json_body()
            v = validate_mcp(d)
            return self._json({"ok": True, "mcp": STORE.create_mcp(v)}, 201)
        if len(parts) == 3 and parts[:2] == ["api", "mcps"]:
            old = STORE.get_mcp(parts[2])
            if not old:
                return self._json({"ok": False, "error": "MCP 不存在"}, 404)
            v = validate_mcp(self._json_body(), old, parts[2])
            return self._json({"ok": True, "mcp": STORE.update_mcp(parts[2], v)})
        if len(parts) == 4 and parts[:2] == ["api", "mcps"] and parts[3] == "probe":
            m = STORE.get_mcp(parts[2])
            if not m:
                return self._json({"ok": False, "error": "MCP 不存在"}, 404)
            tools, err = mcp_runtime.probe_tools(m, EXECUTOR_ROOT, timeout=90)
            STORE.set_mcp_probe(parts[2], tools, err)
            return self._json({"ok": not err, "tools": tools, "error": err})
        if parts == ["api", "cases"]:
            d = self._json_body()
            try:
                v = validate_case(d)
                c = STORE.create_case(v)
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "case": c}, 201)
        if len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "run":
            data = self._json_body()
            try:
                ids = run_cases([parts[2]], max_retry=data.get("max_retry"),
                                target_node_id=data.get("target_node_id"))
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "task_ids": ids}, 201)
        if len(parts) == 3 and parts[:2] == ["api", "cases"]:
            old = STORE.get_case(parts[2])
            if not old:
                return self._json({"ok": False, "error": "用例不存在"}, 404)
            try:
                v = validate_case(self._json_body(), old)
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "case": STORE.update_case(parts[2], v)})
        if parts == ["api", "env-profiles"]:
            try:
                v = validate_env_profile(self._json_body())
                p = STORE.create_env_profile(v)
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "profile": p}, 201)
        if (len(parts) == 4 and parts[:2] == ["api", "env-profiles"]
                and parts[3] == "capture"):
            try:
                d = self._json_body()
                tid = P.add_capture_task(parts[2], target_node_id=d.get("target_node_id"))
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": True, "task_id": tid}, 201)
        return self._json({"error": "not found"}, 404)

    def _page_post(self, parts):
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "delete":
            try:
                deleted = P.delete_run(parts[1])
            except ValueError as e:
                return self._flash(f"/runs/{parts[1]}", err=str(e))
            if not deleted:
                return self._flash("/runs", err="运行记录不存在")
            return self._flash("/runs", msg="运行记录已删除（含日志与截图）")
        if parts in (["runs", "delete-selected"], ["runs", "delete-finished"]):
            if parts[-1] == "delete-finished":
                ids = [r["task_id"] for r in STORE.list_runs(100000)
                       if r.get("state") in ("done", "failed")]
            else:
                ids = self._form().get("task_id") or []
            deleted = skipped = 0
            for tid in ids:
                try:
                    if P.delete_run(tid):
                        deleted += 1
                except ValueError:
                    skipped += 1
            return self._flash(
                "/runs",
                msg=f"已删除 {deleted} 条运行记录" if deleted else "",
                err=f"{skipped} 条运行尚未结束，未删除" if skipped else "")
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "freeze":
            try:
                case = freeze_run(parts[1])
            except ValueError as e:
                return self._flash(f"/runs/{parts[1]}", err=str(e))
            return self._flash(f"/cases/{case['case_id']}/edit",
                               msg="已把 AI 动作固化为脚本用例，请补充断言后保存")
        if parts in (["cases", "run-all"], ["cases", "run-selected"]):
            form = self._form()
            try:
                if parts[-1] == "run-all":
                    ids = [c["case_id"] for c in STORE.list_cases()
                           if c["enabled"] and c.get("kind") != "setup"]
                else:
                    ids = form.get("case_ids") or []
                task_ids = run_cases(ids, target_node_id=_fget(form, "target_node_id"))
            except ValueError as e:
                return self._flash("/cases", err=str(e))
            return self._flash("/runs", msg=f"已提交 {len(task_ids)} 个任务")
        if parts == ["cases", "new"]:
            form = self._form()
            raw = self._case_raw(form)
            try:
                v = validate_case(raw)
            except ValueError as e:
                return self._html(ui.case_form({
                    "case": raw, "steps_raw": _fget(form, "steps"),
                    "is_new": True, "mcps": STORE.list_mcps(),
                    "profiles": STORE.list_env_profiles(),
                    "budget": STORE.get_budget(), "err": str(e),
                }), 400)
            STORE.create_case(v)
            return self._flash("/cases", msg="用例已创建")
        if len(parts) == 3 and parts[0] == "cases" and parts[2] == "edit":
            form = self._form()
            raw = self._case_raw(form)
            old = STORE.get_case(parts[1])
            if not old:
                return self._html("<h1>用例不存在</h1>", 404)
            try:
                v = validate_case(raw, old)
            except ValueError as e:
                merged = dict(old)
                merged.update(raw)
                return self._html(ui.case_form({
                    "case": merged, "steps_raw": _fget(form, "steps"),
                    "is_new": False, "mcps": STORE.list_mcps(),
                    "profiles": STORE.list_env_profiles(),
                    "budget": STORE.get_budget(), "err": str(e),
                }), 400)
            STORE.update_case(parts[1], v)
            return self._flash("/cases", msg="用例已保存")
        if len(parts) == 3 and parts[0] == "cases" and parts[2] == "delete":
            STORE.delete_case(parts[1])
            return self._flash("/cases", msg="用例已删除")
        if len(parts) == 3 and parts[0] == "cases" and parts[2] == "run":
            form = self._form()
            try:
                ids = run_cases([parts[1]], target_node_id=_fget(form, "target_node_id"))
            except ValueError as e:
                return self._flash("/cases", err=str(e))
            return self._flash("/runs", msg=f"已提交 {len(ids)} 个任务")
        if parts == ["profiles", "new"]:
            form = self._form()
            raw = self._profile_raw(form)
            try:
                v = validate_env_profile(raw)
            except ValueError as e:
                return self._html(ui.env_profile_form({
                    "profile": raw, "is_new": True,
                    "setup_cases": STORE.list_cases(kind="setup"),
                    "mcps": STORE.list_mcps(), "err": str(e),
                }), 400)
            STORE.create_env_profile(v)
            return self._flash("/profiles", msg="Profile 已创建，可以点击“采集”生成登录态")
        if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "edit":
            form = self._form()
            raw = self._profile_raw(form)
            old = STORE.get_env_profile(parts[1])
            if not old:
                return self._html("<h1>Profile 不存在</h1>", 404)
            try:
                v = validate_env_profile(raw, old)
            except ValueError as e:
                merged = dict(old)
                merged.update(raw)
                return self._html(ui.env_profile_form({
                    "profile": merged, "is_new": False,
                    "setup_cases": STORE.list_cases(kind="setup"),
                    "mcps": STORE.list_mcps(), "err": str(e),
                }), 400)
            STORE.update_env_profile(parts[1], v)
            return self._flash(f"/profiles/{parts[1]}", msg="Profile 已保存；如采集用例变更，请重新采集")
        if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "capture":
            form = self._form()
            try:
                tid = P.add_capture_task(parts[1], target_node_id=_fget(form, "target_node_id"))
            except ValueError as e:
                return self._flash(f"/profiles/{parts[1]}", err=str(e))
            return self._flash(f"/runs/{tid}", msg="环境采集任务已提交")
        if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "delete":
            p = STORE.get_env_profile(parts[1])
            if not p:
                return self._flash("/profiles", err="Profile 不存在")
            if p.get("status") == "capturing":
                return self._flash(f"/profiles/{parts[1]}", err="Profile 正在采集中，不能删除")
            used = STORE.cases_using_profile(parts[1])
            if used:
                return self._flash(f"/profiles/{parts[1]}",
                                   err="Profile 仍被用例使用: " + "、".join(
                                       x["name"] for x in used[:5]))
            STORE.delete_env_profile(parts[1])
            return self._flash("/profiles", msg="Profile 已删除")
        if parts == ["agents", "new"]:
            form = self._form()
            raw = self._agent_raw(form)
            try:
                v = validate_agent(raw)
            except ValueError as e:
                return self._html(ui.agent_form({
                    "agent": raw, "command_raw": _fget(form, "command"),
                    "env_raw": _fget(form, "env"), "is_new": True,
                    "here": str(ROOT), "log_dir": str(LOG_DIR), "err": str(e),
                }), 400)
            try:
                STORE.create_agent_config(v)
            except Exception as e:
                return self._flash("/agents", err=f"保存失败: {e}")
            return self._flash("/agents", msg="执行节点配置已创建")
        if len(parts) == 3 and parts[0] == "agents" and parts[2] == "edit":
            form = self._form()
            raw = self._agent_raw(form)
            old = STORE.get_agent_config(parts[1])
            if not old:
                return self._html("<h1>执行节点不存在</h1>", 404)
            try:
                v = validate_agent(raw, old, parts[1])
            except ValueError as e:
                merged = dict(old)
                merged.update(raw)
                return self._html(ui.agent_form({
                    "agent": merged, "command_raw": _fget(form, "command"),
                    "env_raw": _fget(form, "env"), "is_new": False,
                    "here": str(ROOT), "log_dir": str(LOG_DIR), "err": str(e),
                }), 400)
            STORE.update_agent_config(parts[1], v)
            return self._flash("/agents", msg="执行节点配置已保存；如进程在运行，请停止后重新启动使配置生效")
        if len(parts) == 3 and parts[0] == "agents" and parts[2] == "delete":
            AM.stop(parts[1])
            STORE.delete_agent_config(parts[1])
            return self._flash("/agents", msg="执行节点已删除")
        if len(parts) == 3 and parts[0] == "agents" and parts[2] == "start":
            ok, msg = AM.start(parts[1])
            return self._flash("/agents", msg=msg if ok else "", err="" if ok else msg)
        if len(parts) == 3 and parts[0] == "agents" and parts[2] == "stop":
            ok, msg = AM.stop(parts[1])
            return self._flash("/agents", msg=msg if ok else "", err="" if ok else msg)
        if len(parts) == 4 and parts[:2] == ["agents", "remote"] and parts[3] == "slots":
            if not P.remote:
                return self._flash(f"/agents/remote/{parts[2]}", err="远程网关未启用")
            form = self._form()
            try:
                value = int(_fget(form, "max_slots").strip())
            except (TypeError, ValueError):
                return self._flash(f"/agents/remote/{parts[2]}",
                                   err="平台授权容量必须是 1–128 的整数")
            try:
                result = P.remote.update_grant(parts[2], value)
            except ValueError as e:
                return self._flash(f"/agents/remote/{parts[2]}", err=str(e))
            effective = result.get("slots_effective")
            return self._flash(
                f"/agents/remote/{parts[2]}",
                msg=f"平台授权上限已更新为 {result['slots_granted']}，实际生效 "
                    f"{effective if effective is not None else '待节点上报'}")
        if parts == ["budget"]:
            form = self._form()
            raw_total = _fget(form, "max_total_tokens")
            raw_steps = _fget(form, "max_steps_ceiling")
            raw_pricing = _fget(form, "pricing_json")
            try:
                pricing_data = json.loads(raw_pricing or "[]")
                v = budget.normalize_budget({
                    "max_total_tokens": raw_total,
                    "max_steps_ceiling": raw_steps,
                    "pricing": pricing_data,
                })
                STORE.save_budget(v)
            except ValueError as e:
                return self._html(ui.budget_page({
                    "budget": {"max_total_tokens": raw_total,
                               "max_steps_ceiling": raw_steps,
                               "pricing": []},
                    "pricing_raw": raw_pricing,
                    "llm": llm_lib.public_llm_config(),
                    "default_pricing_json": json.dumps(
                        budget.DEFAULT_PRICING, ensure_ascii=False, indent=2),
                    "err": str(e),
                }), 400)
            return self._flash("/budget", msg="AI 预算设置已保存")
        if parts == ["llm"]:
            form = self._form()
            data = {
                "provider": _fget(form, "provider"), "base_url": _fget(form, "base_url"),
                "api_key": _fget(form, "api_key"), "model": _fget(form, "model"),
                "auth_header": _fget(form, "auth_header"),
                "max_tokens": _fget(form, "max_tokens"),
                "temperature": _fget(form, "temperature"),
                "timeout_s": _fget(form, "timeout_s"),
            }
            try:
                llm_lib.save_llm_config(data)
            except ValueError as e:
                return self._flash("/llm", err=str(e))
            return self._flash("/llm", msg="LLM 配置已保存")
        if parts == ["llm", "test"]:
            try:
                res = llm_lib.test_llm()
            except Exception as e:
                return self._flash("/llm", err=f"连接测试失败: {mcp_runtime.describe_exc(e)}")
            return self._flash("/llm", msg=f"连接成功：{res.get('model')}，"
                                            f"{res.get('latency_ms')} ms，回复={res.get('reply')}")
        if parts == ["llm", "import-env"]:
            try:
                llm_lib.import_llm_env()
            except Exception as e:
                return self._flash("/llm", err=f"导入失败: {mcp_runtime.describe_exc(e)}")
            return self._flash("/llm", msg="已从环境变量导入配置")
        if parts == ["mcps", "new"]:
            form = self._form()
            raw = self._mcp_raw(form)
            try:
                v = validate_mcp(raw)
            except ValueError as e:
                return self._html(ui.mcp_form({
                    "mcp": raw, "args_raw": _fget(form, "args"),
                    "env_raw": _fget(form, "env"), "headers_raw": _fget(form, "headers"),
                    "is_new": True, "err": str(e),
                }), 400)
            STORE.create_mcp(v)
            return self._flash("/mcps", msg="MCP 已创建")
        if len(parts) == 3 and parts[0] == "mcps" and parts[2] == "edit":
            form = self._form()
            raw = self._mcp_raw(form)
            old = STORE.get_mcp(parts[1])
            if not old:
                return self._html("<h1>MCP 不存在</h1>", 404)
            try:
                v = validate_mcp(raw, old, parts[1])
            except ValueError as e:
                merged = dict(old)
                merged.update(raw)
                return self._html(ui.mcp_form({
                    "mcp": merged, "args_raw": _fget(form, "args"),
                    "env_raw": _fget(form, "env"), "headers_raw": _fget(form, "headers"),
                    "is_new": False, "err": str(e),
                }), 400)
            STORE.update_mcp(parts[1], v)
            return self._flash("/mcps", msg="MCP 已保存")
        if len(parts) == 3 and parts[0] == "mcps" and parts[2] == "delete":
            if not STORE.delete_mcp(parts[1]):
                return self._flash("/mcps", err="内置 MCP 不可删除")
            return self._flash("/mcps", msg="MCP 已删除")
        if len(parts) == 3 and parts[0] == "mcps" and parts[2] == "probe":
            m = STORE.get_mcp(parts[1])
            if not m:
                return self._flash("/mcps", err="MCP 不存在")
            tools, err = mcp_runtime.probe_tools(m, EXECUTOR_ROOT, timeout=90)
            STORE.set_mcp_probe(parts[1], tools, err)
            if err:
                return self._flash(f"/mcps/{parts[1]}", err=f"探测失败: {err}")
            return self._flash(f"/mcps/{parts[1]}", msg=f"探测成功，发现 {len(tools)} 个工具")
        return self._html("<h1>404</h1>", 404)

    # ── PUT / DELETE（纯 API）───────────────────────────────────────
    def do_PUT(self):
        try:
            _, parts, _ = self._parts()
            if len(parts) == 3 and parts[:2] == ["api", "cases"]:
                old = STORE.get_case(parts[2])
                if not old:
                    return self._json({"ok": False, "error": "用例不存在"}, 404)
                v = validate_case(self._json_body(), old)
                return self._json({"ok": True, "case": STORE.update_case(parts[2], v)})
            if len(parts) == 3 and parts[:2] == ["api", "mcps"]:
                old = STORE.get_mcp(parts[2])
                if not old:
                    return self._json({"ok": False, "error": "MCP 不存在"}, 404)
                v = validate_mcp(self._json_body(), old, parts[2])
                return self._json({"ok": True, "mcp": STORE.update_mcp(parts[2], v)})
            if len(parts) == 4 and parts[:3] == ["api", "agents", "configs"]:
                old = STORE.get_agent_config(parts[3])
                if not old:
                    return self._json({"ok": False, "error": "执行节点不存在"}, 404)
                v = validate_agent(self._json_body(), old, parts[3])
                return self._json({"ok": True, "config": STORE.update_agent_config(parts[3], v)})
            if len(parts) == 3 and parts[:2] == ["api", "env-profiles"]:
                old = STORE.get_env_profile(parts[2])
                if not old:
                    return self._json({"ok": False, "error": "Profile 不存在"}, 404)
                v = validate_env_profile(self._json_body(), old)
                return self._json({"ok": True, "profile": STORE.update_env_profile(parts[2], v)})
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            self._api_exc(e)

    def do_DELETE(self):
        try:
            _, parts, _ = self._parts()
            if len(parts) == 3 and parts[:2] == ["api", "cases"]:
                return self._json({"ok": STORE.delete_case(parts[2])})
            if len(parts) == 3 and parts[:2] == ["api", "mcps"]:
                return self._json({"ok": STORE.delete_mcp(parts[2])})
            if len(parts) == 4 and parts[:3] == ["api", "agents", "configs"]:
                AM.stop(parts[3])
                return self._json({"ok": STORE.delete_agent_config(parts[3])})
            if len(parts) == 3 and parts[:2] == ["api", "env-profiles"]:
                p = STORE.get_env_profile(parts[2])
                if not p:
                    return self._json({"ok": False, "error": "Profile 不存在"}, 404)
                if p.get("status") == "capturing":
                    return self._json({"ok": False, "error": "Profile 正在采集中，不能删除"}, 400)
                used = STORE.cases_using_profile(parts[2])
                if used:
                    names = "、".join(x["name"] for x in used[:5])
                    return self._json({"ok": False, "error": f"Profile 仍被用例使用: {names}"}, 400)
                return self._json({"ok": STORE.delete_env_profile(parts[2])})
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            self._api_exc(e, 500)

    # ── 表单 raw ──────────────────────────────────────────────────
    @staticmethod
    def _case_raw(form):
        return {
            "name": _fget(form, "name"), "description": _fget(form, "description"),
            "tags": _fget(form, "tags"), "mcp_id": _fget(form, "mcp_id"),
            "timeout_s": _fget(form, "timeout_s"), "steps": _fget(form, "steps"),
            "mode": _fget(form, "mode", "scripted"),
            "instruction": _fget(form, "instruction"),
            "expected": _fget(form, "expected"),
            "max_steps": _fget(form, "max_steps", limits.AI_DEFAULT_MAX_STEPS),
            "navigation_hint": _fget(form, "navigation_hint"),
            "max_retry": _fget(form, "max_retry", -1),
            "side_effect": "side_effect" in form,
            "kind": _fget(form, "kind", "test"),
            "profile_id": _fget(form, "profile_id"),
            "enabled": "enabled" in form,
        }

    @staticmethod
    def _profile_raw(form):
        return {
            "name": _fget(form, "name"),
            "description": _fget(form, "description"),
            "setup_case_id": _fget(form, "setup_case_id"),
        }

    @staticmethod
    def _agent_raw(form):
        return {
            "id": _fget(form, "id"), "name": _fget(form, "name"), "kind": _fget(form, "kind"),
            "host": _fget(form, "host"), "port": _fget(form, "port"), "slots": _fget(form, "slots"),
            "tags": _fget(form, "tags"), "command": _fget(form, "command"),
            "cwd": _fget(form, "cwd"), "env": _fget(form, "env"),
            "autostart": "autostart" in form, "enabled": "enabled" in form,
        }

    @staticmethod
    def _mcp_raw(form):
        return {
            "id": _fget(form, "id"), "name": _fget(form, "name"),
            "transport": _fget(form, "transport"), "command": _fget(form, "command"),
            "args": _fget(form, "args"), "env": _fget(form, "env"),
            "url": _fget(form, "url"), "headers": _fget(form, "headers"),
            "enabled": "enabled" in form,
        }

    def _server_error(self, e):
        print("[platform] 请求异常:", type(e).__name__, e, file=sys.stderr, flush=True)
        try:
            self._html(f"<h1>500</h1><pre>{ui.esc(mcp_runtime.describe_exc(e))}</pre>", 500)
        except Exception:
            pass


def _fget(form, key, default=""):
    v = form.get(key)
    if isinstance(v, list):
        return v[-1] if v else default
    return default if v is None else v


def _q1(q, key, default=""):
    v = q.get(key)
    return (v[-1] if v else default) if isinstance(v, list) else (v or default)


# ══════════════════════ 启动 ══════════════════════
def main():
    global STORE, P, AM, PLATFORM_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--max-retry", type=int, default=2)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--no-autostart", action="store_true", help="不自动拉起 autostart 的本地执行节点")
    ap.add_argument("--node-policies")
    ap.add_argument("--gateway-host", default="127.0.0.1")
    ap.add_argument("--gateway-port", type=int)
    ap.add_argument("--gateway-cert")
    ap.add_argument("--gateway-key")
    ap.add_argument("--allow-insecure-gateway-http", action="store_true")
    a = ap.parse_args()
    if bool(a.node_policies) != (a.gateway_port is not None):
        ap.error("--node-policies 与 --gateway-port 必须一起指定")

    STORE = Store(a.db)
    stale = STORE.recover_stale_runs()
    P = Platform(STORE, a.max_retry)
    gateway = None
    if a.node_policies:
        from aiui.platform.remote import RemoteCoordinator, load_policies, make_gateway
        try:
            coordinator = RemoteCoordinator(P, load_policies(a.node_policies),
                                            policy_path=a.node_policies)
            gateway = make_gateway(coordinator, a.gateway_host, a.gateway_port,
                                   a.gateway_cert, a.gateway_key, a.allow_insecure_gateway_http)
        except (ValueError, OSError):
            STORE.close()
            ap.error("执行网关配置或监听失败，请检查策略、证书及端口")
    bind_host = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    PLATFORM_URL = f"http://{bind_host}:{a.port}"
    AM = AgentManager(STORE, P, LOG_DIR, PLATFORM_URL)
    threading.Thread(target=P.dispatch_loop, daemon=True).start()

    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
        srv.daemon_threads = True          # 避免探测 MCP 等长请求阻塞 Ctrl+C 收尾
    except OSError as e:
        print(f"[platform] 无法监听 {a.host}:{a.port}: {e}", file=sys.stderr, flush=True)
        P.stop.set()
        if gateway:
            gateway.server_close()
        STORE.close()
        sys.exit(1)

    if gateway:
        threading.Thread(target=gateway.serve_forever, daemon=True).start()

    if not a.no_autostart:
        threading.Thread(target=AM.autostart, daemon=True).start()

    print(f"[platform] 控制面已启动 {PLATFORM_URL}/  （看板；DB={a.db}；历史恢复 {stale} 条中断任务）",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        P.stop.set()
        AM.stop_all()
        srv.server_close()
        if gateway:
            gateway.shutdown()
            gateway.server_close()
        STORE.close()


if __name__ == "__main__":
    main()
