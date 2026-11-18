import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiui.platform import server


class ExecutorRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = server.AgentManager(None, None, self.root / 'logs', 'http://127.0.0.1:8000')
        self.cfg = {
            'id': 'local-1', 'kind': 'builtin', 'host': '127.0.0.1', 'port': 8001,
            'slots': 1, 'command': [], 'cwd': '', 'env': {},
        }

    def test_builtin_agent_uses_executor_venv_and_cwd(self):
        venv = self.root / '.venv' / 'Scripts'
        venv.mkdir(parents=True)
        python = venv / 'python.exe'
        python.write_text('', encoding='utf-8')
        with patch.object(server, 'EXECUTOR_ROOT', self.root):
            cmd = self.manager._cmd(self.cfg)
            cwd = self.manager._cwd(self.cfg)
        self.assertEqual(cmd[0], str(python))
        self.assertEqual(cmd[1:3], ['-m', 'aiui.agent.runner'])
        self.assertEqual(cwd, str(self.root))

    def test_builtin_agent_falls_back_to_platform_python(self):
        with patch.object(server, 'EXECUTOR_ROOT', self.root):
            cmd = self.manager._cmd(self.cfg)
        self.assertEqual(cmd[0], sys.executable)

    def test_custom_agent_cwd_keeps_platform_default(self):
        custom = dict(self.cfg, kind='custom', command=['{executor}/run-agent.exe'])
        with patch.object(server, 'EXECUTOR_ROOT', self.root):
            cmd = self.manager._cmd(custom)
            cwd = self.manager._cwd(custom)
        self.assertEqual(cmd[0], f'{self.root}/run-agent.exe')
        self.assertEqual(cwd, str(server.ROOT))

    def test_custom_command_supports_executor_placeholder(self):
        custom = dict(self.cfg, kind='custom', command=['{executor}/run-agent.exe'])
        with patch.object(server, 'EXECUTOR_ROOT', self.root):
            cmd = self.manager._cmd(custom)
        self.assertEqual(cmd, [f'{self.root}/run-agent.exe'])
