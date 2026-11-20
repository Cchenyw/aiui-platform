import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from aiui.platform.server import Platform
from aiui.platform.store import Store


class RemotePlatformTests(unittest.TestCase):
    def setUp(self):
        from aiui.platform.remote import RemoteCoordinator, load_policies
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, value in {
            'aiui.platform.server.AUDIT': root / 'audit.jsonl',
            'aiui.platform.store.PROFILES_DIR': root / 'profiles',
            'aiui.platform.remote.PROFILES_DIR': root / 'profiles',
            'aiui.common.screenshots.SCREENSHOTS_DIR': root / 'screenshots',
        }.items():
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = root / 'p.db'
        self.store = Store(self.path)
        self.p = Platform(self.store)
        self.policy = {'version': 1, 'nodes': {'n1': {
            'name': 'Node', 'machine_id': 'm1',
            'token_sha256': hashlib.sha256(b'secret').hexdigest(),
            'allowed_ips': ['127.0.0.1/32'], 'mcp_ids': ['builtin-browser'], 'max_slots': 1}}}
        self.r = RemoteCoordinator(self.p, load_policies(self.policy))
        self.body = dict(version=1, node_id='n1', instance_id='i1', machine_id='m1',
                         name='Node', os='Windows', slots=99, mcp_ids=['builtin-browser', 'forbidden'],
                         features=['env_profile'], active_dispatch_ids=[])

    def tearDown(self):
        if hasattr(self, 'store'):
            self.store.close()


    def register(self):
        return self.r.register('n1', self.body.copy())

    def task(self, **kw):
        return self.p.add_tasks([dict(name='Task', mcp_id='builtin-browser', steps=[], **kw)])[0]

    def event(self, task, event_id='e1', kind='result', **kw):
        return dict(version=1, node_id='n1', instance_id='i1', event_id=event_id,
                    task_id=task['task_id'], dispatch_id=task['dispatch_id'], type=kind,
                    payload=kw or {'ok': True, 'elapsed_ms': 1})

    def test_run_detail_retains_remote_dispatch_metadata(self):
        self.register(); tid = self.task(target_node_id='n1')
        task = self.r.claim('n1', self.body)['task']
        run = self.store.get_run(tid)
        self.assertEqual(run.get('dispatch_id'), task['dispatch_id'])
        self.assertEqual(run.get('node_id'), 'n1')
        self.assertEqual(run.get('machine_id'), 'm1')
        self.assertEqual(run.get('target_node_id'), 'n1')

    def test_explicit_task_id_cannot_overwrite_durable_assignment(self):
        self.register()
        tid = self.p.add_tasks([{'id': 'fixed-task', 'name': 'original', 'mcp_id': 'builtin-browser'}])[0]
        self.r.claim('n1', self.body)
        with self.assertRaises(ValueError):
            self.p.add_tasks([{'id': tid, 'name': 'replacement', 'mcp_id': 'builtin-browser'}])
        self.assertEqual(self.store.get_run(tid)['name'], 'original')

    def test_generated_ids_remain_distinct_after_parent_restart(self):
        from types import SimpleNamespace
        values = [SimpleNamespace(hex='aaaa' + '1' * 28), SimpleNamespace(hex='aaaa' + '2' * 28)]
        with patch('aiui.platform.server.uuid.uuid4', side_effect=values):
            tid = self.task()
            self.p = Platform(self.store)
            other = self.task()
        self.assertNotEqual(tid, other)
        self.assertEqual(self.store.count_runs(), 2)

    def restart_with_policy(self, policy):
        from aiui.platform.remote import RemoteCoordinator
        self.store.close()
        self.store = Store(self.path)
        self.store.recover_stale_runs()
        self.p = Platform(self.store)
        self.r = RemoteCoordinator(self.p, policy)

    def test_agent_manager_stop_preserves_remote_assignment(self):
        from aiui.platform.server import AgentManager
        self.register(); tid = self.task()
        task = self.r.claim('n1', self.body)['task']
        manager = AgentManager(self.store, self.p, Path(self.tmp.name) / 'logs', 'http://127.0.0.1:1')
        before = list(self.p.queue)
        manager.stop('n1')
        self.assertEqual(self.p.tasks[tid]['state'], 'awaiting_result')
        self.assertEqual(self.p.tasks[tid]['agent'], 'n1')
        self.assertEqual(self.p.tasks[tid]['dispatch_id'], task['dispatch_id'])
        self.assertEqual(self.p.queue, before)
        self.assertEqual(self.r.list_nodes()[0]['status'], 'offline')
        self.p.register({'agent_id': 'local'})
        self.assertNotIn(tid, [x[2] for x in self.p.queue])
        self.r.event('n1', self.event(task))
        self.assertEqual(self.store.get_run(tid)['state'], 'done')

    def test_restart_revokes_cached_mcp_grant_without_registration(self):
        import copy
        self.register(); self.task()
        task = self.r.claim('n1', self.body)['task']
        policy = copy.deepcopy(self.policy)
        policy['nodes']['n1']['mcp_ids'] = []
        self.restart_with_policy(policy)
        self.r.event('n1', self.event(task))
        self.task()
        self.r.heartbeat('n1', self.body)
        self.assertIsNone(self.r.claim('n1', self.body)['task'])
        self.assertEqual(self.r.list_nodes()[0]['mcp_ids'], [])

    def test_restart_reduces_cached_slots_without_registration(self):
        import copy
        self.r.policies['nodes']['n1']['max_slots'] = 2
        self.register(); self.task(); self.task()
        task = self.r.claim('n1', self.body)['task']
        policy = copy.deepcopy(self.policy)
        policy['nodes']['n1']['max_slots'] = 1
        self.restart_with_policy(policy)
        self.task()
        active = {**self.body, 'active_dispatch_ids': [task['dispatch_id']]}
        self.r.heartbeat('n1', active)
        self.assertIsNone(self.r.claim('n1', active)['task'])
        self.assertEqual(self.r.list_nodes()[0]['slots'], 1)
        self.r.event('n1', self.event(task))

    def test_update_grant_applies_immediately_without_killing_running_tasks(self):
        self.register()  # 声明 99，授权 1
        self.assertEqual(self.r.list_nodes()[0]['slots_effective'], 1)
        result = self.r.update_grant('n1', 2)
        self.assertEqual(result['slots_granted'], 2)
        node = self.r.list_nodes()[0]
        self.assertEqual((node['slots_granted'], node['slots_effective'], node['slots']), (2, 2, 2))
        first = self.task(); second = self.task()
        a = self.r.claim('n1', self.body)['task']
        b = self.r.claim('n1', {**self.body, 'active_dispatch_ids': [a['dispatch_id']]})['task']
        self.assertIsNotNone(a); self.assertIsNotNone(b)
        self.r.update_grant('n1', 1)
        node = self.r.list_nodes()[0]
        self.assertEqual((node['slots_granted'], node['slots_effective']), (1, 1))
        active = {**self.body, 'active_dispatch_ids': [a['dispatch_id'], b['dispatch_id']]}
        self.assertIsNone(self.r.claim('n1', active)['task'])
        # 降低授权不打断在跑任务，占用回落后才恢复派发
        self.assertEqual(self.p.tasks[first]['state'], 'running')
        self.assertEqual(self.p.tasks[second]['state'], 'running')
        self.r.event('n1', self.event(a, event_id='e1'))
        self.r.event('n1', self.event(b, event_id='e2'))
        self.task()
        self.assertIsNotNone(self.r.claim('n1', self.body)['task'])

    def test_update_grant_persists_policy_file_and_validates(self):
        from aiui.platform.remote import RemoteCoordinator, load_policies
        path = Path(self.tmp.name) / 'gateway-policies.json'
        path.write_text(json.dumps(self.policy, ensure_ascii=False), encoding='utf-8')
        coordinator = RemoteCoordinator(self.p, load_policies(path), policy_path=path)
        result = coordinator.update_grant('n1', 3)
        self.assertEqual(result['slots_granted'], 3)
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['nodes']['n1']['max_slots'], 3)
        self.assertEqual(saved['nodes']['n1']['token_sha256'],
                         self.policy['nodes']['n1']['token_sha256'])
        self.assertFalse(Path(str(path) + '.tmp').exists())
        for bad in (0, 129, '2', True, 2.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                coordinator.update_grant('n1', bad)
        self.assertEqual(
            json.loads(path.read_text(encoding='utf-8'))['nodes']['n1']['max_slots'], 3)
        with self.assertRaises(ValueError):
            coordinator.update_grant('missing', 1)

    def test_heartbeat_reports_effective_slots_after_grant_change(self):
        self.register()  # 声明 99，授权 1
        self.assertEqual(self.r.heartbeat('n1', self.body)['slots'], 1)
        self.r.update_grant('n1', 3)
        self.assertEqual(self.r.heartbeat('n1', self.body)['slots'], 3)
        # 声明低于授权时仍以声明封顶
        low = {**self.body, 'slots': 2}
        self.r.register('n1', low)
        self.assertEqual(self.r.heartbeat('n1', low)['slots'], 2)

    def test_restart_machine_binding_change_blocks_claim_but_accepts_old_result(self):
        import copy
        self.register(); self.task()
        task = self.r.claim('n1', self.body)['task']
        policy = copy.deepcopy(self.policy)
        policy['nodes']['n1']['machine_id'] = 'new-machine'
        self.restart_with_policy(policy)
        for action in (self.r.heartbeat, self.r.claim):
            with self.subTest(action=action.__name__), self.assertRaises(ValueError):
                action('n1', self.body)
        self.r.event('n1', self.event(task))
        self.assertEqual(self.store.get_run(task['task_id'])['state'], 'done')

    def test_policy_owns_display_name(self):
        self.r.register('n1', {**self.body, 'name': 'Client display name'})
        self.assertEqual(self.r.list_nodes()[0]['name'], 'Node')

    def test_malformed_result_cannot_complete_assignment(self):
        self.register(); tid = self.task(); task = self.r.claim('n1', self.body)['task']
        for payload in ({}, {'ok': 'true'}, {'ok': True, 'steps': 'bad'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.r.event('n1', {**self.event(task), 'payload': payload})
        self.assertEqual(self.store.get_run(tid)['state'], 'running')

    def test_policy_metadata_slots_and_identity(self):
        v = self.register()
        self.assertEqual(v['slots'], 1)
        self.assertEqual(v['mcp_ids'], ['builtin-browser'])
        for delta in ({'machine_id': 'other'}, {'version': 2}, {'node_id': 'other'}):
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                self.r.register('n1', {**self.body, **delta})
        with self.assertRaises(ValueError):
            self.r.register('n1', {**self.body, 'instance_id': 'i2'})

    def test_registration_reports_runtime_slots_and_sanitized_capabilities(self):
        body = {
            **self.body,
            'runtime': {
                'agent_version': '0.1.0', 'python': '3.13.9', 'os_version': '10.0.26100',
                'api_key': 'must-not-store', 'unknown': 'drop-me',
                'llm': {'configured': True, 'provider': 'openai',
                        'model': 'deepseek-flash', 'api_key': 'must-not-store'},
                'resources': {'cpu_cores': 4, 'memory_mb': 8192, 'disk_free_mb': 1000},
            },
            'slots_recommended': 4,
            'mcp_capabilities': [
                {'id': 'builtin-browser', 'available': True, 'tool_count': 2,
                 'tools': ['navigate', 'snapshot'], 'profile_capable': True, 'probed_at': 123},
                {'id': 'forbidden', 'available': True, 'tool_count': 1},
            ],
        }
        reply = self.r.register('n1', body)
        node = self.r.list_nodes()[0]
        self.assertEqual(reply['slots'], 1)
        self.assertEqual(reply['mcp_ids'], ['builtin-browser'])
        self.assertEqual([m['id'] for m in reply['mcps']], ['builtin-browser'])
        self.assertEqual(node['slots_advertised'], 99)
        self.assertEqual(node['slots_granted'], 1)
        self.assertEqual(node['slots_effective'], 1)
        self.assertEqual(node['slots_recommended'], 4)
        self.assertEqual(node['runtime']['python'], '3.13.9')
        self.assertNotIn('api_key', node['runtime'])
        self.assertNotIn('unknown', node['runtime'])
        self.assertNotIn('api_key', node['runtime']['llm'])
        self.assertEqual(node['runtime']['resources']['cpu_cores'], 4)
        self.assertEqual([c['id'] for c in node['mcp_capabilities']], ['builtin-browser'])
        self.assertEqual(node['mcp_capabilities'][0]['tool_count'], 2)

    def test_heartbeat_updates_runtime_and_filters_capabilities(self):
        self.register()
        heartbeat = {
            **self.body, 'active_dispatch_ids': [],
            'runtime': {'python': '3.14.0'},
            'mcp_capabilities': [
                {'id': 'builtin-browser', 'available': False, 'tool_count': 0, 'error': 'probe failed'},
                {'id': 'forbidden', 'available': True, 'tool_count': 3},
            ],
        }
        self.r.heartbeat('n1', heartbeat)
        node = self.r.list_nodes()[0]
        self.assertEqual(node['runtime']['python'], '3.14.0')
        self.assertEqual([c['id'] for c in node['mcp_capabilities']], ['builtin-browser'])
        self.assertFalse(node['mcp_capabilities'][0]['available'])
        self.assertEqual(node['mcp_capabilities'][0]['error'], 'probe failed')

    def test_old_registration_without_extras_remains_compatible(self):
        reply = self.register()
        node = self.r.list_nodes()[0]
        self.assertEqual(reply['slots'], 1)
        self.assertEqual(node['slots_advertised'], 99)
        self.assertEqual(node['slots_granted'], 1)
        self.assertEqual(node['slots_effective'], 1)
        self.assertNotIn('runtime', node)
        self.assertNotIn('mcp_capabilities', node)

    def test_matching_replay_capacity_and_local_target(self):
        self.register()
        blocked = self.task(target_node_id='other')
        tid = self.task(target_node_id='n1')
        self.p.register({'agent_id': 'local'})
        self.assertIsNone(self.p._pick_agent(self.p.tasks[tid]))
        t = self.r.claim('n1', self.body)['task']
        self.assertEqual(t['task_id'], tid)
        self.assertEqual(self.r.claim('n1', self.body)['task']['dispatch_id'], t['dispatch_id'])
        hb = {**self.body, 'active_dispatch_ids': [t['dispatch_id']]}
        self.r.heartbeat('n1', hb)
        self.assertIsNone(self.r.claim('n1', hb)['task'])
        self.assertEqual(self.p.tasks[blocked]['state'], 'queued')

    def test_events_deduplicate_and_reject_cross_instance_or_dispatch(self):
        self.register(); self.task()
        t = self.r.claim('n1', self.body)['task']
        e = self.event(t, kind='logs', logs=[{'message': 'one'}])
        self.assertEqual(self.r.event('n1', e)['status'], 'received')
        self.assertEqual(self.r.event('n1', e)['status'], 'duplicate')
        self.assertEqual(sum(x['message'] == 'one' for x in self.store.list_logs(t['task_id'])), 1)
        with self.assertRaises(ValueError):
            self.r.event('n1', {**self.event(t), 'instance_id': 'i2'})
        with self.assertRaises(ValueError):
            self.r.event('n1', {**self.event(t), 'dispatch_id': 'wrong'})
        e = self.event(t, 'result')
        self.assertEqual(self.r.event('n1', e)['status'], 'received')
        self.assertEqual(self.r.event('n1', e)['status'], 'duplicate')
        self.assertEqual(self.store.get_run(t['task_id'])['state'], 'done')

    def test_instance_switch_does_not_transfer_old_work(self):
        self.register(); self.task(); t = self.r.claim('n1', self.body)['task']
        self.r.mark_offline('n1')
        self.r.register('n1', {**self.body, 'instance_id': 'i2'})
        self.assertIsNone(self.r.claim('n1', {**self.body, 'instance_id': 'i2'})['task'])
        with self.assertRaises(ValueError):
            self.r.claim('n1', self.body)
        self.r.event('n1', self.event(t))
        self.assertEqual(self.r.list_nodes()[0]['instance_id'], 'i2')

    def test_restart_preserves_dispatch_budget_and_original_result(self):
        from aiui.platform.remote import RemoteCoordinator
        self.register(); self.task(mode='ai')
        t = self.r.claim('n1', self.body)['task']
        self.store.close(); self.store = Store(self.path)
        self.store.recover_stale_runs()
        self.p = Platform(self.store)
        self.r = RemoteCoordinator(self.p, self.policy)
        self.r.recover()
        self.assertEqual(self.p.tasks[t['task_id']]['state'], 'awaiting_result')
        self.register()
        replay = self.r.claim('n1', self.body)['task']
        self.assertEqual(replay['dispatch_id'], t['dispatch_id'])
        self.assertEqual(replay['budget'], t['budget'])
        self.r.event('n1', self.event(t))
        self.assertEqual(self.store.get_run(t['task_id'])['state'], 'done')

    def test_management_result_cannot_bypass_remote_identity(self):
        self.register(); self.task(); t = self.r.claim('n1', self.body)['task']
        resp = self.p.on_result({'task_id': t['task_id'], 'agent_id': 'n1', 'ok': True})
        self.assertFalse(resp['ok'])
        self.assertEqual(self.store.get_run(t['task_id'])['state'], 'running')

    def test_assignment_transaction_rolls_back_on_save_failure(self):
        self.register(); tid = self.task()
        with patch.object(self.store, 'save_run', side_effect=RuntimeError('disk')):
            with self.assertRaises(RuntimeError):
                self.r.claim('n1', self.body)
        self.assertEqual(self.p.tasks[tid]['state'], 'queued')
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM remote_assignments').fetchone()[0], 0)

    def test_frozen_profile_version_and_features_across_restart(self):
        from aiui.platform.remote import RemoteCoordinator
        state = {'cookies': [{'name': 'sid', 'value': 'private-original', 'domain': 'example.test', 'path': '/'}], 'origins': []}
        pid = self.store.create_env_profile({'name': 'P', 'setup_case_id': ''})['profile_id']
        self.store.finish_profile_capture(pid, state, 'capture1', source_mcp_id='builtin-browser')
        tid = self.task(profile_id=pid)
        self.r.register('n1', {**self.body, 'features': []})
        self.assertIsNone(self.r.claim('n1', self.body)['task'])
        self.register()
        task = self.r.claim('n1', self.body)['task']
        changed = json.loads(json.dumps(state)); changed['cookies'][0]['value'] = 'private-new'
        self.store.finish_profile_capture(pid, changed, 'capture2')
        self.store.close(); self.store = Store(self.path); self.store.recover_stale_runs()
        self.p = Platform(self.store); self.r = RemoteCoordinator(self.p, self.policy)
        self.register()
        replay = self.r.claim('n1', self.body)['task']
        self.assertEqual(replay['profile'], task['profile'])
        snapshot = self.store.conn.execute('SELECT snapshot_json FROM remote_assignments').fetchone()[0]
        self.assertNotIn('private-original', snapshot)
        self.assertNotIn('private-original', json.dumps(self.store.get_run(tid)))

    def test_result_and_event_commit_atomically_on_disk_error(self):
        self.register(); tid = self.task(); task = self.r.claim('n1', self.body)['task']
        with patch.object(self.store, 'save_run', side_effect=RuntimeError('disk')):
            with self.assertRaises(RuntimeError):
                self.r.event('n1', self.event(task))
        self.assertEqual(self.store.get_run(tid)['state'], 'running')
        self.assertEqual(self.p.tasks[tid]['state'], 'running')
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM remote_events').fetchone()[0], 0)
        self.assertEqual(self.store.conn.execute('SELECT completed FROM remote_assignments').fetchone()[0], 0)
        self.r.event('n1', self.event(task))
        self.assertEqual(self.store.get_run(tid)['state'], 'done')

    def test_policy_revocation_and_legacy_registration(self):
        self.register()
        with self.assertRaises(ValueError):
            self.p.register({'agent_id': 'n1'})
        del self.r.policies['nodes']['n1']
        with self.assertRaises(ValueError):
            self.r.claim('n1', self.body)

    def test_offline_never_requeues_and_capacity_uses_assignments(self):
        self.register(); tid = self.task(); self.task()
        task = self.r.claim('n1', self.body)['task']
        self.r.heartbeat('n1', {**self.body, 'active_dispatch_ids': [task['dispatch_id']], 'running': 0})
        self.assertIsNone(self.r.claim('n1', self.body)['task'])
        with patch('aiui.platform.remote.time.time', return_value=10**12):
            self.r.tick()
        self.assertEqual(self.p.tasks[tid]['state'], 'awaiting_result')
        self.assertNotIn(tid, [x[2] for x in self.p.queue])
        self.r.event('n1', self.event(task, ok=False, error='unknown', retryable=True))
        self.assertEqual(self.p.tasks[tid]['state'], 'failed')

    def test_three_nodes_target_and_concurrent_claim(self):
        from concurrent.futures import ThreadPoolExecutor
        self.register(); self.task()
        with ThreadPoolExecutor(max_workers=3) as pool:
            claims = list(pool.map(lambda _: self.r.claim('n1', self.body)['task'], range(3)))
        self.assertEqual(len({x['dispatch_id'] for x in claims}), 1)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM remote_assignments').fetchone()[0], 1)
        for i in (2, 3):
            nid = 'n' + str(i)
            self.r.policies['nodes'][nid] = {**self.policy['nodes']['n1'], 'machine_id': 'm' + str(i)}
            body = {**self.body, 'node_id': nid, 'machine_id': 'm' + str(i)}
            self.r.register(nid, body)
            tid = self.task(target_node_id=nid)
            self.assertEqual(self.r.claim(nid, body)['task']['task_id'], tid)

    def test_gateway_errors_are_json_and_hide_secrets(self):
        from aiui.platform.remote import make_gateway
        srv = make_gateway(self.r, '127.0.0.1', 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            for path, raw in (('/api/llm', '{}'), ('/v1/register', 'bad-secret-token')):
                c = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=3)
                c.request('POST', path, raw, {'Authorization': 'Bearer secret', 'X-AIUI-Node-ID': 'n1', 'Content-Type': 'application/json'})
                resp = c.getresponse(); data = resp.read(); c.close()
                self.assertGreaterEqual(resp.status, 400)
                self.assertFalse(json.loads(data)['ok'])
                self.assertNotIn(b'secret', data)
        finally:
            srv.shutdown(); srv.server_close()

    def test_gateway_security_and_limits(self):
        from aiui.platform.remote import make_gateway
        from aiui.common.node_protocol import MAX_BODY_BYTES
        srv = make_gateway(self.r, '127.0.0.1', 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        def request(body, token='secret', **headers):
            c = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=3)
            h = {'Authorization': 'Bearer ' + token, 'X-AIUI-Node-ID': 'n1', 'Content-Type': 'application/json', **headers}
            c.request('POST', '/v1/register', body=body, headers=h)
            resp = c.getresponse(); code = resp.status; resp.read(); c.close(); return code
        try:
            self.assertEqual(request(json.dumps(self.body)), 200)
            self.assertEqual(request(json.dumps(self.body), token='bad'), 401)
            self.assertEqual(request(json.dumps({**self.body, 'version': 2})), 400)
            self.assertEqual(request('[]'), 400)
            self.assertEqual(request('{}', **{'Content-Length': str(MAX_BODY_BYTES + 1)}), 413)
            self.r.policies['nodes']['n1']['allowed_ips'] = ['10.0.0.0/8']
            self.assertEqual(request(json.dumps(self.body), **{'X-Forwarded-For': '10.1.1.1'}), 403)
        finally:
            srv.shutdown(); srv.server_close()
        with self.assertRaises(ValueError):
            make_gateway(self.r, '0.0.0.0', 0)


if __name__ == '__main__':
    unittest.main()
