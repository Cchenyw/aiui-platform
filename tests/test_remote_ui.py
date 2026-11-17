import unittest

from aiui.platform import ui


NODE = {"node_id": "win-exec-01", "machine_id": "win-server-01", "name": "Remote 01",
        "instance_id": "instance-01", "connection": "pull", "status": "offline",
        "running": 1, "slots": 1, "last_hb": 1700000000,
        "mcp_ids": ["builtin-browser"], "features": ["env_profile"]}


class RemoteNodeUiTests(unittest.TestCase):
    def test_dashboard_includes_remote_online_nodes_and_pending_results(self):
        body = ui.dashboard({'status': {'agents': [], 'remote_nodes': [{**NODE, 'status': 'online'}],
            'counts': {'awaiting_result': 2}, 'total': 2, 'avg_elapsed_ms': 0},
            'runs': [], 'case_count': 0, 'run_count': 2})
        self.assertIn('win-server-01', body)
        self.assertIn('<span>执行节点在线</span><b>1</b>', body)
        self.assertIn('<span>待回传</span><b>2</b>', body)

    def test_remote_nodes_show_machine_capacity_and_heartbeat_without_local_controls(self):
        body = ui.agents_page({"configs": [], "procs": {}, "here": ".", "log_dir": ".",
                               "status": {"agents": [], "remote_nodes": [NODE]}})
        for value in ("win-exec-01", "win-server-01", "builtin-browser", "1/1",
                      "/agents/remote/win-exec-01"):
            self.assertIn(value, body)
        self.assertNotIn('/agents/win-exec-01/start', body)
        self.assertNotIn('/agents/win-exec-01/stop', body)

    def test_remote_node_detail_shows_runtime_slots_and_capabilities(self):
        node = {
            **NODE,
            'slots_advertised': 2, 'slots_granted': 1, 'slots_effective': 1,
            'slots_recommended': 2, 'running': 0,
            'runtime': {
                'agent_version': '0.1.0', 'protocol': 1, 'os': 'Windows',
                'os_version': '10.0.26100', 'arch': 'AMD64', 'python': '3.13.9',
                'node': '24.13.0', 'npm': '11.20.0', 'playwright': '1.65.0',
                'chrome': 'C:/chrome.exe', 'chrome_detected': True,
                'chromium_configured': False, 'account': 'SYSTEM',
                'llm': {'configured': True, 'provider': 'openai',
                        'model': 'deepseek-flash', 'base_url': 'https://api.deepseek.com'},
                'resources': {'cpu_cores': 4, 'memory_mb': 8192, 'disk_free_mb': 1000},
            },
            'mcp_capabilities': [{
                'id': 'builtin-browser', 'available': True, 'tool_count': 8,
                'tools': ['navigate', 'snapshot'], 'profile_capable': True,
                'probed_at': 1700000000,
            }],
        }
        body = ui.remote_node_page({'node': node, 'runs': []})
        for value in ('执行机声明', '平台授权上限', '实际生效', '硬件建议',
                      '3.13.9', '24.13.0', 'deepseek-flash', 'builtin-browser',
                      '>8<', 'navigate', '可用'):
            self.assertIn(value, body)

    def test_cases_can_select_a_registered_remote_target(self):
        body = ui.cases_page({"cases": [], "mcps": [], "profiles": {}, "q": "",
                              "execution_nodes": [NODE]})
        self.assertIn('value="win-exec-01"', body)
        self.assertIn('name="target_node_id"', body)

    def test_remote_run_detail_keeps_historical_execution_identity(self):
        run = {"task_id": "T-test", "name": "Example", "state": "awaiting_result",
               "attempt": 0, "max_retry": 0, "agent_id": "win-exec-01", "created": 1,
               "started": 2, "finished": None, "error": "", "case": {}, "mcp": {},
               "result": {}, "connection": "pull", "node_id": "win-exec-01",
               "machine_id": "win-server-01", "instance_id": "instance-01",
               "dispatch_id": "dispatch-001"}
        body = ui.run_detail({"run": run})
        for value in ("win-server-01", "instance-01", "dispatch-001"):
            self.assertIn(value, body)
