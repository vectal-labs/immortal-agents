#!/usr/bin/env python3
"""Tests for the bb host (ADR 0036) against real recorded bb events."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from immortal.detect import bb as detect_bb
from immortal.detect import bb_provider as detect_bb_provider
from immortal.hosts import bb as host_bb
from immortal.core import bb_runtime
import revive
from immortal.core import logbook, revive_state
import watcher
from immortal.core.common import now_iso, parse_ts

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Real death on 2026-08-31: Claude Code in bb, internet lost (ENOTFOUND).
DEAD_EVENTS = json.loads((FIXTURES / "bb_thr_jg3yv6kthc_events.json").read_text())
# Real non-network error: "Not logged in".
LOGIN_EVENTS = json.loads((FIXTURES / "bb_thr_hnu4c2a76j_events.json").read_text())
DEATH_AT = datetime.fromtimestamp(1788185043065 / 1000, tz=timezone.utc)
# Experiment 0013 (2026-09-03): Claude Code in bb died 20s AFTER the captive
# probe passed, inside the stale-DNS gap, before api.anthropic.com resolved.
LATE_DEATH_EVENTS = json.loads((FIXTURES / "bb_thr_gmgh8s7j9w_events.json").read_text())
EXP13_LOSS = "2026-09-03T09:08:36.920310Z"
EXP13_PROBE_ONLINE = "2026-09-03T09:28:24.340210Z"
EXP13_APIS_READY = "2026-09-03T09:30:05.188265Z"


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def thread(thread_id, status="error", provider="claude-code"):
    return {"id": thread_id, "status": status, "providerId": provider, "title": "t"}


def detector_input(value, events):
    error = host_bb.last_error(events)
    target = {
        "ref": value["id"],
        "id": value["id"],
        "cwd": None,
        "title": value.get("title"),
        "harness_hint": value.get("providerId"),
        "status": value.get("status"),
        "error_at": error["at"].isoformat() if error and error["at"] else None,
    }
    return target, error["detail"] if error else None


def evaluate_outage(value, events, loss, recovery):
    target, screen = detector_input(value, events)
    return detect_bb.evaluate(target, screen, (loss, recovery))


def evaluate_provider(value, events, now):
    target, screen = detector_input(value, events)
    stamp = now_iso(now)
    return detect_bb_provider.evaluate(target, screen, ("1970-01-01T00:00:00Z", stamp))


class EvaluateTests(unittest.TestCase):
    def window(self, minutes_before=10, minutes_after=10):
        return iso(DEATH_AT - timedelta(minutes=minutes_before)), iso(
            DEATH_AT + timedelta(minutes=minutes_after)
        )

    def test_real_network_death_inside_outage_resumes(self):
        loss, recovery = self.window()
        decision, reasons, info = evaluate_outage(
            thread("thr_jg3yv6kthc"), DEAD_EVENTS, loss, recovery
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
        decision, reasons, _ = evaluate_outage(
            thread("thr_jg3yv6kthc"), DEAD_EVENTS, loss, recovery
        )
        self.assertEqual(decision, "skip")
        self.assertIn("timing_miss", reasons)

    def test_death_in_the_stale_dns_gap_is_inside_the_outage(self):
        # A window that ends at the captive probe misses it (the 0013 bug)...
        decision, reasons, info = evaluate_outage(
            thread("thr_gmgh8s7j9w"), LATE_DEATH_EVENTS, EXP13_LOSS, EXP13_PROBE_ONLINE
        )
        self.assertEqual((decision, reasons[-1]), ("skip", "timing_miss"))
        self.assertGreater(parse_ts(info["error_at"]), parse_ts(EXP13_PROBE_ONLINE))
        # ...a window that ends when the APIs resolve catches it.
        decision, reasons, _ = evaluate_outage(
            thread("thr_gmgh8s7j9w"), LATE_DEATH_EVENTS, EXP13_LOSS, EXP13_APIS_READY
        )
        self.assertEqual(decision, "resume")
        self.assertIn("all_three_agree", reasons)

    def test_login_error_skips(self):
        loss, recovery = self.window()
        decision, reasons, _ = evaluate_outage(
            thread("thr_hnu4c2a76j"), LOGIN_EVENTS, loss, recovery
        )
        self.assertEqual(decision, "skip")
        self.assertIn("error_not_network", reasons)

    def test_idle_thread_never_resumes(self):
        loss, recovery = self.window()
        decision, reasons, _ = evaluate_outage(
            thread("thr_x", status="idle"), DEAD_EVENTS, loss, recovery
        )
        self.assertEqual(decision, "skip")
        self.assertIn("not_error_status", reasons)

    def test_error_without_provider_error_event_skips(self):
        loss, recovery = self.window()
        decision, reasons, _ = evaluate_outage(thread("thr_x"), [], loss, recovery)
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
            ids = [t["id"] for t in host_bb.list_targets()]
        self.assertEqual(ids, ["a", "b", "c", "e"])

    def test_idle_cursor_threads_are_candidates_only_when_recent(self):
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        recent = {**thread("recent", "idle", "acp-cursor"), "latestAttentionAt": now_ms}
        stale = {**thread("stale", "idle", "acp-cursor"), "latestAttentionAt": now_ms - 2 * 86400 * 1000}
        claude = {**thread("claude", "idle", "claude-code"), "latestAttentionAt": now_ms}
        with mock.patch.object(host_bb, "bb_json", return_value=[recent, stale, claude]):
            ids = [t["id"] for t in host_bb._list_error_threads()]
        self.assertEqual(ids, ["recent"])

    def test_error_is_fetched_once_and_cached_with_target(self):
        with mock.patch.object(host_bb, "_list_error_threads", return_value=[thread("a")]), mock.patch.object(
            host_bb, "_thread_events", return_value=DEAD_EVENTS
        ) as events:
            target = host_bb.list_targets()[0]
            self.assertIn("ENOTFOUND", host_bb.read_screen("a"))
            self.assertIn("ENOTFOUND", host_bb.read_screen("a"))
        events.assert_called_once_with("a")
        self.assertEqual(target["error_at"], DEATH_AT.isoformat())


class ResumeTests(unittest.TestCase):
    def setUp(self):
        # Isolate HOME/state and give resume a real Node+bb pair to validate.
        # Unisolated get_runtime() wrote ~/.immortal-agents/bb_runtime.json.
        self._env = os.environ.copy()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        state = root / "state"
        state.mkdir()
        node = root / "node"
        bb = root / "bb"
        node.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-p" ] && [ "$2" = "process.execPath" ]; then echo "$0"; exit 0; fi\n'
            'script="$1"; shift; exec /bin/bash "$script" "$@"\n'
        )
        bb.write_text("#!/usr/bin/env node\necho '[]'\nexit 0\n")
        node.chmod(node.stat().st_mode | stat.S_IEXEC)
        bb.chmod(bb.stat().st_mode | stat.S_IEXEC)
        os.environ["WATCHER_STATE_DIR"] = str(state)
        os.environ["HOME"] = str(root / "home")
        (root / "home").mkdir()
        os.environ["IMMORTAL_NODE"] = str(node)
        os.environ["IMMORTAL_NODE_HINTS"] = ""
        os.environ["BB_BIN"] = str(bb)
        os.environ.pop("BB_CLI", None)
        os.environ.pop("IMMORTAL_PLIST", None)

    def tearDown(self):
        for key in list(os.environ):
            if key not in self._env:
                os.environ.pop(key, None)
        os.environ.update(self._env)
        self.tmp.cleanup()

    def test_logs_stdout_tail(self):
        # Validate before patching: host_bb.subprocess is the stdlib module, so
        # the mock replaces every subprocess.run, including Node discovery.
        runtime = bb_runtime.get_runtime()
        self.assertTrue(os.path.isfile(runtime["node"]))
        self.assertTrue(os.path.isfile(runtime["bb"]))
        value = thread("thr_x")
        with mock.patch.object(host_bb, "_list_error_threads", return_value=[value]), mock.patch.object(
            host_bb, "_thread_events", return_value=DEAD_EVENTS
        ):
            target = host_bb.list_targets()[0]
        proc = mock.Mock(returncode=0, stdout='{"ok":true,"delivery":"sent"}', stderr="")

        def run(runtime, args, **kwargs):
            proc.args = bb_runtime.command_for(runtime, args)
            return proc

        def read(args):
            if args[1] == "show":
                return {"thread": value}
            if args[1] == "log":
                return DEAD_EVENTS
            return []
        with mock.patch.object(bb_runtime, "run_command", side_effect=run), mock.patch.object(
            host_bb, "log"
        ) as log, mock.patch.object(host_bb, "bb_json", side_effect=read):
            self.assertEqual(host_bb.resume(target), "sent")
        self.assertEqual(log.call_args.args[0], "bb_result")
        self.assertEqual(log.call_args.kwargs["stdout"], proc.stdout)
        self.assertEqual(log.call_args.kwargs["cmd"][:2], [runtime["node"], runtime["bb"]])


# Pi on Grok 4.6 via OpenRouter, 2026-09-03. Three real failures in one day:
# 13:23Z "Service temporarily unavailable" (online), 13:52Z "Connection error"
# (real Wi-Fi cut 13:28-13:53Z), and 14:00Z "at capacity" (online).
PI_EVENTS = json.loads((FIXTURES / "bb_thr_fv5bxrynb7_events.json").read_text())
PI_UNAVAILABLE_EVENTS = PI_EVENTS[:21]
PI_CAPACITY_EVENTS = json.loads((FIXTURES / "bb_thr_b2q9cmz7zb_events.json").read_text())
PI_WIFI_LOSS = "2026-09-03T13:28:27.795722Z"
PI_WIFI_APIS_READY = "2026-09-03T13:53:40Z"


class PiOutagePathTests(unittest.TestCase):
    def test_pi_connection_error_inside_wifi_cut_resumes(self):
        decision, reasons, info = evaluate_outage(
            thread("thr_fv5bxrynb7", provider="pi"), PI_EVENTS, PI_WIFI_LOSS, PI_WIFI_APIS_READY
        )
        self.assertEqual(decision, "resume")
        self.assertIn("all_three_agree", reasons)
        self.assertEqual(info["error_detail"], "Connection error.")

    def test_provider_outage_is_not_a_network_error(self):
        decision, reasons, _ = evaluate_outage(
            thread("thr_b2q9cmz7zb", provider="pi"), PI_CAPACITY_EVENTS, PI_WIFI_LOSS, PI_WIFI_APIS_READY
        )
        self.assertEqual((decision, reasons[-1]), ("skip", "error_not_network"))


class ProviderOutageTests(unittest.TestCase):
    def evaluate(self, events, after_secs, thread_id="thr_x"):
        err_at = host_bb.last_error(events)["at"]
        return evaluate_provider(
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
        decision, _, _ = evaluate_provider(thread("t", status="idle"), PI_CAPACITY_EVENTS, now)
        self.assertEqual(decision, "skip")


class ProviderCheckLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(logbook, "STATE_DIR", self.tmp))
        self.enterContext(mock.patch.object(logbook, "STATE_PATH", self.tmp / "state.json"))
        self.enterContext(mock.patch.object(logbook, "LOG_PATH", self.tmp / "watcher.log"))
        self.enterContext(mock.patch.object(revive, "log"))
        self.enterContext(mock.patch.object(revive_state, "save_state"))
        self.enterContext(mock.patch.object(revive.ready, "check", return_value=True))
        self.send = self.enterContext(mock.patch.object(revive.notify, "notify_revive", return_value=True))
        self.resume = self.enterContext(mock.patch.object(host_bb, "resume", return_value="sent"))
        self.enterContext(mock.patch.object(host_bb, "available", return_value=True))
        self.dead, screen = detector_input(thread("thr_b2q9cmz7zb", provider="pi"), PI_CAPACITY_EVENTS)
        self.enterContext(mock.patch.object(host_bb, "list_targets", return_value=[self.dead]))
        self.enterContext(mock.patch.object(host_bb, "read_screen", return_value=screen))
        self.enterContext(mock.patch.object(host_bb, "_thread_events", return_value=PI_CAPACITY_EVENTS))
        self.err_at = host_bb.last_error(PI_CAPACITY_EVENTS)["at"]

    def run_at(self, state, after_secs):
        with mock.patch.object(revive, "datetime", wraps=revive.datetime) as dt, mock.patch.object(
            revive_state, "datetime", wraps=revive_state.datetime
        ) as state_dt:
            dt.now.return_value = self.err_at + timedelta(seconds=after_secs)
            state_dt.now.return_value = dt.now.return_value
            revive.run_provider_check(state)

    def test_revives_once_per_failure_after_the_delay(self):
        state = {}
        self.run_at(state, 30)
        self.resume.assert_not_called()
        self.run_at(state, 150)
        self.run_at(state, 200)
        self.resume.assert_called_once_with(self.dead)
        self.assertEqual(state["revived"]["bb:thr_b2q9cmz7zb:provider"]["tries"], 1)
        self.assertEqual(self.send.call_args.args[-1], "provider_outage")

    def test_caps_revives_per_thread(self):
        state = {"revived": {"bb:thr_b2q9cmz7zb:provider": {
            "tries": revive.MAX_REVIVES, "at": iso(self.err_at), "error_at": "old"
        }}}
        self.run_at(state, 150)
        self.resume.assert_not_called()

    def test_failed_provider_delivery_retries_after_restart_and_backoff(self):
        state = {}
        self.resume.return_value = 'not_sent'
        self.run_at(state, 150)
        logbook.save_state(state)
        loaded = logbook.load_state()
        self.run_at(loaded, 160)
        self.assertEqual(self.resume.call_count, 1)
        self.resume.return_value = 'sent'
        self.run_at(loaded, 181)
        self.assertEqual(self.resume.call_count, 2)
        self.assertEqual(loaded['revived']['bb:thr_b2q9cmz7zb:provider']['delivery'], 'sent')
        self.run_at(loaded, 250)
        self.assertEqual(self.resume.call_count, 2)

    def test_unknown_provider_delivery_is_observed_without_retry_after_restart(self):
        state = {}
        self.resume.return_value = 'unknown'
        self.run_at(state, 150)
        logbook.save_state(state)
        loaded = logbook.load_state()
        self.run_at(loaded, 250)
        self.resume.assert_called_once()
        self.assertTrue(loaded['pending_revives'])

    def test_unready_provider_check_does_not_attempt_delivery(self):
        state = {}
        with mock.patch.object(revive.ready, 'check', return_value=False):
            self.run_at(state, 150)
        self.resume.assert_not_called()
        self.assertFalse(state.get('revived'))
        self.run_at(state, 181)
        self.resume.assert_called_once()

    def test_unknown_error_is_announced_once_and_not_revived(self):
        state = {}
        unknown, screen = detector_input(thread("thr_b2q9cmz7zb", provider="pi"), LOGIN_EVENTS)
        with mock.patch.object(host_bb, "list_targets", return_value=[unknown]), mock.patch.object(
            host_bb, "read_screen", return_value=screen
        ):
            self.run_at(state, 150)
            self.run_at(state, 200)
        self.resume.assert_not_called()
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[-1], "unhandled_provider_error")


# ADR 0048. Real death on 2026-09-03: Cursor (acp) in bb, Wi-Fi lost while a
# DeepAPI batch ran. No provider/error; the last agent message is the CLI
# error line and the thread went idle. The watcher noticed the loss at
# 19:19:04.9Z, 1.3s AFTER the death; the last good probe was 19:14:58.4Z.
CURSOR_EVENTS = json.loads((FIXTURES / "bb_thr_vp7hipzyr6_events.json").read_text())
CURSOR_DEATH_AT = "2026-09-03T19:19:03.596000+00:00"
CURSOR_LAST_GOOD_PROBE = "2026-09-03T19:14:58.401757Z"
CURSOR_FIRST_FAILED_PROBE = "2026-09-03T19:19:04.904731Z"
CURSOR_APIS_READY = "2026-09-03T19:42:07.754192Z"


def cursor_thread(status="idle"):
    return {"id": "thr_vp7hipzyr6", "status": status, "providerId": "acp-cursor", "title": "what_is_conductor"}


def cursor_target(events):
    with mock.patch.object(host_bb, "_list_error_threads", return_value=[cursor_thread()]), mock.patch.object(
        host_bb, "_thread_events", return_value=events
    ):
        targets = host_bb.list_targets()
    return targets, [host_bb.read_screen(t["ref"]) for t in targets]


class CursorTests(unittest.TestCase):
    def test_dead_cursor_thread_is_a_target_with_error_status(self):
        targets, screens = cursor_target(CURSOR_EVENTS)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["status"], "error")
        self.assertEqual(targets[0]["bb_status"], "idle")
        self.assertEqual(targets[0]["error_at"], CURSOR_DEATH_AT)
        self.assertEqual(screens, ["Error: RetriableError: Connection stalled"])

    def test_finished_cursor_thread_is_not_a_target(self):
        # Drop the error message and its turn end: the turn ends on prose.
        targets, _ = cursor_target(CURSOR_EVENTS[:-3])
        self.assertEqual(targets, [])

    def test_messages_only_count_for_message_error_providers(self):
        self.assertIsNone(host_bb.last_error(CURSOR_EVENTS))
        self.assertEqual(host_bb.last_error(CURSOR_EVENTS, "acp-cursor")["detail"],
                         "Error: RetriableError: Connection stalled")

    def test_cursor_death_inside_the_real_outage_window_resumes(self):
        targets, screens = cursor_target(CURSOR_EVENTS)
        window = (CURSOR_LAST_GOOD_PROBE, CURSOR_APIS_READY)
        decision, reasons, info = detect_bb.evaluate(targets[0], screens[0], window)
        self.assertEqual(decision, "resume")
        self.assertIn("all_three_agree", reasons)
        self.assertEqual(info["error_detail"], "Error: RetriableError: Connection stalled")

    def test_window_opened_at_the_first_failed_probe_misses_the_death(self):
        # The 2026-09-03 bug: the death is 1.3s before the first failed probe.
        targets, screens = cursor_target(CURSOR_EVENTS)
        window = (CURSOR_FIRST_FAILED_PROBE, CURSOR_APIS_READY)
        decision, reasons, _ = detect_bb.evaluate(targets[0], screens[0], window)
        self.assertEqual((decision, reasons[-1]), ("skip", "timing_miss"))

    def test_cursor_network_error_is_left_to_the_outage_path_when_online(self):
        targets, screens = cursor_target(CURSOR_EVENTS)
        now = now_iso(parse_ts(CURSOR_DEATH_AT) + timedelta(seconds=150))
        decision, reasons, _ = detect_bb_provider.evaluate(targets[0], screens[0], ("1970-01-01T00:00:00Z", now))
        self.assertEqual((decision, reasons[-1]), ("skip", "network_error_belongs_to_outage_path"))


# Experiment 0014 (2026-09-03): the same Cursor death 34s into a real Wi-Fi
# cut, with a different error line. The watcher saw the thread but skipped it
# as "error_not_network" until "ping timed out" joined the fingerprints.
CURSOR_0014_EVENTS = json.loads((FIXTURES / "bb_thr_acbjnabb28_events.json").read_text())
CURSOR_0014_WINDOW = ("2026-09-03T20:43:16.151448Z", "2026-09-03T21:07:11.318346Z")


class Cursor0014Tests(unittest.TestCase):
    def test_ping_timed_out_death_resumes(self):
        with mock.patch.object(host_bb, "_list_error_threads", return_value=[
            {**cursor_thread(), "id": "thr_acbjnabb28"}
        ]), mock.patch.object(host_bb, "_thread_events", return_value=CURSOR_0014_EVENTS):
            target = host_bb.list_targets()[0]
        screen = host_bb.read_screen("thr_acbjnabb28")
        self.assertEqual(screen, "Error: RetriableError: [unavailable] PING timed out")
        decision, reasons, _ = detect_bb.evaluate(target, screen, CURSOR_0014_WINDOW)
        self.assertEqual(decision, "resume")
        self.assertIn("all_three_agree", reasons)


# Experiment 0015 (2026-09-03): the unattended revive. The thread log ends
# with the watcher's "keep going" turn, so the death is no longer its last
# message and it must not be a target again.
CURSOR_0015_EVENTS = json.loads((FIXTURES / "bb_thr_rsgzd56nhq_events.json").read_text())


class Cursor0015Tests(unittest.TestCase):
    def test_revived_cursor_thread_is_no_longer_a_target(self):
        with mock.patch.object(host_bb, "_list_error_threads", return_value=[
            {**cursor_thread(), "id": "thr_rsgzd56nhq"}
        ]), mock.patch.object(host_bb, "_thread_events", return_value=CURSOR_0015_EVENTS):
            self.assertEqual(host_bb.list_targets(), [])


class WatcherIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(logbook, "STATE_DIR", self.tmp))
        self.enterContext(mock.patch.object(logbook, "STATE_PATH", self.tmp / "state.json"))
        self.enterContext(mock.patch.object(logbook, "LOG_PATH", self.tmp / "watcher.log"))
        # A revive posts to Discord; tests must never hit the real webhook.
        self.enterContext(mock.patch.object(revive.notify, "notify_revive", return_value=False))

    def test_recovery_resumes_dead_bb_thread_once(self):
        loss = iso(DEATH_AT - timedelta(minutes=5))
        recovery = iso(DEATH_AT + timedelta(minutes=5))
        state = {"revived": {}}
        with mock.patch.object(revive, "log"), mock.patch.object(
            revive_state, "save_state"
        ), mock.patch.object(
            host_bb, "list_targets", return_value=[detector_input(thread("thr_jg3yv6kthc"), DEAD_EVENTS)[0]]
        ), mock.patch.object(
            host_bb, "read_screen", return_value=detector_input(thread("thr_jg3yv6kthc"), DEAD_EVENTS)[1]
        ), mock.patch.object(
            host_bb, "resume", return_value="sent"
        ) as resume, mock.patch.object(
            host_bb, "available", return_value=True
        ), mock.patch.object(
            revive, "HOSTS", (host_bb,)
        ):
            revive.revive_pass(state, (loss, recovery), "first")
            revive.revive_pass(state, (loss, recovery), "first")
        resume.assert_called_once_with(detector_input(thread("thr_jg3yv6kthc"), DEAD_EVENTS)[0])
        self.assertIn(f"bb:thr_jg3yv6kthc:{loss}", state["revived"])

    def test_bb_unavailable_is_logged_not_fatal(self):
        state = {"revived": {}}
        with mock.patch.object(revive, "log"), mock.patch.object(
            host_bb, "log"
        ) as log, mock.patch.object(
            host_bb, "_list_error_threads", side_effect=host_bb.BbUnavailable("down")
        ), mock.patch.object(
            host_bb, "available", return_value=True
        ), mock.patch.object(
            revive, "HOSTS", (host_bb,)
        ):
            revive.revive_pass(
                state, ("2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"), "first"
            )
        log.assert_called_once()
        self.assertEqual(log.call_args.args[0], "host_unavailable")


if __name__ == "__main__":
    unittest.main()
