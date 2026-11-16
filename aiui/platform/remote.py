"""持久化远程领取协调器；执行网关与管理监听器隔离。"""
import copy
import hashlib
import hmac
import heapq
import ipaddress
import json
import ssl
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aiui.common import profiles
from aiui.common.node_protocol import (VERSION, MAX_BODY_BYTES, HEARTBEAT_INTERVAL_S,
                                       ID_RE, validate_envelope)
from aiui.common.paths import PROFILES_DIR


class ProtocolError(ValueError):
    """仅包含可以安全返回给节点的固定校验信息。"""


def load_policies(path_or_dict):
    data = (copy.deepcopy(path_or_dict) if isinstance(path_or_dict, dict)
            else json.loads(Path(path_or_dict).read_text(encoding='utf-8-sig')))
    if (not isinstance(data, dict) or type(data.get('version')) is not int
            or data['version'] != VERSION or not isinstance(data.get('nodes'), dict)):
        raise ProtocolError('节点策略格式或版本无效')
    for nid, policy in data['nodes'].items():
        if not isinstance(nid, str) or not ID_RE.fullmatch(nid) or not isinstance(policy, dict):
            raise ProtocolError('节点策略 ID 无效')
        for key in ('name', 'machine_id'):
            if not isinstance(policy.get(key), str) or not policy[key].strip():
                raise ProtocolError('策略缺少节点元数据')
        digest = policy.get('token_sha256')
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in '0123456789abcdefABCDEF' for c in digest)):
            raise ProtocolError('策略凭证摘要无效')
        policy['token_sha256'] = digest.lower()
        policy.setdefault('allowed_ips', ['127.0.0.1/32', '::1/128'])
        if not isinstance(policy['allowed_ips'], list) or not policy['allowed_ips']:
            raise ProtocolError('策略来源白名单无效')
        for cidr in policy['allowed_ips']:
            ipaddress.ip_network(cidr, strict=False)
        if (not isinstance(policy.get('mcp_ids'), list)
                or not all(isinstance(x, str) and x for x in policy['mcp_ids'])):
            raise ProtocolError('策略 MCP 列表无效')
        if type(policy.get('max_slots')) is not int or not 1 <= policy['max_slots'] <= 128:
            raise ProtocolError('策略容量无效')
    return data


