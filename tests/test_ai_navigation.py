"""AI 站点导航提示、副作用重试策略的回归测试。"""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from aiui.platform import server
from aiui.platform.store import Store


class AiNavigationCaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.root = root
        for target, value in (
                ('aiui.platform.server.AUDIT', root / 'audit.jsonl'),
                ('aiui.common.screenshots.SCREENSHOTS_DIR', root / 'screenshots')):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = Store(root / 'platform.db')
        self.addCleanup(self.store.close)
        self.platform = server.Platform(self.store)
        for name, value in (('STORE', self.store), ('P', self.platform)):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def request(self, method, path, data=None, as_json=False):
        content_type = 'application/json' if as_json else 'application/x-www-form-urlencoded'
        body = (json.dumps(data) if as_json else urlencode(data, doseq=True)) if data is not None else None
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request(method, path, body=body, headers={'Content-Type': content_type})
        response = conn.getresponse()
        return response.status, response.read().decode('utf-8')

    def ai_case(self, **overrides):
        data = {
            'name': '盘后固定价格-导航',
            'mode': 'ai',
            'mcp_id': 'builtin-browser',
            'instruction': '从首页点击柜台委托，再点击盘后固定价格交易。',
            'expected': '页面显示持仓单元、证券代码、委托价格、提交委托。',
            'max_steps': 12,
            'timeout_s': 600,
            'navigation_hint': '柜台委托 > 盘后固定价格交易\n预期面包屑：柜台委托 / 盘后固定价格交易',
        }
        data.update(overrides)
        return server.validate_case(data)

    def test_case_roundtrip_includes_navigation_and_retry_fields(self):
        case = self.store.create_case(self.ai_case(max_retry=3, side_effect=False))
        self.assertEqual(case['navigation_hint'], '柜台委托 > 盘后固定价格交易\n预期面包屑：柜台委托 / 盘后固定价格交易')
        self.assertEqual(case['max_retry'], 3)
        self.assertFalse(case['side_effect'])
        self.assertEqual(self.store.get_case(case['case_id'])['navigation_hint'],
                         case['navigation_hint'])

    def test_side_effect_forces_zero_retry(self):
        case = self.ai_case(max_retry=5, side_effect=True)
        self.assertTrue(case['side_effect'])
        self.assertEqual(case['max_retry'], 0)

    def test_task_snapshot_uses_case_retry_and_navigation_hint(self):
        normal = self.store.create_case(self.ai_case(name='普通', max_retry=1))
        normal_id = self.platform.add_tasks([normal])[0]
        normal_task = self.platform.tasks[normal_id]
        self.assertEqual(normal_task['max_retry'], 1)
        self.assertEqual(normal_task['case']['navigation_hint'],
                         normal['navigation_hint'])

        side = self.store.create_case(self.ai_case(name='副作用', max_retry=5,
                                                   side_effect=True))
        side_id = self.platform.add_tasks([side], max_retry=3)[0]
        self.assertEqual(self.platform.tasks[side_id]['max_retry'], 0)

    def test_task_retry_can_be_overridden_by_run_request(self):
        case = self.store.create_case(self.ai_case(max_retry=3))
        task_id = self.platform.add_tasks([case], max_retry=0)[0]
        self.assertEqual(self.platform.tasks[task_id]['max_retry'], 0)

    def test_case_form_contains_navigation_hint_and_side_effect_controls(self):
        code, page = self.request('GET', '/cases/new')
        self.assertEqual(code, 200)
        for text in ('站点导航提示', 'name=navigation_hint', 'name=max_retry',
                     '副作用用例', 'name=side_effect'):
            self.assertIn(text, page)

    def test_single_case_run_api_honors_max_retry(self):
        case = self.store.create_case(self.ai_case(max_retry=3))
        code, body = self.request('POST', f'/api/cases/{case["case_id"]}/run',
                                  {'max_retry': 0, 'target_node_id': 'local-ai'},
                                  as_json=True)
        self.assertEqual(code, 201, body)
        task_id = json.loads(body)['task_ids'][0]
        self.assertEqual(self.platform.tasks[task_id]['max_retry'], 0)

    def test_freeze_skips_virtual_observation_tools_and_keeps_side_effect(self):
        case = self.store.create_case(self.ai_case(side_effect=True))
        task_id = self.platform.add_tasks([case])[0]
        task = self.platform.tasks[task_id]
        task['state'] = 'done'
        task['finished'] = time.time()
        task['result'] = {
            'ok': True, 'elapsed_ms': 10, 'steps': [
                {'tool': 'inspect_page', 'args': {}, 'ok': True, 'result': '{}'},
                {'tool': 'browser_click', 'args': {'target': 'e1'}, 'ok': True,
                 'result': 'ok'},
                {'tool': '__finish__', 'args': {}, 'ok': True, 'result': 'done'},
            ],
        }
        self.platform._save(task)
        frozen = server.freeze_run(task_id)
        self.assertTrue(frozen['side_effect'])
        self.assertEqual(frozen['max_retry'], 0)
        self.assertEqual([s['tool'] for s in frozen['steps']], ['browser_click'])
        self.assertEqual(frozen['navigation_hint'], '')

    def test_run_detail_shows_navigation_hint_and_audit_warnings(self):
        case = self.store.create_case(self.ai_case(side_effect=True))
        task_id = self.platform.add_tasks([case])[0]
        task = self.platform.tasks[task_id]
        task['state'] = 'done'
        task['finished'] = time.time()
        task['result'] = {
            'ok': True, 'elapsed_ms': 10, 'steps': [],
            'user_level_warnings': ['直接 URL 跳转（第 2 次 navigate；非用户级操作）'],
        }
        self.platform._save(task)
        code, page = self.request('GET', f'/runs/{task_id}')
        self.assertEqual(code, 200)
        for text in ('站点导航提示', '柜台委托 / 盘后固定价格交易', '副作用用例',
                     '用户级操作告警', '直接 URL 跳转'):
            self.assertIn(text, page)


if __name__ == '__main__':
    unittest.main()
