"""State survives interrupted saves and malformed files at watcher startup."""

import json
import os
from contextlib import ExitStack
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from immortal.core import logbook, revive_state


class StateTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.directory / "state.json"
        stack.enter_context(mock.patch.object(logbook, "STATE_DIR", self.directory))
        stack.enter_context(mock.patch.object(logbook, "STATE_PATH", self.path))
        stack.enter_context(mock.patch.object(logbook, "LOG_PATH", self.directory / "watcher.log"))

    def run_process(self, script):
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "WATCHER_STATE_DIR": str(self.directory), "WATCHER_ONCE": "1"},
            capture_output=True, text=True, timeout=10,
        )

    def test_missing_file_returns_independent_defaults(self):
        state = logbook.load_state()
        self.assertEqual(state, {"online": None, "outage_started_at": None, "revived": {}})
        state["revived"]["first"] = {}
        self.assertEqual(logbook.load_state()["revived"], {})

    def test_watcher_starts_with_malformed_state_without_retrying(self):
        script = """
import watcher
watcher.probe = lambda: True
watcher.ready.wait_for_apis = lambda: None
watcher.revive.revive_pass = lambda *args: 0
watcher.revive.telemetry.heartbeat = lambda: None
def no_retry(_):
    raise AssertionError("watcher failed its first tick")
watcher.time.sleep = no_retry
watcher.loop()
"""
        cases = (
            b'{"online":', b'\xff', b'[]', b'null', b'42', b'"state"',
            b'{"online": "false", "revived": []}',
            b'{"online": false, "outage_started_at": "invalid"}',
            b'{"recheck": [1]}', b'{"recheck": {"next_at": "invalid"}}',
            b'{"provider_check_next_at": "invalid"}',
            b'{"online": false, "outage_started_at": "2026-09-04T12:00:00"}',
        )
        for content in cases:
            with self.subTest(content=content):
                self.path.write_bytes(content)
                result = self.run_process(script)
                self.assertEqual(result.returncode, 0, result.stderr)
                state = json.loads(self.path.read_text())
                self.assertIs(state["online"], True)
                self.assertIsInstance(state["revived"], dict)

    def test_valid_revive_history_survives_other_invalid_entries(self):
        valid = {"tries": 3, "at": "2026-09-04T12:00:00Z", "error_at": "seen"}
        seen = {"error_at": "seen", "at": None}
        state = {
            "revived": {
                "valid": valid, "seen": seen, "list": [], "null": None,
                "bad_tries": {"tries": "3"}, "negative": {"tries": -1},
                "boolean": {"tries": True},
            },
            "recheck": {"next_at": "bad"},
            "extra": "preserved",
        }
        self.path.write_text(json.dumps(state))
        loaded = logbook.load_state()
        self.assertEqual(loaded["revived"], {"valid": valid, "seen": seen})
        self.assertFalse(revive_state.may_revive(loaded, "valid", "first"))
        self.assertFalse(revive_state.may_revive(loaded, "valid", "recheck"))
        self.assertTrue(revive_state.may_revive(loaded, "seen", "first"))
        self.assertIsNone(loaded["recheck"])
        self.assertEqual(loaded["extra"], "preserved")

    def test_valid_state_round_trips(self):
        stamp = "2026-09-04T12:00:00Z"
        state = {
            "online": False, "outage_started_at": stamp,
            "revived": {"target": {"tries": 2, "at": stamp}},
            "recheck": {"loss_at": stamp, "next_at": stamp, "until": stamp, "duration": 120.5},
            "provider_check_next_at": stamp,
        }
        logbook.save_state(state)
        self.assertEqual(logbook.load_state(), state)
        self.assertEqual(list(self.directory.glob(".state-*.tmp")), [])

    def test_interrupted_write_keeps_previous_state(self):
        original = {"revived": {"target": {"tries": 3}}}
        self.path.write_text(json.dumps(original))
        previous_bytes = self.path.read_bytes()
        result = self.run_process("""
import os
from immortal.core import logbook
def interrupted_dump(state, fh, **kwargs):
    fh.write('{"revived":')
    fh.flush()
    os._exit(23)
logbook.json.dump = interrupted_dump
logbook.save_state({"revived": {}})
""")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self.path.read_bytes(), previous_bytes)
        self.assertEqual(logbook.load_state()["revived"], original["revived"])

    def test_failed_save_keeps_previous_state_and_cleans_temporary_file(self):
        original = {"revived": {"target": {"tries": 3}}}
        self.path.write_text(json.dumps(original))
        previous_bytes = self.path.read_bytes()
        failures = (
            mock.patch.object(logbook.os, "fsync", side_effect=OSError("sync failed")),
            mock.patch.object(logbook.os, "replace", side_effect=OSError("replace failed")),
        )
        for failure in failures:
            with self.subTest(failure=failure), failure:
                with self.assertRaises(OSError):
                    logbook.save_state({"revived": {}})
            self.assertEqual(self.path.read_bytes(), previous_bytes)
            self.assertEqual(list(self.directory.glob(".state-*.tmp")), [])
        with self.assertRaises(TypeError):
            logbook.save_state({"invalid": object()})
        self.assertEqual(self.path.read_bytes(), previous_bytes)
        self.assertEqual(list(self.directory.glob(".state-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
