"""运行记录删除与详情页用例信息的回归测试。"""
import hashlib
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


class RunRecordTests(unittest.TestCase):
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

    def add_run(self, name='登录-管理员', description='验证管理员可以正常登录', state=None):
        task_id = self.platform.add_tasks([{
            'name': name, 'description': description,
            'mcp_id': 'builtin-browser', 'steps': []}])[0]
        if state:
            task = self.platform.tasks[task_id]
            task['state'] = state
            task['finished'] = time.time()
            task['result'] = {'ok': state == 'done', 'elapsed_ms': 5, 'steps': []}
            self.platform._save(task)
        return task_id

    def test_delete_rejects_active_run(self):
        task_id = self.add_run()
        with self.assertRaises(ValueError):
            self.platform.delete_run(task_id)
        self.assertIsNotNone(self.store.get_run(task_id))

    def test_delete_finished_run_removes_records_logs_and_screenshots(self):
        task_id = self.add_run(state='done')
        self.store.add_logs(task_id, [{'level': 'info', 'source': 'agent', 'message': 'hello'}])
        shots = Path(server.screenshots.SCREENSHOTS_DIR) / hashlib.sha256(
            task_id.encode('utf-8')).hexdigest()
        shots.mkdir(parents=True)
        (shots / 'a.png').write_bytes(b'png')
        self.assertTrue(self.platform.delete_run(task_id))
        self.assertIsNone(self.store.get_run(task_id))
        self.assertEqual(self.store.list_logs(task_id), [])
        self.assertFalse(shots.exists())
        self.assertNotIn(task_id, self.platform.tasks)
        self.assertFalse(self.platform.delete_run(task_id))

    def test_run_detail_shows_case_name_and_description(self):
        task_id = self.add_run(name='登录-管理员', description='验证管理员可以正常登录')
        code, page = self.request('GET', f'/runs/{task_id}')
        self.assertEqual(code, 200)
        for text in ('用例信息', '用例名称', '登录-管理员', '验证管理员可以正常登录'):
            self.assertIn(text, page)

    def test_run_list_offers_delete_controls(self):
        task_id = self.add_run(state='done')
        code, page = self.request('GET', '/runs')
        self.assertEqual(code, 200)
        self.assertIn('删除选中', page)
        self.assertIn('清空已结束', page)
        self.assertIn(f'/runs/{task_id}/delete', page)

    def test_single_and_bulk_delete_routes(self):
        task_id = self.add_run(state='done')
        code, _ = self.request('POST', f'/runs/{task_id}/delete')
        self.assertEqual(code, 303)
        self.assertIsNone(self.store.get_run(task_id))

        api_id = self.add_run(state='done')
        code, body = self.request('POST', f'/api/runs/{api_id}/delete', {}, as_json=True)
        self.assertEqual(code, 200, body)
        self.assertIsNone(self.store.get_run(api_id))

        code, _ = self.request('POST', '/api/runs/missing/delete', {}, as_json=True)
        self.assertEqual(code, 404)

        done = self.add_run(state='done')
        queued = self.add_run()
        code, _ = self.request('POST', '/runs/delete-selected',
                               {'task_id': [done, queued]})
        self.assertEqual(code, 303)
        self.assertIsNone(self.store.get_run(done))
        self.assertIsNotNone(self.store.get_run(queued))

        finished = self.add_run(state='failed')
        code, _ = self.request('POST', '/runs/delete-finished')
        self.assertEqual(code, 303)
        self.assertIsNone(self.store.get_run(finished))
        self.assertIsNotNone(self.store.get_run(queued))


if __name__ == '__main__':
    unittest.main()
