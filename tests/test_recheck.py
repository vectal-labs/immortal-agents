#!/usr/bin/env python3
"""Experiment 0009 follow-ups: API readiness gate and bounded re-revive."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import host_bb
import ready
import watcher

NOW = datetime(2026, 9, 2, 12, 23, 4, tzinfo=timezone.utc)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


class ReadyTests(unittest.TestCase):
    def test_waits_until_every_api_host_resolves(self):
        sleep = mock.Mock()
        with mock.patch.object(ready, "log"), mock.patch.object(
            ready, "unresolved", side_effect=[["api.anthropic.com"], ["api.anthropic.com"], []]
        ):
            self.assertTrue(ready.wait_for_apis(sleep=sleep))
        self.assertEqual(sleep.call_count, 2)

    def test_gives_up_after_the_cap(self):
        clock = iter(range(0, 10_000, 100))
        with mock.patch.object(ready, "log"), mock.patch.object(
            ready, "unresolved", return_value=["api.anthropic.com"]
        ), mock.patch.object(ready.time, "monotonic", side_effect=lambda: next(clock)):
            self.assertFalse(ready.wait_for_apis(max_wait=300, sleep=mock.Mock()))

    def test_resolves_uses_the_system_resolver(self):
        with mock.patch.object(ready.socket, "getaddrinfo", side_effect=OSError("ENOTFOUND")):
            self.assertFalse(ready.resolves("api.anthropic.com"))


class RecheckTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(watcher, "log"))
        self.enterContext(mock.patch.object(watcher, "save_state"))
        self.enterContext(mock.patch.object(watcher.ready, "wait_for_apis", return_value=True))
        self.enterContext(mock.patch.object(watcher.notify, "notify_revive", return_value=True))
        for host in watcher.TERMINAL_HOSTS:
            self.enterContext(mock.patch.object(host, "available", return_value=False))
        self.dead = {"id": "thr_a", "status": "error", "providerId": "claude-code", "title": "A"}
        self.enterContext(mock.patch.object(host_bb, "thread_events", return_value=[]))
        self.enterContext(mock.patch.object(host_bb, "evaluate", return_value=("resume", ["x"], {})))
        self.resume = self.enterContext(mock.patch.object(host_bb, "resume", return_value=(True, "ok")))
        self.loss, self.recovery = iso(NOW - timedelta(minutes=10)), iso(NOW)

    def test_recovery_waits_for_apis_then_arms_recheck_after_a_revive(self):
        state = {"resumed": {}}
        with mock.patch.object(host_bb, "list_error_threads", return_value=[self.dead]):
            watcher.on_recovery(state, self.loss, self.recovery, 600)
        watcher.ready.wait_for_apis.assert_called_once()
        self.assertEqual(state["resumed"][f"bb:thr_a:{self.loss}"]["tries"], 1)
        self.assertEqual(state["recheck"]["loss_at"], self.loss)

    def test_no_revive_means_no_recheck(self):
        state = {"resumed": {}}
        with mock.patch.object(host_bb, "list_error_threads", return_value=[]):
            watcher.on_recovery(state, self.loss, self.recovery, 600)
        self.assertNotIn("recheck", state)

    def test_recheck_revives_again_up_to_the_cap(self):
        state = {"resumed": {}}
        with mock.patch.object(host_bb, "list_error_threads", return_value=[self.dead]):
            watcher.on_recovery(state, self.loss, self.recovery, 600)
            for _ in range(5):
                state["recheck"]["next_at"] = iso(NOW - timedelta(minutes=1))
                watcher.run_recheck(state)
        self.assertEqual(self.resume.call_count, watcher.MAX_REVIVES)
        self.assertEqual(state["resumed"][f"bb:thr_a:{self.loss}"]["tries"], watcher.MAX_REVIVES)

    def test_recheck_never_wakes_a_target_it_did_not_revive(self):
        state = {"resumed": {}, "recheck": {"loss_at": self.loss, "duration": 600,
                                            "next_at": iso(NOW), "until": iso(NOW + timedelta(hours=1))}}
        with mock.patch.object(host_bb, "list_error_threads", return_value=[self.dead]):
            watcher.run_recheck(state)
        self.resume.assert_not_called()

    def test_recheck_expires(self):
        state = {"resumed": {}, "recheck": {"loss_at": self.loss, "duration": 600,
                                            "next_at": iso(NOW), "until": iso(NOW - timedelta(hours=1))}}
        with mock.patch.object(host_bb, "list_error_threads") as listing:
            watcher.run_recheck(state)
        listing.assert_not_called()
        self.assertIsNone(state["recheck"])

    def test_recheck_waits_before_next_at(self):
        state = {"resumed": {}, "recheck": {"loss_at": self.loss, "duration": 600,
                                            "next_at": iso(datetime.now(timezone.utc) + timedelta(hours=1)),
                                            "until": iso(datetime.now(timezone.utc) + timedelta(hours=2))}}
        with mock.patch.object(host_bb, "list_error_threads") as listing:
            watcher.run_recheck(state)
        listing.assert_not_called()


class OutageWindowTests(unittest.TestCase):
    """Experiment 0013: the agents' outage ends when their APIs resolve, not
    when the captive probe passes. Replays the real 2026-09-03 timestamps."""

    LOSS = "2026-09-03T09:08:36.920310Z"
    PROBE_ONLINE = "2026-09-03T09:28:24.340210Z"
    APIS_READY = "2026-09-03T09:30:05.188265Z"  # 101s later; the thread died at 09:28:47
    EVENTS = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "bb_thr_latedns01_events.json").read_text()
    )

    def setUp(self):
        self.enterContext(mock.patch.object(watcher, "log"))
        self.enterContext(mock.patch.object(watcher, "save_state"))
        self.enterContext(mock.patch.object(watcher.notify, "notify_revive", return_value=True))
        for host in watcher.TERMINAL_HOSTS:
            self.enterContext(mock.patch.object(host, "available", return_value=False))
        dead = {"id": "thr_latedns01", "status": "error", "providerId": "claude-code", "title": "tech debt reduction"}
        self.enterContext(mock.patch.object(host_bb, "list_error_threads", return_value=[dead]))
        self.enterContext(mock.patch.object(host_bb, "thread_events", return_value=self.EVENTS))
        self.resume = self.enterContext(mock.patch.object(host_bb, "resume", return_value=(True, "ok")))
        # The DNS wait returns once the APIs resolve; the clock reads that moment.
        self.enterContext(mock.patch.object(watcher.ready, "wait_for_apis", return_value=True))
        self.enterContext(mock.patch.object(watcher, "now_iso", return_value=self.APIS_READY))

    def test_thread_that_died_after_the_probe_but_before_dns_is_revived(self):
        state = {"resumed": {}}
        watcher.on_recovery(state, self.LOSS, self.PROBE_ONLINE, 1187)
        self.resume.assert_called_once_with("thr_latedns01")
        self.assertEqual(state["last_api_ready_at"], self.APIS_READY)
        self.assertEqual(state["resumed"][f"bb:thr_latedns01:{self.LOSS}"]["tries"], 1)
        self.assertEqual(state["recheck"]["loss_at"], self.LOSS)


if __name__ == "__main__":
    unittest.main()
