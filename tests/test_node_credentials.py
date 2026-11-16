import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/create_node_credentials.py"


class NodeCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "access"

    def create(self, node="win-01", address="127.0.0.1/32"):
        return subprocess.run([
            sys.executable, "-B", str(SCRIPT), "--node-id", node,
            "--machine-id", "machine-" + node, "--allowed-ip", address,
            "--output-dir", str(self.root),
        ], capture_output=True, text=True, encoding="utf-8")

    def test_generates_private_token_and_only_its_hash_in_policy(self):
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        token = (self.root / "win-01.token").read_text(encoding="utf-8").strip()
        self.assertGreaterEqual(len(token), 40)
        raw_policy = (self.root / "gateway-policies.json").read_text(encoding="utf-8")
        policy = json.loads(raw_policy)
        node = policy["nodes"]["win-01"]
        self.assertEqual(policy["version"], 1)
        self.assertEqual(node["token_sha256"], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(node["allowed_ips"], ["127.0.0.1/32"])
        self.assertEqual(node["max_slots"], 1)
        self.assertEqual(node["machine_id"], "machine-win-01")
        self.assertNotIn(token, raw_policy + result.stdout + result.stderr)

    def test_adds_another_node_without_changing_existing_credential(self):
        self.assertEqual(self.create().returncode, 0)
        first = (self.root / "win-01.token").read_bytes()
        self.assertEqual(self.create("win-02").returncode, 0)
        policy = json.loads((self.root / "gateway-policies.json").read_text(encoding="utf-8"))
        self.assertEqual(set(policy["nodes"]), {"win-01", "win-02"})
        self.assertEqual((self.root / "win-01.token").read_bytes(), first)

    def test_duplicate_node_refuses_to_overwrite_existing_files(self):
        self.assertEqual(self.create().returncode, 0)
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = self.create()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    def test_invalid_ip_is_rejected_before_writing_a_token(self):
        result = self.create(address="not-an-ip")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.root.exists())

    def test_invalid_existing_policy_is_reported_without_overwriting_it(self):
        self.root.mkdir()
        path = self.root / "gateway-policies.json"
        path.write_text("[]", encoding="utf-8")
        result = self.create()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), "[]")
        self.assertEqual(list(self.root.iterdir()), [path])
