import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RuntimeIsolationTests(unittest.TestCase):
    def paths(self, env):
        script = """
import json
from aiui.common import paths, llm
from aiui.platform import server
from aiui.platform.store import DEFAULT_DB
print(json.dumps({
 'runtime':str(paths.RUNTIME_DIR), 'screenshots':str(paths.SCREENSHOTS_DIR),
 'profiles':str(paths.PROFILES_DIR), 'logs':str(paths.LOGS_DIR),
 'llm':str(llm.LLM_CONFIG_PATH), 'audit':str(server.AUDIT), 'db':str(DEFAULT_DB)}))
"""
        result = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT,
            env=env, capture_output=True, text=True, encoding='utf-8', check=True)
        return json.loads(result.stdout)

    def test_explicit_runtime_directory_isolates_all_generated_platform_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / 'private-runtime'
            paths = self.paths({**os.environ, 'AIUI_RUNTIME_DIR': str(expected)})
            self.assertEqual(Path(paths['runtime']), expected)
            for key in ('screenshots', 'profiles', 'logs', 'llm', 'audit', 'db'):
                self.assertTrue(Path(paths[key]).is_relative_to(expected), (key, paths[key]))

    def test_default_runtime_location_remains_compatible(self):
        env = os.environ.copy()
        env.pop('AIUI_RUNTIME_DIR', None)
        self.assertEqual(Path(self.paths(env)['runtime']), ROOT / 'runtime')