class RemoteCoordinator:
    def __init__(self, platform, policies):
        self.platform = platform
        self.store = platform.store
        self.policies = load_policies(policies)
        platform.remote = self
        self.recover()

    @contextmanager
    def _atomic(self):
        # 锁顺序始终 platform -> store；失败后同步恢复内存视图。
        with self.platform.lock:
            tasks = {}
            queue = None

            def remember(task_id, changes_queue=False):
                nonlocal queue
                if task_id not in tasks:
                    tasks[task_id] = copy.deepcopy(self.platform.tasks.get(task_id))
                if changes_queue and queue is None:
                    queue = list(self.platform.queue)

            try:
                with self.store.transaction():
                    yield remember
            except BaseException:
                for tid, task in tasks.items():
                    if task is None:
                        self.platform.tasks.pop(tid, None)
                    else:
                        self.platform.tasks[tid] = task
                if queue is not None:
                    self.platform.queue = queue
                raise

    def _policy(self, node_id, body):
        validate_envelope(node_id, body)
        policy = self.policies['nodes'].get(node_id)
        if policy is None:
            raise ProtocolError('节点未授权')
        return policy

    def _node(self, nid):
        r = self.store.conn.execute('SELECT data_json FROM remote_nodes WHERE node_id=?', (nid,)).fetchone()
        return json.loads(r[0]) if r else None

    def _save_node(self, n):
        self.store.conn.execute('INSERT OR REPLACE INTO remote_nodes VALUES (?,?)',
                                (n['node_id'], json.dumps(n)))

    def register(self, node_id, body):
        p = self._policy(node_id, body)
        if body.get('machine_id') != p['machine_id']:
            raise ProtocolError('节点机器不符合策略')
        if type(body.get('slots')) is not int or body['slots'] < 1:
            raise ProtocolError('节点容量无效')
        for k in ('mcp_ids', 'features'):
            if not isinstance(body.get(k), list) or not all(isinstance(v, str) for v in body[k]):
                raise ProtocolError('节点能力格式无效')
        with self._atomic():
            n = self._node(node_id)
            if (n and n['instance_id'] != body['instance_id'] and n['status'] == 'online'
                    and time.time() - n['last_hb'] <= 10):
                raise ProtocolError('另一节点实例仍在线')
            n = dict(node_id=node_id, instance_id=body['instance_id'], machine_id=p['machine_id'],
                     name=p['name'], os=str(body.get('os', '')), connection='pull',
                     slots=min(p['max_slots'], body['slots']),
                     mcp_ids=[x for x in p['mcp_ids'] if x in body['mcp_ids']],
                     features=body['features'], status='online', last_hb=time.time())
            if not body.get('dry_run'):
                self._save_node(n)
        return dict(ok=True, node_id=node_id, slots=n['slots'], mcp_ids=n['mcp_ids'],
                    heartbeat_interval_s=HEARTBEAT_INTERVAL_S)

    def _active(self, nid, body):
        policy = self._policy(nid, body)
        n = self._node(nid)
        if not n or n['instance_id'] != body['instance_id']:
            raise ProtocolError('实例未注册或已被替换')
        if n['machine_id'] != policy['machine_id']:
            raise ProtocolError('节点机器授权已变更，请重新注册')
        # 持久注册不是永久授权；每次活动都服从当前策略。
        n['mcp_ids'] = [mid for mid in n['mcp_ids'] if mid in policy['mcp_ids']]
        n['slots'] = min(n['slots'], policy['max_slots'])
        n['name'] = policy['name']
        ids = body.get('active_dispatch_ids', [])
        if not isinstance(ids, list) or not all(isinstance(v, str) for v in ids):
            raise ProtocolError('活动分配列表无效')
        for did in ids:
            self.store.conn.execute('''UPDATE remote_assignments SET acknowledged=1
                WHERE node_id=? AND instance_id=? AND dispatch_id=? AND completed=0''',
                (nid, body['instance_id'], did))
        n.update(status='online', last_hb=time.time())
        self._save_node(n)
        return n

    def heartbeat(self, node_id, body):
        with self._atomic():
            self._active(node_id, body)
        return {'ok': True}

    def _payload(self, t):
        payload = {k: t.get(k) for k in ('task_id', 'dispatch_id', 'instance_id', 'case', 'mcp',
                                          'budget', 'profile_capture')}
        ref = t.get('profile_ref')
        payload['profile'] = None
        if ref:
            # 只加载领取时冻结的不可变 hash；绝不能使用 current_hash。
            state = json.loads((PROFILES_DIR / ref['profile_id'] / (ref['hash'] + '.json')).read_text(encoding='utf-8'))
            state, _ = profiles.validate_state(state)
            if profiles.state_hash(state) != ref['hash']:
                raise ProtocolError('Profile 快照校验失败')
            payload['profile'] = {**ref, 'state': state}
        return payload

    def claim(self, node_id, body):
        with self._atomic() as remember:
            n = self._active(node_id, body)
            rows = self.store.conn.execute('SELECT * FROM remote_assignments WHERE node_id=? AND completed=0', (node_id,)).fetchall()
            for row in rows:
                if row['instance_id'] == body['instance_id'] and not row['acknowledged']:
                    return {'ok': True, 'task': self._payload(json.loads(row['snapshot_json']))}
            if len(rows) >= n['slots']:
                return {'ok': True, 'task': None}
            for item in sorted(self.platform.queue):
                t = self.platform.tasks.get(item[2])
                if not t or t['state'] not in ('queued', 'retry'):
                    continue
                if t.get('target_node_id') and t['target_node_id'] != node_id:
                    continue
                if t['mcp_id'] not in n['mcp_ids']:
                    continue
                if not set(t.get('require_features') or []).issubset(n['features']):
                    continue
                remember(t['task_id'], changes_queue=True)
                t.update(state='running', agent=node_id, node_id=node_id, machine_id=n['machine_id'],
                         instance_id=body['instance_id'], dispatch_id=uuid.uuid4().hex,
                         started=t.get('started') or time.time(), connection='pull')
                snap = copy.deepcopy(t)
                profile = snap.pop('profile', None)
                if profile:
                    snap['profile_ref'] = {k: profile[k] for k in ('profile_id', 'version', 'hash')}
                self.store.conn.execute('''INSERT INTO remote_assignments
                    (dispatch_id,task_id,node_id,instance_id,snapshot_json) VALUES (?,?,?,?,?)''',
                    (t['dispatch_id'], t['task_id'], node_id, body['instance_id'], json.dumps(snap)))
                self.platform._save(t)
                payload = self._payload(snap)
                self.platform.queue.remove(item)
                heapq.heapify(self.platform.queue)
                return {'ok': True, 'task': payload}
        return {'ok': True, 'task': None}

    def event(self, node_id, body):
        self._policy(node_id, body)
        eid = body.get('event_id')
        kind = body.get('type')
        if not isinstance(eid, str) or not ID_RE.fullmatch(eid) or kind not in ('logs', 'result'):
            raise ProtocolError('事件类型或 ID 无效')
        payload = body.get('payload')
        if not isinstance(payload, dict):
            raise ProtocolError('事件 payload 必须是对象')
        with self._atomic() as remember:
            row = self.store.conn.execute('SELECT * FROM remote_assignments WHERE dispatch_id=?',
                                          (body.get('dispatch_id'),)).fetchone()
            if (not row or row['task_id'] != body.get('task_id') or row['node_id'] != node_id
                    or row['instance_id'] != body['instance_id']):
                raise ProtocolError('未知任务或分配归属不匹配')
            old = self.store.conn.execute('SELECT * FROM remote_events WHERE node_id=? AND event_id=?', (node_id, eid)).fetchone()
            if old:
                if any(old[k] != body[k] for k in ('task_id', 'dispatch_id', 'type')):
                    raise ProtocolError('事件 ID 冲突')
                return {'ok': True, 'status': 'duplicate'}
            if row['completed']:
                raise ProtocolError('执行批次已完成')
            if kind == 'logs':
                entries = payload.get('logs')
                if not isinstance(entries, list) or not all(isinstance(x, dict) for x in entries):
                    raise ProtocolError('日志格式无效')
                self.store.add_logs(row['task_id'], entries)
            else:
                if type(payload.get('ok')) is not bool:
                    raise ProtocolError('结果缺少布尔 ok 字段')
                if 'steps' in payload and (not isinstance(payload['steps'], list)
                        or not all(isinstance(step, dict) for step in payload['steps'])):
                    raise ProtocolError('结果 steps 格式无效')
                remember(row['task_id'])
                result = copy.deepcopy(payload)
                result.update(task_id=row['task_id'], agent_id=node_id,
                              dispatch_id=row['dispatch_id'], retryable=False)
                resp = self.platform.on_result(result, remote_verified=True)
                if not resp.get('ok') or resp.get('ignored'):
                    raise ProtocolError('结果未被接收')
                self.store.conn.execute('UPDATE remote_assignments SET completed=1 WHERE dispatch_id=?', (row['dispatch_id'],))
            self.store.conn.execute('INSERT INTO remote_events VALUES (?,?,?,?,?)',
                (node_id, eid, row['dispatch_id'], row['task_id'], kind))
        return {'ok': True, 'status': 'received'}

    def recover(self):
        with self._atomic() as remember:
            for row in self.store.conn.execute('SELECT * FROM remote_assignments WHERE completed=0').fetchall():
                if row['task_id'] in self.platform.tasks:
                    continue
                remember(row['task_id'])
                t = json.loads(row['snapshot_json'])
                t.update(state='awaiting_result', timeline=[])
                self.platform.tasks[t['task_id']] = t
                self.platform._save(t)
            for row in self.store.conn.execute('SELECT node_id FROM remote_nodes').fetchall():
                n = self._node(row[0]); n['status'] = 'offline'; self._save_node(n)

    def mark_offline(self, node_id):
        with self._atomic() as remember:
            n = self._node(node_id)
            if n:
                n['status'] = 'offline'; self._save_node(n)
            for t in self.platform.tasks.values():
                if t.get('node_id') == node_id and t['state'] == 'running':
                    remember(t['task_id'])
                    t['state'] = 'awaiting_result'
                    self.platform._save(t)

    def tick(self):
        for n in self.list_nodes():
            if n['status'] == 'online' and time.time() - n['last_hb'] > 10:
                self.mark_offline(n['node_id'])

    def list_nodes(self):
        with self.platform.lock, self.store.lock:
            out = []
            for row in self.store.conn.execute('SELECT data_json FROM remote_nodes').fetchall():
                n = json.loads(row[0])
                n['running'] = self.store.conn.execute('SELECT count(*) FROM remote_assignments WHERE node_id=? AND completed=0', (n['node_id'],)).fetchone()[0]
                out.append(n)
            return out


