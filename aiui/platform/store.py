# -*- coding: utf-8 -*-
"""SQLite 存储层：用例库 / 执行机配置 / MCP 配置 / 运行记录 / 执行日志。

设计取舍：
  · 用标准库 sqlite3，不引入 ORM。单文件、可随 demo 一起拷走。
  · 一份连接 + RLock：平台是多线程 http.server，写操作串行化最省心。
  · 运行中的调度状态仍以内存为准，DB 只做持久化快照；进程重启后历史可查，
    但不会自动恢复未完成的队列（demo 边界，见 README）。
"""
from contextlib import contextmanager

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from aiui.common import budget, limits, profiles
from aiui.common.paths import PROFILES_DIR, RUNTIME_DIR, ensure_runtime_dirs

DEFAULT_DB = RUNTIME_DIR / "platform.db"


def now():
    return round(time.time(), 3)


def _loads(text, default):
    try:
        v = json.loads(text) if text else default
        return v
    except Exception:
        return default


def _dumps(v):
    return json.dumps(v, ensure_ascii=False)


class Store:
    def __init__(self, path=DEFAULT_DB):
        ensure_runtime_dirs()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._transaction_depth = 0
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._create()
        self._seed_mcps()

    def _commit(self):
        if not self._transaction_depth:
            self.conn.commit()

    @contextmanager
    def transaction(self):
        """同连接复用现有存储方法；最外层统一提交/回滚。"""
        with self.lock:
            outer = not self._transaction_depth
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._transaction_depth += 1
            try:
                yield
                if outer:
                    self.conn.commit()
            except BaseException:
                if outer:
                    self.conn.rollback()
                raise
            finally:
                self._transaction_depth -= 1

    # ── schema ─────────────────────────────────────────────────────
    def _create(self):
        with self.lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_nodes (
                    node_id TEXT PRIMARY KEY, data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS remote_assignments (
                    dispatch_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE,
                    node_id TEXT NOT NULL, instance_id TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS remote_events (
                    node_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    dispatch_id TEXT NOT NULL, task_id TEXT NOT NULL, type TEXT NOT NULL,
                    PRIMARY KEY (node_id,event_id)
                );
                CREATE TABLE IF NOT EXISTS cases (
                    case_id     TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    tags        TEXT NOT NULL DEFAULT '',
                    mcp_id      TEXT NOT NULL DEFAULT '',
                    timeout_s   REAL NOT NULL DEFAULT 60,
                    steps_json  TEXT NOT NULL,
                    mode        TEXT NOT NULL DEFAULT 'scripted',
                    instruction TEXT NOT NULL DEFAULT '',
                    expected    TEXT NOT NULL DEFAULT '',
                    max_steps   INTEGER NOT NULL DEFAULT 12,
                    kind        TEXT NOT NULL DEFAULT 'test',
                    profile_id  TEXT NOT NULL DEFAULT '',
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    created     REAL NOT NULL,
                    updated     REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_configs (
                    id           TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    kind         TEXT NOT NULL DEFAULT 'builtin',
                    host         TEXT NOT NULL DEFAULT '127.0.0.1',
                    port         INTEGER NOT NULL DEFAULT 8001,
                    slots        INTEGER NOT NULL DEFAULT 1,
                    tags         TEXT NOT NULL DEFAULT '',
                    command_json TEXT NOT NULL DEFAULT '[]',
                    cwd          TEXT NOT NULL DEFAULT '',
                    env_json     TEXT NOT NULL DEFAULT '{}',
                    autostart    INTEGER NOT NULL DEFAULT 0,
                    enabled      INTEGER NOT NULL DEFAULT 1,
                    created      REAL NOT NULL,
                    updated      REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mcps (
                    id           TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    transport    TEXT NOT NULL DEFAULT 'stdio',
                    command      TEXT NOT NULL DEFAULT '',
                    args_json    TEXT NOT NULL DEFAULT '[]',
                    env_json     TEXT NOT NULL DEFAULT '{}',
                    url          TEXT NOT NULL DEFAULT '',
                    headers_json TEXT NOT NULL DEFAULT '{}',
                    builtin      INTEGER NOT NULL DEFAULT 0,
                    enabled      INTEGER NOT NULL DEFAULT 1,
                    tools_json   TEXT NOT NULL DEFAULT '[]',
                    last_probe   REAL,
                    last_error   TEXT NOT NULL DEFAULT '',
                    created      REAL NOT NULL,
                    updated      REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    task_id     TEXT PRIMARY KEY,
                    case_id     TEXT NOT NULL DEFAULT '',
                    name        TEXT NOT NULL DEFAULT '',
                    mcp_id      TEXT NOT NULL DEFAULT '',
                    state       TEXT NOT NULL DEFAULT 'queued',
                    attempt     INTEGER NOT NULL DEFAULT 0,
                    max_retry   INTEGER NOT NULL DEFAULT 2,
                    agent_id    TEXT,
                    created     REAL NOT NULL,
                    started     REAL,
                    finished    REAL,
                    error       TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    case_json   TEXT NOT NULL DEFAULT '{}',
                    mcp_json    TEXT NOT NULL DEFAULT '{}',
                    kind        TEXT NOT NULL DEFAULT 'test',
                    profile_id  TEXT NOT NULL DEFAULT '',
                    profile_hash TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);

                CREATE TABLE IF NOT EXISTS env_profiles (
                    profile_id          TEXT PRIMARY KEY,
                    name                TEXT NOT NULL,
                    description         TEXT NOT NULL DEFAULT '',
                    setup_case_id       TEXT NOT NULL DEFAULT '',
                    source_mcp_id       TEXT NOT NULL DEFAULT '',
                    status              TEXT NOT NULL DEFAULT 'empty',
                    last_error          TEXT NOT NULL DEFAULT '',
                    current_hash        TEXT NOT NULL DEFAULT '',
                    version             INTEGER NOT NULL DEFAULT 0,
                    last_capture        REAL,
                    last_capture_run_id TEXT NOT NULL DEFAULT '',
                    last_used           REAL,
                    capture_started     REAL,
                    created             REAL NOT NULL,
                    updated             REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS env_profile_versions (
                    profile_id     TEXT NOT NULL,
                    version        INTEGER NOT NULL,
                    hash           TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    size_bytes     INTEGER NOT NULL,
                    summary_json   TEXT NOT NULL DEFAULT '{}',
                    captured_at    REAL NOT NULL,
                    capture_run_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (profile_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_profile_versions
                    ON env_profile_versions(profile_id, version DESC);

                CREATE TABLE IF NOT EXISTS settings (
                    key        TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    updated    REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_logs (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    ts      REAL NOT NULL,
                    level   TEXT NOT NULL DEFAULT 'info',
                    source  TEXT NOT NULL DEFAULT 'platform',
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_run_logs_task ON run_logs(task_id, id);
                """
            )
            self._ensure_case_columns()
            self._ensure_run_columns()
            self._commit()

    def _ensure_case_columns(self):
        """兼容旧库：给 cases 补 AI 模式 / 环境 Profile 字段。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(cases)")}
        additions = {
            "mode": "TEXT NOT NULL DEFAULT 'scripted'",
            "instruction": "TEXT NOT NULL DEFAULT ''",
            "expected": "TEXT NOT NULL DEFAULT ''",
            "max_steps": "INTEGER NOT NULL DEFAULT 12",
            "kind": "TEXT NOT NULL DEFAULT 'test'",
            "profile_id": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in cols:
                self.conn.execute(f"ALTER TABLE cases ADD COLUMN {name} {ddl}")

    def _ensure_run_columns(self):
        """兼容旧库：给 runs 补环境 Profile 快照字段。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(runs)")}
        additions = {
            "kind": "TEXT NOT NULL DEFAULT 'test'",
            "profile_id": "TEXT NOT NULL DEFAULT ''",
            "profile_hash": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in cols:
                self.conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {ddl}")

    def _seed_mcps(self):
        """内置 MCP 示例。INSERT OR IGNORE：用户改过的不覆盖。"""
        seeds = [
            {
                "id": "builtin-browser",
                "name": "内置浏览器 MCP",
                "transport": "stdio",
                "command": "{python}",
                "args": ["-m", "aiui.toolservers.builtin_browser"],
                "env": {"PYTHONIOENCODING": "utf-8"},
                "builtin": 1,
            },
            {
                "id": "playwright-mcp",
                "name": "Playwright MCP（官方 @playwright/mcp）",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@playwright/mcp@latest", "--headless"],
                "env": {"PYTHONIOENCODING": "utf-8"},
                "builtin": 0,
            },
            {
                "id": "playwright-mcp-ea",
                "name": "Playwright MCP（ExecuteAutomation，本机缓存）",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@executeautomation/playwright-mcp-server"],
                "env": {"PYTHONIOENCODING": "utf-8"},
                "builtin": 0,
            },
        ]
        with self.lock:
            for s in seeds:
                t = now()
                self.conn.execute(
                    """INSERT OR IGNORE INTO mcps
                       (id,name,transport,command,args_json,env_json,url,headers_json,
                        builtin,enabled,tools_json,last_error,created,updated)
                       VALUES (?,?,?,?,?,?,'','',?,1,'[]','',?,?)""",
                    (s["id"], s["name"], s["transport"], s["command"],
                     _dumps(s["args"]), _dumps(s["env"]), s["builtin"], t, t),
                )
            self._commit()

    # ── 用例 ───────────────────────────────────────────────────────
    @staticmethod
    def _case_row(r):
        d = dict(r)
        d["steps"] = _loads(d.pop("steps_json", "[]"), [])
        d["enabled"] = bool(d["enabled"])
        d["mode"] = d.get("mode") or "scripted"
        d["instruction"] = d.get("instruction") or ""
        d["expected"] = d.get("expected") or ""
        d["max_steps"] = int(d.get("max_steps") or limits.AI_DEFAULT_MAX_STEPS)
        d["kind"] = d.get("kind") or "test"
        d["profile_id"] = d.get("profile_id") or ""
        d["step_count"] = len(d["steps"])
        return d

    def list_cases(self, q="", kind=""):
        clauses, params = [], []
        if q:
            like = f"%{q}%"
            clauses.append("(name LIKE ? OR description LIKE ? OR tags LIKE ? OR case_id LIKE ?"
                           " OR instruction LIKE ? OR expected LIKE ?)")
            params.extend([like] * 6)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        sql = "SELECT * FROM cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated DESC"
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._case_row(r) for r in rows]

    def get_case(self, case_id):
        with self.lock:
            r = self.conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return self._case_row(r) if r else None

    def create_case(self, data):
        t = now()
        cid = data.get("case_id") or f"C-{uuid.uuid4().hex[:8]}"
        with self.lock:
            self.conn.execute(
                """INSERT INTO cases
                   (case_id,name,description,tags,mcp_id,timeout_s,steps_json,
                    mode,instruction,expected,max_steps,kind,profile_id,
                    enabled,created,updated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, data["name"], data.get("description", ""), data.get("tags", ""),
                 data.get("mcp_id", ""), float(data.get("timeout_s", 60)),
                 _dumps(data.get("steps", [])), data.get("mode", "scripted"),
                 data.get("instruction", ""), data.get("expected", ""),
                 int(data.get("max_steps", limits.AI_DEFAULT_MAX_STEPS)),
                 data.get("kind", "test"),
                 data.get("profile_id", ""),
                 1 if data.get("enabled", True) else 0, t, t),
            )
            self._commit()
        return self.get_case(cid)

    def update_case(self, case_id, data):
        cur = self.get_case(case_id)
        if not cur:
            return None
        merged = {
            "name": data.get("name", cur["name"]),
            "description": data.get("description", cur["description"]),
            "tags": data.get("tags", cur["tags"]),
            "mcp_id": data.get("mcp_id", cur["mcp_id"]),
            "timeout_s": float(data.get("timeout_s", cur["timeout_s"])),
            "steps": data.get("steps", cur["steps"]),
            "mode": data.get("mode", cur.get("mode", "scripted")),
            "instruction": data.get("instruction", cur.get("instruction", "")),
            "expected": data.get("expected", cur.get("expected", "")),
            "max_steps": int(data.get("max_steps",
                                       cur.get("max_steps", limits.AI_DEFAULT_MAX_STEPS))),
            "kind": data.get("kind", cur.get("kind", "test")),
            "profile_id": data.get("profile_id", cur.get("profile_id", "")),
            "enabled": data.get("enabled", cur["enabled"]),
        }
        with self.lock:
            self.conn.execute(
                """UPDATE cases SET name=?,description=?,tags=?,mcp_id=?,timeout_s=?,
                   steps_json=?,mode=?,instruction=?,expected=?,max_steps=?,
                   kind=?,profile_id=?,enabled=?,updated=? WHERE case_id=?""",
                (merged["name"], merged["description"], merged["tags"], merged["mcp_id"],
                 merged["timeout_s"], _dumps(merged["steps"]), merged["mode"],
                 merged["instruction"], merged["expected"], merged["max_steps"],
                 merged["kind"], merged["profile_id"],
                 1 if merged["enabled"] else 0, now(), case_id),
            )
            self._commit()
        return self.get_case(case_id)

    def delete_case(self, case_id):
        with self.lock:
            cur = self.conn.execute("DELETE FROM cases WHERE case_id=?", (case_id,))
            self._commit()
        return cur.rowcount > 0

    # ── 执行环境 Profile ───────────────────────────────────────────
    @staticmethod
    def _profile_row(r):
        d = dict(r)
        d["version"] = int(d.get("version") or 0)
        return d

    @staticmethod
    def _profile_summary(conn, profile_id):
        row = conn.execute(
            "SELECT summary_json FROM env_profile_versions "
            "WHERE profile_id=? ORDER BY version DESC LIMIT 1", (profile_id,)).fetchone()
        return _loads(row["summary_json"], {}) if row else {}

    def list_env_profiles(self, q=""):
        sql = "SELECT * FROM env_profiles"
        params = []
        if q:
            like = f"%{q}%"
            sql += " WHERE name LIKE ? OR description LIKE ? OR profile_id LIKE ?"
            params.extend([like, like, like])
        sql += " ORDER BY updated DESC"
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
            out = []
            for row in rows:
                d = self._profile_row(row)
                d["summary"] = self._profile_summary(self.conn, d["profile_id"])
                out.append(d)
            return out

    def get_env_profile(self, profile_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM env_profiles WHERE profile_id=?", (profile_id,)).fetchone()
            if not row:
                return None
            d = self._profile_row(row)
            d["summary"] = self._profile_summary(self.conn, profile_id)
            return d

    def create_env_profile(self, data):
        pid = data.get("profile_id") or f"P-{uuid.uuid4().hex[:8]}"
        t = now()
        with self.lock:
            self.conn.execute(
                """INSERT INTO env_profiles
                   (profile_id,name,description,setup_case_id,source_mcp_id,status,
                    last_error,current_hash,version,last_capture,last_capture_run_id,
                    last_used,capture_started,created,updated)
                   VALUES (?,?,?,?,?,'empty','','',0,NULL,'',NULL,NULL,?,?)""",
                (pid, data["name"], data.get("description", ""),
                 data.get("setup_case_id", ""), data.get("source_mcp_id", ""), t, t),
            )
            self._commit()
        return self.get_env_profile(pid)

    def update_env_profile(self, profile_id, data):
        cur = self.get_env_profile(profile_id)
        if not cur:
            return None
        merged = {
            "name": data.get("name", cur["name"]),
            "description": data.get("description", cur["description"]),
            "setup_case_id": data.get("setup_case_id", cur["setup_case_id"]),
            "source_mcp_id": data.get("source_mcp_id", cur["source_mcp_id"]),
        }
        with self.lock:
            self.conn.execute(
                """UPDATE env_profiles SET name=?,description=?,setup_case_id=?,
                   source_mcp_id=?,updated=? WHERE profile_id=?""",
                (merged["name"], merged["description"], merged["setup_case_id"],
                 merged["source_mcp_id"], now(), profile_id),
            )
            self._commit()
        return self.get_env_profile(profile_id)

    def delete_env_profile(self, profile_id):
        with self.lock:
            cur = self.conn.execute(
                "DELETE FROM env_profiles WHERE profile_id=?", (profile_id,))
            self.conn.execute(
                "DELETE FROM env_profile_versions WHERE profile_id=?", (profile_id,))
            self._commit()
        shutil.rmtree(PROFILES_DIR / profile_id, ignore_errors=True)
        return cur.rowcount > 0

    def cases_using_profile(self, profile_id):
        with self.lock:
            rows = self.conn.execute(
                "SELECT case_id,name FROM cases WHERE profile_id=? ORDER BY name",
                (profile_id,)).fetchall()
        return [dict(r) for r in rows]

    def begin_profile_capture(self, profile_id, stale_seconds=300):
        """占用采集锁；返回 (ok, error)。"""
        with self.lock:
            row = self.conn.execute(
                "SELECT status,capture_started FROM env_profiles WHERE profile_id=?",
                (profile_id,)).fetchone()
            if not row:
                return False, "Profile 不存在"
            started = row["capture_started"] or 0
            if row["status"] == "capturing" and now() - started < stale_seconds:
                return False, "该 Profile 已有采集任务进行中"
            self.conn.execute(
                """UPDATE env_profiles SET status='capturing', capture_started=?,
                   last_error='', updated=? WHERE profile_id=?""",
                (now(), now(), profile_id),
            )
            self._commit()
        return True, ""

    def finish_profile_capture(self, profile_id, state, run_id="", captured_at=None,
                               source_mcp_id=""):
        """保存一次采集结果；相同 hash 不重复建版本。返回版本信息。"""
        state, summary = profiles.validate_state(state)
        digest = profiles.state_hash(state)
        captured_at = float(captured_at or now())
        with self.lock:
            row = self.conn.execute(
                "SELECT current_hash,version FROM env_profiles WHERE profile_id=?",
                (profile_id,)).fetchone()
            if not row:
                raise ValueError("Profile 不存在")
            current_hash = row["current_hash"] or ""
            version = int(row["version"] or 0)
            changed = digest != current_hash
            if changed:
                version += 1
            directory = PROFILES_DIR / profile_id
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{digest}.json"
            if not path.exists():
                tmp = directory / f".{digest}.tmp"
                tmp.write_text(
                    json.dumps(state, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")),
                    encoding="utf-8",
                )
                os.replace(tmp, path)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            if changed:
                self.conn.execute(
                    """INSERT OR REPLACE INTO env_profile_versions
                       (profile_id,version,hash,schema_version,size_bytes,summary_json,
                        captured_at,capture_run_id)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (profile_id, version, digest, profiles.SCHEMA_VERSION,
                     summary["size_bytes"], _dumps(summary), captured_at, run_id),
                )
            self.conn.execute(
                """UPDATE env_profiles SET current_hash=?,version=?,status='ready',
                   last_error='',last_capture=?,last_capture_run_id=?,capture_started=NULL,
                   updated=? WHERE profile_id=?""",
                (digest, version, captured_at, run_id, now(), profile_id),
            )
            if source_mcp_id:
                self.conn.execute(
                    "UPDATE env_profiles SET source_mcp_id=? WHERE profile_id=?",
                    (source_mcp_id, profile_id))
            self._commit()
        return {"version": version, "hash": digest, "summary": summary,
                "changed": changed}

    def mark_profile_capture_failed(self, profile_id, error):
        with self.lock:
            self.conn.execute(
                """UPDATE env_profiles SET status='error',last_error=?,
                   capture_started=NULL,updated=? WHERE profile_id=?""",
                (str(error or "采集失败")[:1000], now(), profile_id),
            )
            self._commit()

    def touch_profile_used(self, profile_id):
        with self.lock:
            self.conn.execute(
                "UPDATE env_profiles SET last_used=? WHERE profile_id=?",
                (now(), profile_id),
            )
            self._commit()

    def get_profile_state(self, profile_id):
        """读取当前版本状态；返回 (profile, state)。原文仅供执行注入。"""
        profile = self.get_env_profile(profile_id)
        if not profile:
            raise ValueError("Profile 不存在")
        digest = profile.get("current_hash") or ""
        if not digest:
            raise ValueError("Profile 还没有可用的采集结果")
        path = PROFILES_DIR / profile_id / f"{digest}.json"
        if not path.exists():
            raise ValueError("Profile 状态文件缺失，请重新采集")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"Profile 状态文件损坏: {e}")
        state, _ = profiles.validate_state(state)
        if profiles.state_hash(state) != digest:
            raise ValueError("Profile 状态文件 hash 不匹配，请重新采集")
        return profile, state

    def list_profile_versions(self, profile_id):
        with self.lock:
            rows = self.conn.execute(
                """SELECT * FROM env_profile_versions WHERE profile_id=?
                   ORDER BY version DESC""", (profile_id,)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["summary"] = _loads(d.pop("summary_json", "{}"), {})
            out.append(d)
        return out

    # ── 应用设置 / AI 预算 ─────────────────────────────────────────
    def get_setting(self, key, default=None):
        with self.lock:
            row = self.conn.execute(
                "SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        return _loads(row["value_json"], default)

    def save_setting(self, key, value):
        with self.lock:
            self.conn.execute(
                """INSERT INTO settings (key,value_json,updated) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                     updated=excluded.updated""",
                (key, _dumps(value), now()),
            )
            self._commit()
        return value

    def get_budget(self):
        raw = self.get_setting("ai_budget", None)
        try:
            return budget.normalize_budget(raw or budget.DEFAULT_BUDGET)
        except ValueError:
            return budget.normalize_budget(budget.DEFAULT_BUDGET)

    def save_budget(self, data):
        value = budget.normalize_budget(data)
        self.save_setting("ai_budget", value)
        return value

    # ── MCP 配置 ───────────────────────────────────────────────────
    @staticmethod
    def _mcp_row(r):
        d = dict(r)
        d["args"] = _loads(d.pop("args_json", "[]"), [])
        d["env"] = _loads(d.pop("env_json", "{}"), {})
        d["headers"] = _loads(d.pop("headers_json", "{}"), {})
        d["tools"] = _loads(d.pop("tools_json", "[]"), [])
        d["builtin"] = bool(d["builtin"])
        d["enabled"] = bool(d["enabled"])
        return d

    def list_mcps(self, enabled_only=False):
        sql = "SELECT * FROM mcps"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY builtin DESC, name"
        with self.lock:
            rows = self.conn.execute(sql).fetchall()
        return [self._mcp_row(r) for r in rows]

    def get_mcp(self, mcp_id):
        with self.lock:
            r = self.conn.execute("SELECT * FROM mcps WHERE id=?", (mcp_id,)).fetchone()
        return self._mcp_row(r) if r else None

    def default_mcp(self):
        m = self.get_mcp("builtin-browser")
        if m and m["enabled"]:
            return m
        for m in self.list_mcps(enabled_only=True):
            return m
        return None

    def create_mcp(self, data):
        mid = data.get("id") or f"mcp-{uuid.uuid4().hex[:8]}"
        t = now()
        with self.lock:
            self.conn.execute(
                """INSERT INTO mcps
                   (id,name,transport,command,args_json,env_json,url,headers_json,builtin,
                    enabled,tools_json,last_error,created,updated)
                   VALUES (?,?,?,?,?,?,?,?,?,?, '[]','',?,?)""",
                (mid, data["name"], data.get("transport", "stdio"),
                 data.get("command", ""), _dumps(data.get("args", [])),
                 _dumps(data.get("env", {})), data.get("url", ""),
                 _dumps(data.get("headers", {})), 1 if data.get("builtin") else 0,
                 1 if data.get("enabled", True) else 0, t, t),
            )
            self._commit()
        return self.get_mcp(mid)

    def update_mcp(self, mcp_id, data):
        cur = self.get_mcp(mcp_id)
        if not cur:
            return None
        merged = {
            "name": data.get("name", cur["name"]),
            "transport": data.get("transport", cur["transport"]),
            "command": data.get("command", cur["command"]),
            "args": data.get("args", cur["args"]),
            "env": data.get("env", cur["env"]),
            "url": data.get("url", cur["url"]),
            "headers": data.get("headers", cur["headers"]),
            "enabled": data.get("enabled", cur["enabled"]),
        }
        with self.lock:
            self.conn.execute(
                """UPDATE mcps SET name=?,transport=?,command=?,args_json=?,env_json=?,
                   url=?,headers_json=?,enabled=?,updated=? WHERE id=?""",
                (merged["name"], merged["transport"], merged["command"], _dumps(merged["args"]),
                 _dumps(merged["env"]), merged["url"], _dumps(merged["headers"]),
                 1 if merged["enabled"] else 0, now(), mcp_id),
            )
            self._commit()
        return self.get_mcp(mcp_id)

    def delete_mcp(self, mcp_id):
        m = self.get_mcp(mcp_id)
        if not m or m["builtin"]:
            return False
        with self.lock:
            self.conn.execute("DELETE FROM mcps WHERE id=?", (mcp_id,))
            self._commit()
        return True

    def set_mcp_probe(self, mcp_id, tools=None, error=""):
        with self.lock:
            self.conn.execute(
                "UPDATE mcps SET tools_json=?, last_probe=?, last_error=?, updated=? WHERE id=?",
                (_dumps(tools or []), now(), error, now(), mcp_id),
            )
            self._commit()
        return self.get_mcp(mcp_id)

    # ── Agent 配置 ─────────────────────────────────────────────────
    @staticmethod
    def _agent_row(r):
        d = dict(r)
        d["command"] = _loads(d.pop("command_json", "[]"), [])
        d["env"] = _loads(d.pop("env_json", "{}"), {})
        d["autostart"] = bool(d["autostart"])
        d["enabled"] = bool(d["enabled"])
        return d

    def list_agent_configs(self):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM agent_configs ORDER BY name").fetchall()
        return [self._agent_row(r) for r in rows]

    def get_agent_config(self, agent_id):
        with self.lock:
            r = self.conn.execute("SELECT * FROM agent_configs WHERE id=?", (agent_id,)).fetchone()
        return self._agent_row(r) if r else None

    def create_agent_config(self, data):
        t = now()
        with self.lock:
            self.conn.execute(
                """INSERT INTO agent_configs
                   (id,name,kind,host,port,slots,tags,command_json,cwd,env_json,
                    autostart,enabled,created,updated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (data["id"], data["name"], data.get("kind", "builtin"),
                 data.get("host", "127.0.0.1"), int(data.get("port", 8001)),
                 max(1, int(data.get("slots", 1))), data.get("tags", ""),
                 _dumps(data.get("command", [])), data.get("cwd", ""),
                 _dumps(data.get("env", {})), 1 if data.get("autostart") else 0,
                 1 if data.get("enabled", True) else 0, t, t),
            )
            self._commit()
        return self.get_agent_config(data["id"])

    def update_agent_config(self, agent_id, data):
        cur = self.get_agent_config(agent_id)
        if not cur:
            return None
        merged = {
            "name": data.get("name", cur["name"]),
            "kind": data.get("kind", cur["kind"]),
            "host": data.get("host", cur["host"]),
            "port": int(data.get("port", cur["port"])),
            "slots": max(1, int(data.get("slots", cur["slots"]))),
            "tags": data.get("tags", cur["tags"]),
            "command": data.get("command", cur["command"]),
            "cwd": data.get("cwd", cur["cwd"]),
            "env": data.get("env", cur["env"]),
            "autostart": data.get("autostart", cur["autostart"]),
            "enabled": data.get("enabled", cur["enabled"]),
        }
        with self.lock:
            self.conn.execute(
                """UPDATE agent_configs SET name=?,kind=?,host=?,port=?,slots=?,tags=?,
                   command_json=?,cwd=?,env_json=?,autostart=?,enabled=?,updated=? WHERE id=?""",
                (merged["name"], merged["kind"], merged["host"], merged["port"],
                 merged["slots"], merged["tags"], _dumps(merged["command"]),
                 merged["cwd"], _dumps(merged["env"]),
                 1 if merged["autostart"] else 0, 1 if merged["enabled"] else 0,
                 now(), agent_id),
            )
            self._commit()
        return self.get_agent_config(agent_id)

    def delete_agent_config(self, agent_id):
        with self.lock:
            cur = self.conn.execute("DELETE FROM agent_configs WHERE id=?", (agent_id,))
            self._commit()
        return cur.rowcount > 0

    # ── 运行记录 ───────────────────────────────────────────────────
    def save_run(self, run):
        with self.lock:
            self.conn.execute(
                """INSERT INTO runs
                   (task_id,case_id,name,mcp_id,state,attempt,max_retry,agent_id,
                    created,started,finished,error,result_json,case_json,mcp_json,
                    kind,profile_id,profile_hash)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(task_id) DO UPDATE SET
                     state=excluded.state, attempt=excluded.attempt, agent_id=excluded.agent_id,
                     started=excluded.started, finished=excluded.finished, error=excluded.error,
                     result_json=excluded.result_json, kind=excluded.kind,
                     profile_id=excluded.profile_id, profile_hash=excluded.profile_hash""",
                (run["task_id"], run.get("case_id", ""), run.get("name", ""),
                 run.get("mcp_id", ""), run.get("state", "queued"),
                 int(run.get("attempt", 0)), int(run.get("max_retry", 2)),
                 run.get("agent_id"), run.get("created", now()), run.get("started"),
                 run.get("finished"), (run.get("error") or "")[:1000],
                 _dumps(run.get("result") or {}),
                 _dumps(run.get("case") or {}), _dumps(run.get("mcp") or {}),
                 run.get("kind", "test"), run.get("profile_id", ""),
                 run.get("profile_hash", "")),
            )
            self._commit()

    @staticmethod
    def _run_row(r):
        d = dict(r)
        d["result"] = _loads(d.pop("result_json", "{}"), {})
        d["case"] = _loads(d.pop("case_json", "{}"), {})
        d["mcp"] = _loads(d.pop("mcp_json", "{}"), {})
        d["kind"] = d.get("kind") or "test"
        d["profile_id"] = d.get("profile_id") or ""
        d["profile_hash"] = d.get("profile_hash") or ""
        d["elapsed_ms"] = (d["result"] or {}).get("elapsed_ms")
        return d

    def get_run(self, task_id):
        with self.lock:
            r = self.conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()
            result = self._run_row(r) if r else None
            if result:
                assignment = self.conn.execute(
                    "SELECT node_id,instance_id,dispatch_id,snapshot_json FROM remote_assignments WHERE task_id=?",
                    (task_id,)).fetchone()
                if assignment:
                    result.update(machine_id=_loads(assignment['snapshot_json'], {}).get('machine_id', ''),
                                  node_id=assignment['node_id'], instance_id=assignment['instance_id'],
                                  dispatch_id=assignment['dispatch_id'], connection='pull',
                                  target_node_id=_loads(assignment['snapshot_json'], {}).get('target_node_id', ''))
            return result

    def list_runs(self, limit=100, state=""):
        sql = "SELECT * FROM runs"
        params = []
        if state:
            sql += " WHERE state=?"
            params.append(state)
        sql += " ORDER BY created DESC LIMIT ?"
        params.append(int(limit))
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._run_row(r) for r in rows]

    def add_logs(self, task_id, entries):
        if not entries:
            return
        rows = [
            (task_id, float(e.get("ts") or now()), str(e.get("level", "info"))[:16],
             str(e.get("source", "agent"))[:32], str(e.get("message", ""))[:8000])
            for e in entries
        ]
        with self.lock:
            self.conn.executemany(
                "INSERT INTO run_logs (task_id,ts,level,source,message) VALUES (?,?,?,?,?)",
                rows,
            )
            self._commit()

    def list_logs(self, task_id, after=0, limit=2000):
        with self.lock:
            rows = self.conn.execute(
                "SELECT id,ts,level,source,message FROM run_logs "
                "WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
                (task_id, int(after), int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_runs(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def delete_run(self, task_id):
        """删除运行记录和日志；内存态、截图与活动任务校验由 Platform 负责。"""
        with self.lock:
            cur = self.conn.execute("DELETE FROM runs WHERE task_id=?", (task_id,))
            self.conn.execute("DELETE FROM run_logs WHERE task_id=?", (task_id,))
            self._commit()
            return cur.rowcount > 0

    def recover_stale_runs(self):
        """平台重启后，内存队列已丢；把上次未完成的运行标记为失败，避免永远“执行中”。"""
        with self.lock:
            cur = self.conn.execute(
                """UPDATE runs SET state='failed', error='平台重启，运行中断', finished=?
                   WHERE state IN ('queued','running','retry','awaiting_result')
                   AND task_id NOT IN (SELECT task_id FROM remote_assignments WHERE completed=0)""",
                (now(),),
            )
            self.conn.execute(
                """UPDATE env_profiles SET status='error', last_error='平台重启，采集中断',
                   capture_started=NULL, updated=? WHERE status='capturing'
                   AND profile_id NOT IN (SELECT r.profile_id FROM runs r
                       JOIN remote_assignments a ON a.task_id=r.task_id WHERE a.completed=0)""",
                (now(),),
            )
            self._commit()
        return cur.rowcount

    def close(self):
        with self.lock:
            try:
                self.conn.close()
            except Exception:
                pass
