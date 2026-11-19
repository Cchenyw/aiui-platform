import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from aiui.platform import server
from aiui.platform.remote import RemoteCoordinator
from aiui.platform.store import Store


class RemoteTargetRoutesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        p = patch.object(server, 'AUDIT', root / 'audit.jsonl')
        p.start(); self.addCleanup(p.stop)
        self.store = Store(root / 'platform.db')
        self.addCleanup(self.store.close)
        self.platform = server.Platform(self.store)
        policy = {'version': 1, 'nodes': {'win-01': {
            'name': 'Windows 01', 'machine_id': 'machine-01',
            'token_sha256': hashlib.sha256(b'test-token').hexdigest(),
            'allowed_ips': ['127.0.0.1/32'], 'mcp_ids': ['builtin-browser'], 'max_slots': 1}}}
        self.remote = RemoteCoordinator(self.platform, policy)
        self.remote.register('win-01', {
            'version': 1, 'node_id': 'win-01', 'instance_id': 'instance-01',
            'machine_id': 'machine-01', 'name': 'Windows 01', 'os': 'Windows',
            'slots': 1, 'mcp_ids': ['builtin-browser'], 'features': ['env_profile']})
        for name, value in (('STORE', self.store), ('P', self.platform)):
            p = patch.object(server, name, value)
            p.start(); self.addCleanup(p.stop)
        self.case = self.store.create_case({'name': 'Feature', 'mcp_id': 'builtin-browser',
                                          'steps': [{'tool': 'snapshot', 'args': {}}]})
        setup = self.store.create_case({'name': 'Setup', 'kind': 'setup',
            'mcp_id': 'builtin-browser', 'steps': [{'tool': 'snapshot', 'args': {}}]})
        self.profile = self.store.create_env_profile({'name': 'Profile',
            'setup_case_id': setup['case_id'], 'source_mcp_id': 'builtin-browser'})
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def request(self, method, path, data=None, as_json=False):
        content_type = 'application/json' if as_json else 'application/x-www-form-urlencoded'
        body = (json.dumps(data) if as_json else urlencode(data)) if data is not None else None
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request(method, path, body=body, headers={'Content-Type': content_type})
        response = conn.getresponse()
        return response.status, response.read().decode('utf-8')

    def test_remote_node_detail_api_and_page_include_capabilities(self):
        self.remote.heartbeat('win-01', {
            'version': 1, 'node_id': 'win-01', 'instance_id': 'instance-01',
            'active_dispatch_ids': [],
            'runtime': {'agent_version': '0.1.0', 'python': '3.13.9'},
            'mcp_capabilities': [{
                'id': 'builtin-browser', 'available': True, 'tool_count': 8,
                'tools': ['navigate'], 'profile_capable': True,
            }],
        })
        code, body = self.request('GET', '/api/remote/nodes/win-01')
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data['runtime']['python'], '3.13.9')
        self.assertEqual(data['mcp_capabilities'][0]['tool_count'], 8)
        code, body = self.request('GET', '/api/remote/nodes')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['nodes'][0]['node_id'], 'win-01')
        code, page = self.request('GET', '/agents/remote/win-01')
        self.assertEqual(code, 200)
        for value in ('win-01', 'Python', '3.13.9', 'builtin-browser', 'navigate'):
            self.assertIn(value, page)
        code, _ = self.request('GET', '/api/remote/nodes/missing')
        self.assertEqual(code, 404)

    def test_slot_grant_api_and_form_update_node_capacity(self):
        code, body = self.request('POST', '/api/remote/nodes/win-01/slots',
                                  {'max_slots': 2}, as_json=True)
        self.assertEqual(code, 200, body)
        data = json.loads(body)
        self.assertEqual((data['ok'], data['slots_granted']), (True, 2))
        code, body = self.request('GET', '/api/remote/nodes/win-01')
        node = json.loads(body)
        self.assertEqual(node['slots_granted'], 2)
        self.assertEqual(node['slots_effective'], 1)  # 执行机声明只有 1
        code, _ = self.request('POST', '/agents/remote/win-01/slots', {'max_slots': '3'})
        self.assertEqual(code, 303)
        code, page = self.request('GET', '/agents/remote/win-01')
        self.assertEqual(code, 200)
        self.assertIn('value="3"', page)
        code, body = self.request('POST', '/api/remote/nodes/win-01/slots',
                                  {'max_slots': 0}, as_json=True)
        self.assertEqual(code, 400)
        code, _ = self.request('POST', '/api/remote/nodes/missing/slots',
                               {'max_slots': 2}, as_json=True)
        self.assertEqual(code, 404)
        code, _ = self.request('POST', '/agents/remote/win-01/slots', {'max_slots': 'abc'})
        self.assertEqual(code, 303)  # 表单错误走 flash 回显
        code, body = self.request('GET', '/api/remote/nodes/win-01')
        self.assertEqual(json.loads(body)['slots_granted'], 3)

    def test_mcp_probe_uses_executor_root(self):
        executor_root = Path('D:/AIUI/executor/app')
        with patch.object(server.mcp_runtime, 'probe_tools', return_value=([], "")) as probe:
            with patch.object(server, 'EXECUTOR_ROOT', executor_root):
                code, body = self.request('POST', '/api/mcps/builtin-browser/probe')
        self.assertEqual(code, 200, body)
        self.assertEqual(probe.call_args.args[1], executor_root)

    def test_single_case_json_run_honors_remote_target(self):
        code, _ = self.request('POST', f"/api/cases/{self.case['case_id']}/run",
                               {'target_node_id': 'win-01'}, as_json=True)
        self.assertEqual(code, 201)
        self.assertEqual(next(iter(self.platform.tasks.values())).get('target_node_id'), 'win-01')

    def test_html_case_run_forms_honor_remote_target(self):
        for path in (f"/cases/{self.case['case_id']}/run", '/cases/run-selected', '/cases/run-all'):
            with self.subTest(path=path):
                code, _ = self.request('POST', path,
                    {'case_ids': self.case['case_id'], 'target_node_id': 'win-01'})
                self.assertEqual(code, 303)
                self.assertEqual(list(self.platform.tasks.values())[-1].get('target_node_id'), 'win-01')

    def test_html_profile_capture_honors_remote_target(self):
        code, _ = self.request('POST', f"/profiles/{self.profile['profile_id']}/capture",
                               {'target_node_id': 'win-01'})
        self.assertEqual(code, 303)
        self.assertEqual(next(iter(self.platform.tasks.values())).get('target_node_id'), 'win-01')

    def test_pages_populate_target_options_from_parent_registration(self):
        for path in ('/cases', '/profiles', f"/profiles/{self.profile['profile_id']}"):
            with self.subTest(path=path):
                code, body = self.request('GET', path)
                self.assertEqual(code, 200)
                self.assertIn('value="win-01"', body)
