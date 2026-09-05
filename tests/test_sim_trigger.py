#!/usr/bin/env python3
"""Tests for the simulated-outage trigger."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import logbook
import watcher


ROOT = Path(__file__).resolve().parent.parent


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


class SimulatedProbeTests(unittest.TestCase):
    def watcher_paths(self, state_dir):
        # The log lives in logbook; watcher keeps only the flag path.
        stack = contextlib.ExitStack()
        stack.enter_context(
            mock.patch.multiple(logbook, STATE_DIR=state_dir, LOG_PATH=state_dir / "watcher.log")
        )
        stack.enter_context(
            mock.patch.multiple(
                watcher,
                STATE_DIR=state_dir,
                SIMULATED_OUTAGE_PATH=state_dir / "simulated_outage.json",
            )
        )
        return stack

    def test_flag_makes_probe_offline_without_network_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            now = datetime.now(timezone.utc)
            flag = {
                "offline": True,
                "since": iso(now),
                "expires_at": iso(now + timedelta(minutes=10)),
            }
            (state_dir / "simulated_outage.json").write_text(json.dumps(flag))

            with self.watcher_paths(state_dir), mock.patch(
                "watcher.urllib.request.urlopen"
            ) as urlopen:
                self.assertFalse(watcher.probe())

            urlopen.assert_not_called()
            row = json.loads((state_dir / "watcher.log").read_text().splitlines()[-1])
            self.assertEqual(row["event"], "probe")
            self.assertFalse(row["online"])
            self.assertTrue(row["simulated"])

    def test_expired_flag_is_deleted_and_real_probe_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            now = datetime.now(timezone.utc)
            flag_path = state_dir / "simulated_outage.json"
            flag_path.write_text(
                json.dumps(
                    {
                        "offline": True,
                        "since": iso(now - timedelta(minutes=2)),
                        "expires_at": iso(now - timedelta(minutes=1)),
                    }
                )
            )
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = b"Success"

            with self.watcher_paths(state_dir), mock.patch(
                "watcher.urllib.request.urlopen"
            ) as urlopen:
                urlopen.return_value.__enter__.return_value = response
                self.assertTrue(watcher.probe())

            urlopen.assert_called_once()
            self.assertFalse(flag_path.exists())
            events = [
                json.loads(line)["event"]
                for line in (state_dir / "watcher.log").read_text().splitlines()
            ]
            self.assertEqual(events, ["sim_expired", "probe"])


class SimCliTests(unittest.TestCase):
    def run_sim(self, state_dir, *args):
        env = os.environ.copy()
        env["WATCHER_STATE_DIR"] = str(state_dir)
        return subprocess.run(
            [sys.executable, str(ROOT / "sim.py"), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    def test_on_status_off_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            enabled = json.loads(self.run_sim(state_dir, "on", "--minutes", "3"))
            self.assertTrue(enabled["offline"])
            flag_path = state_dir / "simulated_outage.json"
            proxy_path = state_dir / "proxy_dead.json"
            self.assertTrue(flag_path.exists())
            self.assertTrue(proxy_path.exists())
            proxy_dead = json.loads(proxy_path.read_text())
            self.assertEqual(proxy_dead["expires_at"], enabled["expires_at"])

            status = json.loads(self.run_sim(state_dir, "status"))
            self.assertEqual(status["watcher"], enabled)
            self.assertEqual(status["proxies"], proxy_dead)

            self.assertEqual(self.run_sim(state_dir, "off"), "off")
            self.assertFalse(flag_path.exists())
            self.assertFalse(proxy_path.exists())
            self.assertEqual(
                json.loads(self.run_sim(state_dir, "status")),
                {"watcher": "off", "proxies": "alive"},
            )


if __name__ == "__main__":
    unittest.main()
