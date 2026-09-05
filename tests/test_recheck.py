#!/usr/bin/env python3
"""Experiment 0009 follow-ups: API readiness gate and bounded re-revive."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from immortal.detect import bb as detect_bb
from immortal.hosts import bb as host_bb
from immortal.core import ready
import revive
from immortal.core import revive_state
import watcher
from support import isolate_state

NOW = datetime(2026, 9, 2, 12, 23, 4, tzinfo=timezone.utc)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def bb_target(thread, events=None):
    error = host_bb.last_error(events or [])
    target = {
        "ref": thread["id"],
        "id": thread["id"],
        "cwd": None,
        "title": thread.get("title"),
        "harness_hint": thread.get("providerId"),
        "status": thread.get("status"),
        "error_at": error["at"].isoformat() if error and error["at"] else None,
    }
    return target, error["detail"] if error else None


class RecheckTests(unittest.TestCase):
    def setUp(self):
        isolate_state(self)
        self.enterContext(mock.patch.object(watcher, "log"))
        self.enterContext(mock.patch.object(revive, "log"))
        self.enterContext(mock.patch.object(watcher, "save_state"))
        self.enterContext(mock.patch.object(revive_state, "save_state"))
        self.enterContext(mock.patch.object(ready, "check", return_value=True))
        self.enterContext(mock.patch.object(revive.notify, "notify_revive", return_value=True))
        for host in (candidate for candidate in revive.HOSTS if candidate is not host_bb):
            self.enterContext(mock.patch.object(host, "available", return_value=False))
        self.enterContext(mock.patch.object(host_bb, "available", return_value=True))
        self.dead, screen = bb_target(
            {"id": "thr_a", "status": "error", "providerId": "claude-code", "title": "A"}
        )
        self.enterContext(mock.patch.object(host_bb, "read_screen", return_value=screen))
        self.enterContext(mock.patch.object(detect_bb, "evaluate", return_value=("resume", ["x"], {})))
        self.resume = self.enterContext(mock.patch.object(host_bb, "resume", return_value="sent"))
        self.loss, self.recovery = iso(NOW - timedelta(minutes=10)), iso(NOW)

    def test_recovery_waits_for_apis_then_arms_recheck_after_a_revive(self):
        state = {"revived": {}}
        with mock.patch.object(host_bb, "list_targets", return_value=[self.dead]):
            watcher.on_recovery(state, self.loss, self.recovery, 600)
            revive.run_recheck(state)
        ready.check.assert_called_once()
        self.assertEqual(state["revived"][f"bb:thr_a:{self.loss}"]["tries"], 1)
        self.assertEqual(state["recheck"]["loss_at"], self.loss)

    def test_recovery_arms_recheck_even_when_nothing_was_revived(self):
        state = {"revived": {}}
        with mock.patch.object(host_bb, "list_targets", return_value=[]):
            watcher.on_recovery(state, self.loss, self.recovery, 600)
            revive.run_recheck(state)
        self.assertEqual(state["recheck"]["loss_at"], self.loss)

    def test_recheck_revives_again_up_to_the_cap(self):
        state = {"revived": {}}
        with mock.patch.object(host_bb, "list_targets", return_value=[self.dead]):
            watcher.on_recovery(state, self.loss, self.recovery, 600)
            revive.run_recheck(state)
            for i in range(5):
                detect_bb.evaluate.return_value = ("resume", ["x"], {"error_at": str(i)})
                state["recheck"]["next_at"] = iso(NOW - timedelta(minutes=1))
                revive.run_recheck(state)
        self.assertEqual(self.resume.call_count, revive.MAX_REVIVES)
        self.assertEqual(state["revived"][f"bb:thr_a:{self.loss}"]["tries"], revive.MAX_REVIVES)

    def test_recheck_wakes_a_target_it_did_not_revive(self):
        now = datetime.now(timezone.utc)
        state = {"revived": {}, "recheck": {"loss_at": self.loss, "duration": 600,
                                            "next_at": iso(now - timedelta(seconds=1)),
                                            "until": iso(now + timedelta(hours=1))}}
        with mock.patch.object(host_bb, "list_targets", return_value=[self.dead]):
            revive.run_recheck(state)
        self.resume.assert_called_once()
        self.assertEqual(self.resume.call_args.args[0]["ref"], "thr_a")

    def test_may_revive_recheck_allows_zero_tries(self):
        self.assertTrue(revive_state.may_revive({"revived": {}}, "x", "recheck"))

    def test_revive_pass_sets_recovery_at_from_state(self):
        seen = []

        def capture(target, screen, window):
            seen.append(target.get("recovery_at"))
            return "skip", ["x"], {}

        ready_at = "2026-09-02T12:20:00Z"
        state = {"revived": {}, "last_api_ready_at": ready_at}
        with mock.patch.object(host_bb, "list_targets", return_value=[self.dead]), mock.patch.object(
            detect_bb, "evaluate", side_effect=capture
        ):
            revive.revive_pass(state, (self.loss, self.recovery), "recheck")
        self.assertEqual(seen, [ready_at])

    def test_recheck_expires(self):
        state = {"revived": {}, "recheck": {"loss_at": self.loss, "duration": 600,
                                            "next_at": iso(NOW), "until": iso(NOW - timedelta(hours=1))}}
        with mock.patch.object(host_bb, "list_targets") as listing:
            revive.run_recheck(state)
        listing.assert_not_called()
        self.assertIsNone(state["recheck"])

    def test_recheck_waits_before_next_at(self):
        state = {"revived": {}, "recheck": {"loss_at": self.loss, "duration": 600,
                                            "next_at": iso(datetime.now(timezone.utc) + timedelta(hours=1)),
                                            "until": iso(datetime.now(timezone.utc) + timedelta(hours=2))}}
        with mock.patch.object(host_bb, "list_targets") as listing:
            revive.run_recheck(state)
        listing.assert_not_called()


class OutageWindowTests(unittest.TestCase):
    """Experiment 0013: the agents' outage ends when their APIs resolve, not
    when the captive probe passes. Replays the real 2026-09-03 timestamps."""

    LOSS = "2026-09-03T09:08:36.920310Z"
    PROBE_ONLINE = "2026-09-03T09:28:24.340210Z"
    APIS_READY = "2026-09-03T09:30:05.188265Z"  # 101s later; the thread died at 09:28:47
    EVENTS = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "bb_thr_gmgh8s7j9w_events.json").read_text()
    )

    def setUp(self):
        isolate_state(self)
        self.enterContext(mock.patch.object(watcher, "log"))
        self.enterContext(mock.patch.object(revive, "log"))
        self.enterContext(mock.patch.object(watcher, "save_state"))
        self.enterContext(mock.patch.object(revive_state, "save_state"))
        self.enterContext(mock.patch.object(revive.notify, "notify_revive", return_value=True))
        for host in (candidate for candidate in revive.HOSTS if candidate is not host_bb):
            self.enterContext(mock.patch.object(host, "available", return_value=False))
        self.enterContext(mock.patch.object(host_bb, "available", return_value=True))
        dead, screen = bb_target(
            {"id": "thr_gmgh8s7j9w", "status": "error", "providerId": "claude-code", "title": "tech debt reduction"},
            self.EVENTS,
        )
        self.enterContext(mock.patch.object(host_bb, "list_targets", return_value=[dead]))
        self.enterContext(mock.patch.object(host_bb, "read_screen", return_value=screen))
        self.resume = self.enterContext(mock.patch.object(host_bb, "resume", return_value="sent"))
        # The DNS wait returns once the APIs resolve; the clock reads that moment.
        self.enterContext(mock.patch.object(ready, "check", return_value=True))
        self.enterContext(mock.patch.object(revive, "now_iso", return_value=self.APIS_READY))

    def test_thread_that_died_after_the_probe_but_before_dns_is_revived(self):
        state = {"revived": {}}
        watcher.on_recovery(state, self.LOSS, self.PROBE_ONLINE, 1187)
        revive.run_recheck(state)
        self.resume.assert_called_once()
        self.assertEqual(self.resume.call_args.args[0]["ref"], "thr_gmgh8s7j9w")
        self.assertEqual(state["last_api_ready_at"], self.APIS_READY)
        self.assertEqual(state["revived"][f"bb:thr_gmgh8s7j9w:{self.LOSS}"]["tries"], 1)
        self.assertEqual(state["recheck"]["loss_at"], self.LOSS)


class OutageStartTests(unittest.TestCase):
    """2026-09-03, thr_vp7hipzyr6: the Mac slept for 4 minutes and the agent
    died 1.3s before the first failed probe on wake. The window opens at the
    last probe that passed."""

    LAST_GOOD = "2026-09-03T19:14:58.401757Z"
    DETECTED = "2026-09-03T19:19:04.904731Z"

    def tick_offline(self, state):
        with mock.patch.object(watcher, "probe", return_value=False), mock.patch.object(
            watcher, "log"
        ) as log, mock.patch.object(watcher, "save_state"), mock.patch.object(
            watcher, "load_state", return_value=state
        ), mock.patch.object(watcher, "now_iso", return_value=self.DETECTED), mock.patch.dict(
            watcher.os.environ, {"WATCHER_ONCE": "1"}
        ):
            watcher.loop()
        return [c for c in log.call_args_list if c.args[0] == "state_change"][0].kwargs

    def test_window_opens_at_the_last_good_probe(self):
        state = {"online": True, "last_probe_at": self.LAST_GOOD, "revived": {}}
        change = self.tick_offline(state)
        self.assertEqual(state["outage_started_at"], self.LAST_GOOD)
        self.assertEqual(state["last_loss_at"], self.LAST_GOOD)
        self.assertEqual((change["at"], change["detected_at"]), (self.LAST_GOOD, self.DETECTED))

    def test_fresh_watcher_opens_the_window_when_it_first_fails(self):
        state = {"online": None, "last_probe_at": None, "revived": {}}
        self.tick_offline(state)
        self.assertEqual(state["outage_started_at"], self.DETECTED)


if __name__ == "__main__":
    unittest.main()
