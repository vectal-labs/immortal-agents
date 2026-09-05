"""Readiness checks against controlled child processes, without network probes."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from immortal.core import ready


POPEN = subprocess.Popen
PROBE = """
import sys
import time
from pathlib import Path

directory = Path(sys.argv[1])
(directory / 'started').touch()
while not (directory / 'release').exists():
    time.sleep(0.01)
raise SystemExit(int(sys.argv[2]))
"""


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.processes = []
        self.exit_code = 0
        self.now = 100.0
        self.enterContext(mock.patch.multiple(
            ready, _process=None, _started=0.0, _checked=None, _result=False, _discard_result=False
        ))
        self.enterContext(mock.patch.object(ready, "log"))
        self.enterContext(mock.patch.object(ready.time, "monotonic", side_effect=lambda: self.now))
        self.spawn = self.enterContext(mock.patch.object(
            ready.subprocess, "Popen", side_effect=self.spawn_probe
        ))
        self.addCleanup(self.stop_processes)

    def spawn_probe(self, command, **kwargs):
        directory = self.directory / str(len(self.processes))
        directory.mkdir()
        process = POPEN(
            [sys.executable, "-c", PROBE, str(directory), str(self.exit_code)], **kwargs
        )
        self.processes.append((process, directory))
        return process

    def stop_processes(self):
        for process, _ in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)

    def wait_for_start(self):
        process, directory = self.processes[-1]
        deadline = time.perf_counter() + 3
        while not (directory / "started").exists() and time.perf_counter() < deadline:
            self.assertIsNone(process.poll(), "controlled probe exited before starting")
            time.sleep(0.005)
        self.assertTrue((directory / "started").exists(), "controlled probe never started")
        return process

    def finish_probe(self):
        process, directory = self.processes[-1]
        (directory / "release").touch()
        return process.wait(timeout=3)

    def test_pending_probe_returns_promptly_without_duplicates(self):
        started = time.perf_counter()
        self.assertFalse(ready.check())
        self.assertLess(time.perf_counter() - started, 1)
        process = self.wait_for_start()
        for _ in range(10):
            self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 1)
        self.assertIsNone(process.poll())

    def test_success_is_cached_until_a_fresh_probe_is_required(self):
        self.assertFalse(ready.check())
        self.assertEqual(self.finish_probe(), 0)
        self.assertTrue(ready.check())
        self.assertTrue(ready.check())
        self.assertEqual(self.spawn.call_count, 1)

        self.now += ready.PROBE_CACHE_SECS + 0.01
        self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 2)

    def test_failure_remains_false_and_a_later_probe_can_succeed(self):
        self.exit_code = 1
        self.assertFalse(ready.check())
        self.assertEqual(self.finish_probe(), 1)
        self.assertFalse(ready.check())
        self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 1)

        self.now += ready.PROBE_CACHE_SECS + 0.01
        self.exit_code = 0
        self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(self.finish_probe(), 0)
        self.assertTrue(ready.check())

    def test_timeout_kills_the_hung_probe(self):
        self.assertFalse(ready.check())
        process = self.wait_for_start()
        self.now += ready.PROBE_TIMEOUT_SECS
        started = time.perf_counter()
        self.assertFalse(ready.check())
        self.assertLess(time.perf_counter() - started, 1)
        self.assertLess(process.wait(timeout=1), 0)
        self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 1)

    def test_invalidation_kills_pending_probe_before_starting_another(self):
        self.assertFalse(ready.check())
        process = self.wait_for_start()
        ready.invalidate()
        self.assertIsNotNone(process.poll())
        self.assertLess(process.returncode, 0)
        self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 2)

    def test_delayed_cancellation_does_not_block_or_spawn_another_probe(self):
        self.assertFalse(ready.check())
        process = self.wait_for_start()
        self.now += ready.PROBE_TIMEOUT_SECS
        with mock.patch.object(process, "kill"), mock.patch.object(
            process, "wait", side_effect=AssertionError("check must never wait for exit")
        ):
            self.assertFalse(ready.check())
            self.now += ready.PROBE_CACHE_SECS + 0.01
            self.assertFalse(ready.check())
            self.assertEqual(self.spawn.call_count, 1)
            self.assertIsNone(process.poll())
        process.kill()
        process.wait(timeout=1)
        self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 2)

    def test_invalidation_wait_is_bounded_when_child_does_not_exit(self):
        self.assertFalse(ready.check())
        process = self.wait_for_start()
        # Keep the real child alive long enough to exercise the actual wait timeout.
        with mock.patch.object(process, "kill"):
            started = time.perf_counter()
            ready.invalidate()
            self.assertLess(time.perf_counter() - started, 1)
            self.assertFalse(ready.check())
            self.assertEqual(self.spawn.call_count, 1)
            self.assertIsNone(process.poll())

    def test_exit_race_after_timeout_cannot_turn_stale_success_into_readiness(self):
        self.assertFalse(ready.check())
        process = self.wait_for_start()
        self.now += ready.PROBE_TIMEOUT_SECS

        def exit_before_kill():
            self.assertEqual(self.finish_probe(), 0)
            raise ProcessLookupError("child exited before signal")

        with mock.patch.object(process, "kill", side_effect=exit_before_kill):
            self.assertFalse(ready.check())
        self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 1)

    def test_invalidation_discards_an_unread_success_from_the_old_connection(self):
        self.assertFalse(ready.check())
        self.assertEqual(self.finish_probe(), 0)
        ready.invalidate()

        self.exit_code = 1
        self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(self.finish_probe(), 1)
        self.assertFalse(ready.check())

    def test_invalidation_discards_cached_success(self):
        self.assertFalse(ready.check())
        self.assertEqual(self.finish_probe(), 0)
        self.assertTrue(ready.check())
        ready.invalidate()
        self.assertFalse(ready.check())
        self.assertEqual(self.spawn.call_count, 2)

    def test_startup_error_cannot_reuse_expired_success(self):
        self.assertFalse(ready.check())
        self.assertEqual(self.finish_probe(), 0)
        self.assertTrue(ready.check())
        self.now += ready.PROBE_CACHE_SECS + 0.01
        with mock.patch.object(ready.subprocess, "Popen", side_effect=OSError("cannot spawn")) as spawn:
            self.assertFalse(ready.check())
            self.assertFalse(ready.check())
            spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
