#!/usr/bin/env python3
"""Tests for the bb host (ADR 0036) against real recorded bb events."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import host_bb
import watcher

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Real death on 2026-08-31: Claude Code in bb, internet lost (ENOTFOUND).
DEAD_EVENTS = json.loads((FIXTURES / "bb_thr_claudea01_events.json").read_text())
# Real non-network error: "Not logged in".
LOGIN_EVENTS = json.loads((FIXTURES / "bb_thr_login0001_events.json").read_text())
DEATH_AT = datetime.fromtimestamp(1788185043065 / 1000, tz=timezone.utc)
# Experiment 0013 (2026-09-03): Claude Code in bb died 20s AFTER the captive
# probe passed, inside the stale-DNS gap, before api.anthropic.com resolved.
LATE_DEATH_EVENTS = json.loads((FIXTURES / "bb_thr_latedns01_events.json").read_text())
EXP13_LOSS = "2026-09-03T09:08:36.920310Z"
EXP13_PROBE_ONLINE = "2026-09-03T09:28:24.340210Z"
EXP13_APIS_READY = "2026-09-03T09:30:05.188265Z"


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def thread(thread_id, status="error", provider="claude-code"):
    return {"id": thread_id, "status": status, "providerId": provider, "title": "t"}


class EvaluateTests(unittest.TestCase):
    def window(self, minutes_before=10, minutes_after=10):
        return iso(DEATH_AT - timedelta(minutes=minutes_before)), iso(
            DEATH_AT + timedelta(minutes=minutes_after)
        )

    def test_real_network_death_inside_outage_resumes(self):
        loss, recovery = self.window()
        decision, reasons, info = host_bb.evaluate(
            thread("thr_claudea01"), DEAD_EVENTS, loss, recovery
        )
        self.assertEqual(decision, "resume")
        self.assertIn("all_three_agree", reasons)
        self.assertIn("ENOTFOUND", info["error_detail"])

    def test_retry_notices_are_not_the_final_error(self):
        err = host_bb.last_error(DEAD_EVENTS)
        self.assertNotIn("retry", err["detail"].lower())

    def test_death_outside_outage_window_skips(self):
        loss = iso(DEATH_AT + timedelta(hours=1))
        recovery = iso(DEATH_AT + timedelta(hours=2))
        decision, reasons, _ = host_bb.evaluate(
            thread("thr_claudea01"), DEAD_EVENTS, loss, recovery
        )
        self.assertEqual(decision, "skip")
        self.assertIn("timing_miss", reasons)

    def test_death_in_the_stale_dns_gap_is_inside_the_outage(self):
        # A window that ends at the captive probe misses it (the 0013 bug)...
        decision, reasons, info = host_bb.evaluate(
            thread("thr_latedns01"), LATE_DEATH_EVENTS, EXP13_LOSS, EXP13_PROBE_ONLINE
        )
        self.assertEqual((decision, reasons[-1]), ("skip", "timing_miss"))
        self.assertGreater(host_bb.parse_ts(info["error_at"]), host_bb.parse_ts(EXP13_PROBE_ONLINE))
        # ...a window that ends when the APIs resolve catches it.
        decision, reasons, _ = host_bb.evaluate(
            thread("thr_latedns01"), LATE_DEATH_EVENTS, EXP13_LOSS, EXP13_APIS_READY
        )
        self.assertEqual(decision, "resume")
        self.assertIn("all_three_agree", reasons)

    def test_login_error_skips(self):
        loss, recovery = self.window()
        decision, reasons, _ = host_bb.evaluate(
            thread("thr_login0001"), LOGIN_EVENTS, loss, recovery
        )
        self.assertEqual(decision, "skip")
        self.assertIn("error_not_network", reasons)

    def test_idle_thread_never_resumes(self):
        loss, recovery = self.window()
        decision, reasons, _ = host_bb.evaluate(
            thread("thr_x", status="idle"), DEAD_EVENTS, loss, recovery
        )
        self.assertEqual(decision, "skip")
        self.assertIn("not_error_status", reasons)

    def test_error_without_provider_error_event_skips(self):
        loss, recovery = self.window()
        decision, reasons, _ = host_bb.evaluate(thread("thr_x"), [], loss, recovery)
        self.assertEqual(decision, "skip")
        self.assertIn("no_provider_error", reasons)


class ListFilterTests(unittest.TestCase):
    def test_only_error_threads_of_known_providers(self):
        listing = [
            thread("a", "error", "claude-code"),
            thread("b", "error", "codex"),
            thread("c", "error", "acp-cursor"),
            thread("d", "idle", "claude-code"),
            thread("e", "error", "pi"),
        ]
        with mock.patch.object(host_bb, "bb_json", return_value=listing):
            ids = [t["id"] for t in host_bb.list_error_threads()]
        self.assertEqual(ids, ["a", "b", "e"])


# Pi on Grok 4.6 via OpenRouter, 2026-09-03. Three real failures in one day:
# 13:23Z "Service temporarily unavailable" (online), 13:52Z "Connection error"
# (real Wi-Fi cut 13:28-13:53Z), and 14:00Z "at capacity" (online).
PI_EVENTS = json.loads((FIXTURES / "bb_thr_piwifi01_events.json").read_text())
PI_UNAVAILABLE_EVENTS = PI_EVENTS[:21]
PI_CAPACITY_EVENTS = json.loads((FIXTURES / "bb_thr_picap001_events.json").read_text())
PI_WIFI_LOSS = "2026-09-03T13:28:27.795722Z"
PI_WIFI_APIS_READY = "2026-09-03T13:53:40Z"


class PiOutagePathTests(unittest.TestCase):
    def test_pi_connection_error_inside_wifi_cut_resumes(self):
        decision, reasons, info = host_bb.evaluate(
            thread("thr_piwifi01", provider="pi"), PI_EVENTS, PI_WIFI_LOSS, PI_WIFI_APIS_READY
        )
        self.assertEqual(decision, "resume")
        self.assertIn("all_three_agree", reasons)
        self.assertEqual(info["error_detail"], "Connection error.")

    def test_provider_outage_is_not_a_network_error(self):
        decision, reasons, _ = host_bb.evaluate(
            thread("thr_picap001", provider="pi"), PI_CAPACITY_EVENTS, PI_WIFI_LOSS, PI_WIFI_APIS_READY
        )
        self.assertEqual((decision, reasons[-1]), ("skip", "error_not_network"))


class ProviderOutageTests(unittest.TestCase):
    def evaluate(self, events, after_secs, thread_id="thr_x"):
        err_at = host_bb.last_error(events)["at"]
        return host_bb.evaluate_provider_outage(
            thread(thread_id, provider="pi"), events, err_at + timedelta(seconds=after_secs)
        )

    def test_at_capacity_resumes_after_the_retry_delay(self):
        decision, reasons, info = self.evaluate(PI_CAPACITY_EVENTS, 150)
        self.assertEqual(decision, "resume")
        self.assertIn("provider_outage", reasons)
        self.assertIn("at capacity", info["error_detail"])

    def test_temporarily_unavailable_resumes(self):
        decision, _, info = self.evaluate(PI_UNAVAILABLE_EVENTS, 150)
        self.assertEqual(decision, "resume")
        self.assertIn("Service temporarily unavailable", info["error_detail"])

    def test_fresh_error_waits(self):
        decision, reasons, _ = self.evaluate(PI_CAPACITY_EVENTS, 30)
        self.assertEqual((decision, reasons[-1]), ("wait", "retry_delay"))

    def test_stale_error_skips(self):
        decision, reasons, _ = self.evaluate(PI_CAPACITY_EVENTS, 3 * 3600)
        self.assertEqual((decision, reasons[-1]), ("skip", "error_too_old"))

    def test_network_error_is_left_to_the_outage_path(self):
        decision, reasons, _ = self.evaluate(PI_EVENTS, 150)
        self.assertEqual((decision, reasons[-1]), ("skip", "network_error_belongs_to_outage_path"))

    def test_unlisted_error_is_unknown_not_resumed(self):
        decision, reasons, _ = self.evaluate(LOGIN_EVENTS, 150)
        self.assertEqual((decision, reasons[-1]), ("unknown", "error_not_whitelisted"))

    def test_idle_thread_skips(self):
        now = host_bb.last_error(PI_CAPACITY_EVENTS)["at"] + timedelta(seconds=150)
        decision, _, _ = host_bb.evaluate_provider_outage(thread("t", status="idle"), PI_CAPACITY_EVENTS, now)
        self.assertEqual(decision, "skip")


class ProviderCheckLoopTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(watcher, "log"))
        self.enterContext(mock.patch.object(watcher, "save_state"))
        self.send = self.enterContext(mock.patch.object(watcher.notify, "send", return_value=True))
        self.resume = self.enterContext(mock.patch.object(host_bb, "resume", return_value=(True, "ok")))
        self.dead = thread("thr_picap001", provider="pi")
        self.enterContext(mock.patch.object(host_bb, "list_error_threads", return_value=[self.dead]))
        self.enterContext(mock.patch.object(host_bb, "thread_events", return_value=PI_CAPACITY_EVENTS))
        self.err_at = host_bb.last_error(PI_CAPACITY_EVENTS)["at"]

    def run_at(self, state, after_secs):
        with mock.patch.object(watcher, "datetime", wraps=watcher.datetime) as dt:
            dt.now.return_value = self.err_at + timedelta(seconds=after_secs)
            watcher.run_provider_check(state)

    def test_revives_once_per_failure_after_the_delay(self):
        state = {}
        self.run_at(state, 30)
        self.resume.assert_not_called()
        self.run_at(state, 150)
        self.run_at(state, 200)
        self.resume.assert_called_once_with("thr_picap001")
        self.assertEqual(state["provider_revived"]["thr_picap001"]["tries"], 1)
        self.assertIn("provider outage", self.send.call_args.args[0])

    def test_caps_revives_per_thread(self):
        state = {"provider_revived": {"thr_picap001": {"tries": watcher.MAX_REVIVES, "at": iso(self.err_at), "error_at": "old"}}}
        self.run_at(state, 150)
        self.resume.assert_not_called()

    def test_unknown_error_is_announced_once_and_not_revived(self):
        state = {}
        with mock.patch.object(host_bb, "thread_events", return_value=LOGIN_EVENTS):
            self.run_at(state, 150)
            self.run_at(state, 200)
        self.resume.assert_not_called()
        self.send.assert_called_once()
        self.assertIn("Unhandled provider error", self.send.call_args.args[0])


class WatcherIntegrationTests(unittest.TestCase):
    def setUp(self):
        # A revive posts to Discord; tests must never hit the real webhook.
        self.enterContext(mock.patch.object(watcher.notify, "notify_revive", return_value=False))

    def test_recovery_resumes_dead_bb_thread_once(self):
        loss = iso(DEATH_AT - timedelta(minutes=5))
        recovery = iso(DEATH_AT + timedelta(minutes=5))
        state = {"resumed": {}}
        with mock.patch.object(watcher, "log"), mock.patch.object(
            watcher, "save_state"
        ), mock.patch.object(
            host_bb, "list_error_threads", return_value=[thread("thr_claudea01")]
        ), mock.patch.object(
            host_bb, "thread_events", return_value=DEAD_EVENTS
        ), mock.patch.object(
            host_bb, "resume", return_value=(True, "steered")
        ) as resume:
            watcher.recover_bb_threads(state, loss, recovery)
            watcher.recover_bb_threads(state, loss, recovery)
        resume.assert_called_once_with("thr_claudea01")
        self.assertIn(f"bb:thr_claudea01:{loss}", state["resumed"])

    def test_bb_unavailable_is_logged_not_fatal(self):
        state = {"resumed": {}}
        with mock.patch.object(watcher, "log") as log, mock.patch.object(
            host_bb, "list_error_threads", side_effect=host_bb.BbUnavailable("down")
        ):
            watcher.recover_bb_threads(state, "2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z")
        log.assert_called_once()
        self.assertEqual(log.call_args.args[0], "bb_unavailable")


if __name__ == "__main__":
    unittest.main()
