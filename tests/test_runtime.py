"""Real watcher processes, with only network/recovery and launchd boundaries replaced."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from immortal.core import runtime, updater, updates


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / "immortal", self.repo / "immortal",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(source / "watcher.py", self.repo / "watcher.py")
        (self.repo / "revive.py").write_text(
            "from types import SimpleNamespace\n"
            "ready = SimpleNamespace(check_internet=lambda url: {'online': False})\n"
            "def run_provider_check(state): return None\n")
        self.version_file = self.repo / "immortal/__init__.py"
        self.version_file.write_text('__version__ = "0.1.0"\n')
        self.env = {**os.environ, "WATCHER_STATE_DIR": str(self.state),
                    "PYTHONDONTWRITEBYTECODE": "1", "GIT_AUTHOR_NAME": "Test",
                    "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "Test",
                    "GIT_COMMITTER_EMAIL": "test@example.com"}
        self.env.pop("WATCHER_ONCE", None)
        self.git("init", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-m", "fixture")
        self.process = None
        self.addCleanup(self.stop)

    def git(self, *args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.repo), *args],
                              env=self.env, capture_output=True, text=True, check=True).stdout.strip()

    def start(self):
        self.process = subprocess.Popen([sys.executable, "watcher.py"], cwd=self.repo, env=self.env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail(self.process.stderr.read().decode())
            if runtime.read(self.state, self.process.pid):
                return
            time.sleep(0.02)
        self.fail("Watcher did not record startup")

    def stop(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.wait(timeout=5)
            self.process.stderr.close()

    def status(self):
        return runtime.status(self.repo, self.state, self.process.pid, check_github=False)

    def test_real_process_reports_old_version_until_verified_restart(self):
        self.start()
        self.assertTrue(self.status()[1])
        self.version_file.write_text('__version__ = "0.1.1"\n')
        self.git("add", ".")
        self.git("commit", "-m", "new version")
        output, active = self.status()
        self.assertFalse(active)
        self.assertIn("Checkout: 0.1.1", output)
        self.assertIn("Running: 0.1.0", output)
        self.assertIn("Active: NO", output)

        def restart(*args):
            self.assertEqual(args, ("kickstart", "-k", f"gui/{os.getuid()}/{updater.WATCHER_LABEL}"))
            self.stop()
            self.start()

        old_pid = self.process.pid
        with mock.patch.object(updater, "process_id", side_effect=lambda: self.process.pid), \
                mock.patch.object(updates, "launchctl", side_effect=restart):
            updater.restart_watcher(self.state, self.repo)
        self.assertNotEqual(old_pid, self.process.pid)
        output, active = self.status()
        self.assertTrue(active)
        self.assertIn("Running: 0.1.1", output)
        self.assertIn("Active: YES", output)
        self.assertTrue((self.state / "state.json").is_file())

    def test_uncommitted_edit_without_version_bump_is_detected(self):
        self.start()
        with (self.repo / "revive.py").open("a") as stream:
            stream.write("\n# changed recovery code\n")
        self.assertFalse(self.status()[1])

    def test_missing_corrupt_or_other_process_record_is_unknown(self):
        self.start()
        path = self.state / "runtime.json"
        for content in ("{}", "[]", "null", "{bad", json.dumps({"pid": self.process.pid + 1})):
            path.write_text(content)
            output, active = self.status()
            self.assertFalse(active)
            self.assertIn("Running version: unknown", output)

    def test_offline_github_never_claims_remote_match(self):
        self.start()
        with mock.patch.object(runtime, "github_head", side_effect=OSError("offline")):
            output, active = runtime.status(self.repo, self.state, self.process.pid)
        self.assertTrue(active)
        self.assertIn("GitHub main: unknown", output)

    def test_github_ahead_is_distinct_from_local_activation(self):
        self.start()
        with mock.patch.object(runtime, "github_head", return_value="a" * 40):
            output, active = runtime.status(self.repo, self.state, self.process.pid)
        self.assertTrue(active)
        self.assertIn("differs from checkout", output)

    def test_new_pid_and_log_with_wrong_loaded_code_do_not_confirm_restart(self):
        self.start()
        self.version_file.write_text('__version__ = "0.1.1"\n')
        with mock.patch.object(updater, "process_id", side_effect=[999999, self.process.pid, self.process.pid]), \
                mock.patch.object(updates, "launchctl"), \
                mock.patch.object(updater, "started", return_value=True), \
                mock.patch.object(updater.time, "sleep"), \
                mock.patch.object(updater.time, "monotonic", side_effect=[0, 0, 11]):
            with self.assertRaisesRegex(updates.UpdateError, "not verified"):
                updater.restart_watcher(self.state, self.repo)


if __name__ == "__main__":
    unittest.main()