def make_gateway(coordinator, host, port, cert_file=None, key_file=None, allow_insecure_http=False):
    if bool(cert_file) != bool(key_file):
        raise ProtocolError('证书与私钥必须一起指定')
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == 'localhost'
    if not loopback and not cert_file and not allow_insecure_http:
        raise ProtocolError('非回环网关需要 HTTPS 或显式实验 HTTP 选项')

    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # 不记录凭证或请求体

        def reply(self, code, value):
            self.close_connection = True
            data = json.dumps(value).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers(); self.wfile.write(data)

        def do_POST(self):
            try:
                self.connection.settimeout(15)
                nid = self.headers.get('X-AIUI-Node-ID', '')
                p = coordinator.policies['nodes'].get(nid)
                auth = self.headers.get('Authorization', '')
                if (not p or not auth.startswith('Bearer ') or not hmac.compare_digest(
                        hashlib.sha256(auth[7:].encode()).hexdigest(), p['token_sha256'])):
                    return self.reply(401, {'ok': False, 'error': '节点认证失败'})
                peer = ipaddress.ip_address(self.client_address[0])
                if not any(peer in ipaddress.ip_network(x, strict=False) for x in p['allowed_ips']):
                    return self.reply(403, {'ok': False, 'error': '来源地址未授权'})
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                    return self.reply(400, {'ok': False, 'error': '请求长度无效'})
                size = int(self.headers['Content-Length'])
                if size > MAX_BODY_BYTES:
                    return self.reply(413, {'ok': False, 'error': '请求体超过 64 MiB'})
                if size < 0 or self.headers.get_content_type() != 'application/json':
                    raise ProtocolError('请求长度或类型无效')
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ProtocolError('请求体不完整')
                body = json.loads(data)
                routes = {'/v1/register': coordinator.register, '/v1/heartbeat': coordinator.heartbeat,
                          '/v1/claim': coordinator.claim, '/v1/events': coordinator.event}
                fn = routes.get(self.path)
                if not fn:
                    return self.reply(404, {'ok': False, 'error': '接口不存在'})
                return self.reply(200, fn(nid, body))
            except ProtocolError as exc:
                return self.reply(400, {'ok': False, 'status': 'rejected',
                                        'code': 'protocol_rejected', 'error': str(exc)})
            except (ValueError, TypeError, UnicodeError):
                return self.reply(400, {'ok': False, 'status': 'rejected', 'error': '请求或分配无效'})
            except Exception:
                return self.reply(500, {'ok': False, 'status': 'rejected', 'error': '服务端未确认，请保留事件'})

        def do_GET(self):
            self.reply(404, {'ok': False, 'error': '接口不存在'})

    class GatewayServer(ThreadingHTTPServer):
        tls_context = None

        def process_request_thread(self, request, client_address):
            if self.tls_context is not None:
                try:
                    request.settimeout(3)
                    request = self.tls_context.wrap_socket(request, server_side=True)
                except (OSError, ssl.SSLError):
                    request.close()
                    return
            super().process_request_thread(request, client_address)

    srv = GatewayServer((host, port), Gateway)
    srv.daemon_threads = True
    if cert_file:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert_file, key_file)
            srv.tls_context = ctx
        except BaseException:
            srv.server_close()
            raise
    return srv
