"""bb submission recovery, starting with the real remove_tokenmaxx failure."""

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from immortal.hosts import bb as host
from immortal.core import bb_recovery, logbook, outcomes
from support import isolate_state
import revive

EVENTS = json.loads((Path(__file__).parent / "fixtures/bb_submission_timeout.json").read_text())
REF = "thr_rx3pebr6j2"
REQUEST = "creq_x49j9b62ab"
ERROR_AT = datetime.fromtimestamp(EVENTS[-1]["createdAt"] / 1000, timezone.utc)


RESTART_EVENTS = json.loads((Path(__file__).parent / "fixtures/bb_daemon_restart.json").read_text())
RESTART_AT = datetime.fromtimestamp(RESTART_EVENTS[-1]["createdAt"] / 1000, timezone.utc)


class DaemonRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(logbook, "STATE_PATH", self.tmp / "state.json"))
        self.enterContext(mock.patch.object(logbook, "LOG_PATH", self.tmp / "watcher.log"))
        self.enterContext(mock.patch.object(logbook, "STATE_DIR", self.tmp))
        self.notice = self.enterContext(mock.patch.object(outcomes.notify, "notify_revive"))
        self.enterContext(mock.patch.object(outcomes.notify, "webhook_url", return_value="https://example.invalid/hook"))
        self.enterContext(mock.patch.object(host, "_LOG_CACHE", {}))
        self.enterContext(mock.patch.object(host, "available", return_value=True))
        self.enterContext(mock.patch.object(outcomes.telemetry, "send"))
        self.alert = self.enterContext(mock.patch.object(revive, "announce"))
        self.events = copy.deepcopy(RESTART_EVENTS)
        self.thread = {"id": REF, "providerId": "codex", "status": "error", "title": "test"}
        self.queue = []
        self.interactions = []
        self.machines = [{"id": "host-1", "status": "connected"}]
        self.enterContext(mock.patch.object(host, "bb_json", side_effect=self.read_bb))
        self.command = self.enterContext(mock.patch.object(host, "run_bb", return_value=mock.Mock(
            returncode=0, stdout='{"ok":true,"delivery":"sent"}', stderr="")))
        self.state = {"revived": {}}

    def read_bb(self, args):
        if args == ["thread", "list"]:
            return [self.thread]
        if args[:2] == ["thread", "show"]:
            return {"thread": self.thread, "environment": {"hostId": "host-1"}}
        if args[:3] == ["thread", "queue", "list"]:
            return self.queue
        if args[:3] == ["thread", "interactions", "list"]:
            return self.interactions
        if args == ["machine", "list"]:
            return self.machines
        return self.events

    def run_at(self, seconds=31):
        now = RESTART_AT + timedelta(seconds=seconds)
        with mock.patch.object(outcomes, "now_iso", return_value=now.isoformat()):
            return revive.revive_pass(self.state, (now.isoformat(), now.isoformat()), "provider")

    def test_real_daemon_restart_continues_accepted_turn_without_replaying_input(self):
        self.assertEqual(self.run_at(), 1)
        self.assertEqual(self.command.call_args.args[0],
                         ["thread", "retry", REF, "--turn", "request-1", "--json"])
        self.assertNotIn("unhandled_provider_error", str(self.alert.call_args_list))


    def test_exact_event_identity_is_preserved_without_prompts(self):
        error = host.last_error(self.events, "codex")
        self.assertEqual(error["at"], RESTART_AT)
        self.assertEqual(error["interruption"], {
            "request_id": "request-1", "original_request_id": "request-1", "attempt": 1,
            "turn_id": "turn-1", "error_seq": 875})
        self.assertNotIn("submission", error)

    def test_only_matching_accepted_daemon_interruptions_qualify(self):
        variants = []
        for index, field, value in ((5, "reason", "user"), (3, "status", "completed"),
                                     (2, "clientRequestId", "other"),
                                     (4, "message", "Another command failed")):
            events = copy.deepcopy(RESTART_EVENTS)
            events[index]["data"][field] = value
            variants.append(events)
        for missing in (0, 1, 2, 3, 4):
            variants.append([e for i, e in enumerate(RESTART_EVENTS) if i != missing])
        mismatched = copy.deepcopy(RESTART_EVENTS)
        mismatched[3]["scope"]["turnId"] = "other"
        variants.append(mismatched)
        late = copy.deepcopy(RESTART_EVENTS)
        late[-1]["createdAt"] += 1
        variants.append(late)
        for events in variants:
            with self.subTest(events=events):
                error = host.last_error(events, "codex")
                self.assertFalse(error and error.get("interruption"))

    def test_new_work_and_manual_stops_invalidate_restart(self):
        for event in ({"type": "client/turn/requested", "data": {"requestId": "new"}},
                      {"type": "turn/started", "scope": {"turnId": "new"}},
                      {"type": "turn/completed", "data": {"status": "completed"}},
                      {"type": "turn/completed", "data": {"status": "interrupted"}},
                      {"type": "system/thread/interrupted", "data": {"reason": "user"}}):
            with self.subTest(event=event):
                self.assertIsNone(host.last_error(RESTART_EVENTS + [event], "codex"))

    def test_delay_age_and_future_timestamp(self):
        for seconds in (-1, 29, bb_recovery.MAX_AGE_SECS + 1):
            self.assertEqual(self.run_at(seconds), 0)
        self.command.assert_not_called()
        self.assertEqual(self.run_at(30), 1)

    def test_host_must_be_connected_and_own_the_environment(self):
        for machines in ([], [{"id": "host-1", "status": "disconnected"}],
                         [{"id": "other", "status": "connected"}]):
            self.machines = machines
            self.assertEqual(self.run_at(), 0)
        self.command.assert_not_called()
        self.machines = [{"id": "host-1", "status": "connected"}]
        self.assertEqual(self.run_at(61), 1)

    def test_queued_messages_and_approvals_block_recovery(self):
        self.queue = [{"id": "queued"}]
        self.run_at()
        self.queue = []
        self.interactions = [{"id": "approval"}]
        self.run_at()
        self.interactions = []
        self.command.assert_not_called()
        self.run_at(61)
        self.command.assert_called_once()

    def test_current_status_is_rechecked_before_dispatch(self):
        target = host.list_targets()[0]
        for changed in ({"status": "idle"}, {"status": "active"}, {"status": "starting"},
                        {"archivedAt": 1}, {"deletedAt": 1}, {"hasPendingInteraction": True},
                        {"queuedMessageCount": 1}, {"activeBackgroundAgentCount": 1}):
            with self.subTest(changed=changed):
                self.thread = {"id": REF, "status": "error", **changed}
                self.assertFalse(host.submission_ready(target))
        self.command.assert_not_called()

    def test_second_guard_catches_work_arriving_after_initial_check(self):
        reads = 0
        def read(args):
            nonlocal reads
            if args[:2] == ["thread", "show"]:
                reads += 1
                if reads == 2:
                    self.events.append({"type": "client/turn/requested", "data": {"requestId": "new"}})
            return self.read_bb(args)
        with mock.patch.object(host, "bb_json", side_effect=read):
            self.assertEqual(self.run_at(), 0)
        self.command.assert_not_called()
        self.assertEqual(self.state["pending_revives"], {})

    def test_native_guard_rejects_race_without_another_retry(self):
        self.command.return_value = mock.Mock(returncode=1, stdout="", stderr="no_failed_turn")
        self.run_at()
        self.run_at(61)
        self.command.assert_called_once()
        self.alert.assert_not_called()
        self.assertEqual(self.state["pending_revives"], {})

    def test_reservation_and_observation_are_saved_before_dispatch(self):
        def dispatch(*args, **kwargs):
            saved = logbook.load_state()
            entry = saved["revived"][f"bb:{REF}:daemon"]
            self.assertEqual(entry["handled_seq"], 875)
            self.assertEqual(entry["delivery"], "unknown")
            self.assertEqual(len(saved["pending_revives"]), 1)
            raise host.subprocess.TimeoutExpired("bb", 60)
        self.command.side_effect = dispatch
        self.run_at()
        self.state = logbook.load_state()
        self.run_at(61)
        self.run_at(120)
        self.command.assert_called_once()
        self.assertEqual(len(self.state["pending_revives"]), 1)
        self.alert.assert_not_called()

    def test_queued_and_sent_deliveries_never_repeat_after_watcher_restart(self):
        for delivery in ("queued", "sent"):
            with self.subTest(delivery=delivery):
                self.state = {"revived": {}}
                self.command.reset_mock()
                self.command.return_value.stdout = json.dumps({"ok": True, "delivery": delivery})
                self.run_at()
                self.state = logbook.load_state()
                self.run_at(61)
                self.command.assert_called_once()
                self.assertEqual(len(self.state["pending_revives"]), 1)
                self.notice.assert_not_called()

    def test_definite_failure_alerts_once_without_retries(self):
        self.command.return_value = mock.Mock(returncode=1, stdout="", stderr="command failed")
        self.run_at()
        self.state = logbook.load_state()
        self.run_at(61)
        self.command.assert_called_once()
        self.alert.assert_called_once()
        self.assertEqual(self.alert.call_args.kwargs["trigger"], "bb_daemon_interruption")
        self.assertEqual(self.state["pending_revives"], {})

    def test_hourly_cap_survives_restart_and_expires_for_new_interruptions(self):
        for index in range(5):
            seconds = 100 * index if index < 4 else 3700
            self.events = copy.deepcopy(RESTART_EVENTS)
            for event in self.events:
                event["createdAt"] += seconds * 1000
                event["seq"] += index * 1000
            self.run_at(seconds + 31)
            self.state = logbook.load_state()
        self.assertEqual(self.command.call_count, 4)
        limited = [c for c in self.alert.call_args_list
                   if c.kwargs.get("detail") == "daemon_recovery_hourly_limit"]
        self.assertEqual(len(limited), 1)

    def test_pending_recovery_survives_long_wait_then_alerts_once_on_interruption(self):
        self.command.side_effect = host.subprocess.TimeoutExpired("bb", 60)
        self.run_at()
        self.state = logbook.load_state()
        for seconds in (631, 1000):
            outcomes.check(self.state, host._thread_events, RESTART_AT + timedelta(seconds=seconds))
            self.state = logbook.load_state()
        self.notice.assert_not_called()
        self.assertEqual(len(self.state["pending_revives"]), 1)
        self.events.append({"type": "system/thread/interrupted", "seq": 876,
                            "createdAt": (RESTART_AT.timestamp() + 1100) * 1000,
                            "data": {"reason": "user"}})
        for seconds in (1101, 1200):
            outcomes.check(self.state, host._thread_events, RESTART_AT + timedelta(seconds=seconds))
            self.state = logbook.load_state()
        self.notice.assert_called_once()
        self.assertEqual(self.notice.call_args.kwargs["trigger"], "bb_daemon_recovery_unconfirmed")
        self.command.assert_called_once()

    def test_lost_reply_is_reconciled_by_output_from_exact_retry_turn(self):
        self.command.side_effect = host.subprocess.TimeoutExpired("bb", 60)
        self.run_at()
        self.state = logbook.load_state()
        self.events.extend([
            {"type": "client/turn/requested", "data": {"requestId": "retry-1",
             "retryOfRequestId": "request-1", "retryAttempt": 2}},
            {"type": "turn/started", "scope": {"turnId": "retry-turn"}},
            {"type": "turn/input/accepted", "scope": {"turnId": "retry-turn"},
             "data": {"clientRequestId": "retry-1"}},
            {"type": "item/completed", "createdAt": (RESTART_AT.timestamp() + 40) * 1000,
             "scope": {"turnId": "old-turn"},
             "data": {"item": {"type": "agentMessage", "text": "Old output"}}},
        ])
        outcomes.check(self.state, host._thread_events, RESTART_AT + timedelta(seconds=45))
        self.notice.assert_not_called()
        self.events.append({"type": "item/completed", "createdAt": (RESTART_AT.timestamp() + 50) * 1000,
                            "scope": {"turnId": "retry-turn"},
                            "data": {"item": {"type": "agentMessage", "text": "Continued"}}})
        outcomes.check(self.state, host._thread_events, RESTART_AT + timedelta(seconds=60))
        self.state = logbook.load_state()
        outcomes.check(self.state, host._thread_events, RESTART_AT + timedelta(seconds=90))
        self.notice.assert_not_called()
        alerts = logbook.load_state()["discord_outbox"]
        self.assertEqual(len(alerts), 1)
        self.assertIn('Recovery confirmed: Codex in bb', next(iter(alerts.values()))['text'])
        self.command.assert_called_once()

    def test_status_read_failure_defers_without_reserving_an_attempt(self):
        with mock.patch.object(host, "submission_ready", side_effect=host.BbUnavailable("offline")):
            self.assertEqual(self.run_at(), 0)
        self.command.assert_not_called()
        self.assertNotIn("handled_seq", self.state["revived"][f"bb:{REF}:daemon"])
        self.assertEqual(self.run_at(61), 1)

    def test_daemon_recovery_never_resets_the_thread(self):
        target = host.list_targets()[0]
        self.assertEqual(host.retry_submission(target, reset=True), "sent")
        self.assertEqual(self.command.call_args.args[0][:2], ["thread", "retry"])
        self.command.assert_called_once()

    def test_outage_passes_leave_daemon_recovery_to_online_polling(self):
        for mode in ("first", "recheck"):
            with mock.patch.object(revive, "HOSTS", (host,)):
                stamp = (RESTART_AT + timedelta(seconds=31)).isoformat()
                self.assertEqual(revive.revive_pass(self.state, (stamp, stamp), mode), 0)
        self.command.assert_not_called()
        self.alert.assert_not_called()

    def test_original_input_is_not_persisted_or_sent(self):
        self.events[0]["data"]["input"] = [{"type": "text", "text": "private original instruction"}]
        self.run_at()
        self.assertNotIn("private original instruction", (self.tmp / "state.json").read_text())
        self.assertNotIn("private original instruction", (self.tmp / "watcher.log").read_text())
        self.assertNotIn("private original instruction", str(self.command.call_args))

    def test_daemon_notifications_distinguish_delivery_and_confirmation(self):
        for trigger, expected in (("bb_daemon_interruption", "Resume sent"),
                                  ("bb_daemon_resume_queued", "Resume queued"),
                                  ("bb_daemon_recovery_confirmed", "Recovery confirmed"),
                                  ("bb_daemon_recovery_unconfirmed", "Recovery unconfirmed")):
            text = outcomes.notify.revive_message("bb", "codex", 0, "test", True, trigger=trigger)
            self.assertTrue(text.startswith(expected))
            self.assertIn("BB daemon interruption", text)
            self.assertNotIn("provider error", text)


class SubmissionRegressionTests(unittest.TestCase):
    def test_real_rejected_instruction_is_retried_without_an_outage(self):
        thread = {"id": REF, "providerId": "codex", "status": "error", "title": "test"}
        def read(args):
            if args == ["thread", "list"]:
                return [thread]
            if args[:2] == ["thread", "show"]:
                return {"thread": thread, "pendingTodos": None}
            if args[:3] == ["thread", "queue", "list"]:
                return []
            if args[:3] == ["thread", "interactions", "list"]:
                return []
            return EVENTS
        now = ERROR_AT + timedelta(seconds=31)
        with mock.patch.object(host, "available", return_value=True), \
                mock.patch.object(host, "bb_json", side_effect=read), \
                mock.patch.object(host, "run_bb", return_value=mock.Mock(
                    returncode=0, stdout='{"ok":true,"delivery":"sent"}', stderr="")) as command, \
                mock.patch.object(revive, "log"), mock.patch.object(revive, "announce"), \
                tempfile.TemporaryDirectory() as tmp, \
                mock.patch("immortal.core.logbook.STATE_PATH", Path(tmp) / "state.json"), \
                mock.patch("immortal.core.logbook.LOG_PATH", Path(tmp) / "watcher.log"):
            revive.revive_pass({"revived": {}}, (now.isoformat(), now.isoformat()), "provider")
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0][:5], ["thread", "retry", REF, "--turn", REQUEST])


class ErrorParsingTests(unittest.TestCase):
    def test_real_failure_has_request_identity_and_timestamp(self):
        error = host.last_error(EVENTS, "codex")
        self.assertEqual(error["at"], ERROR_AT)
        self.assertEqual(error["submission"]["request_id"], REQUEST)
        self.assertEqual(error["submission"]["original_request_id"], REQUEST)

    def test_new_request_clears_old_failure(self):
        later = {"type": "client/turn/requested", "data": {"requestId": "new"}}
        self.assertIsNone(host.last_error(EVENTS + [later], "codex"))

    def test_started_completed_or_interrupted_turn_clears_old_failure(self):
        for event in ({"type": "turn/started"},
                      {"type": "turn/input/accepted", "data": {"clientRequestId": REQUEST}},
                      {"type": "turn/completed", "data": {"status": "completed"}},
                      {"type": "turn/completed", "data": {"status": "interrupted"}}):
            with self.subTest(event=event):
                self.assertIsNone(host.last_error(EVENTS + [event], "codex"))

    def test_late_acceptance_from_another_request_does_not_hide_failure(self):
        event = {"type": "turn/input/accepted", "data": {"clientRequestId": "earlier-request"}}
        self.assertEqual(host.last_error(EVENTS + [event], "codex")["submission"]["request_id"], REQUEST)

    def test_rejection_must_belong_to_current_request(self):
        events = copy.deepcopy(EVENTS)
        events[-2]["data"]["requestId"] = "another-request"
        self.assertIsNone(host.last_error(events, "codex"))

    def test_unrelated_system_error_is_not_a_submission(self):
        self.assertIsNone(host.last_error([EVENTS[0], EVENTS[-1]], "codex"))

    def test_retry_retains_original_identity(self):
        events = copy.deepcopy(EVENTS)
        events[0]["data"].update(requestId="retry", retryOfRequestId=REQUEST, retryAttempt=2)
        events[-2]["data"]["requestId"] = "retry"
        error = host.last_error(events, "codex")
        self.assertEqual(error["submission"], {
            "request_id": "retry", "original_request_id": REQUEST, "attempt": 2, "error_seq": 260})


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(logbook, "STATE_PATH", self.tmp / "state.json"))
        self.enterContext(mock.patch.object(logbook, "LOG_PATH", self.tmp / "watcher.log"))
        self.enterContext(mock.patch.object(bb_recovery, "log"))
        self.enterContext(mock.patch.object(outcomes, "log"))
        self.enterContext(mock.patch.object(outcomes.telemetry, "send"))
        self.ready = self.enterContext(mock.patch.object(host, "submission_ready", return_value=True))
        self.retry = self.enterContext(mock.patch.object(host, "retry_submission", return_value="sent"))
        self.enterContext(mock.patch.object(host, "_thread_events", return_value=[]))
        self.alert = mock.Mock()
        self.state = {"revived": {}}
        error = host.last_error(EVENTS, "codex")
        self.target = {"ref": REF, "id": REF, "status": "error", "title": "test", "harness_hint": "codex",
                       "error_at": ERROR_AT.isoformat(), "submission": error["submission"]}

    def run_at(self, seconds, detail=bb_recovery.TIMEOUTS[0]):
        return bb_recovery.recover(self.state, self.target, detail, ERROR_AT + timedelta(seconds=seconds), self.alert)

    def next_failure(self, attempt, seconds):
        self.target = copy.deepcopy(self.target)
        self.target["submission"].update(request_id=f"retry-{attempt}", attempt=attempt, error_seq=260 + attempt)
        self.target["error_at"] = (ERROR_AT + timedelta(seconds=seconds)).isoformat()

    def test_waits_thirty_seconds_then_retries(self):
        self.assertEqual(self.run_at(29), 0)
        self.retry.assert_not_called()
        self.assertEqual(self.run_at(30), 1)
        self.retry.assert_called_once_with(self.target, reset=False)

    def test_submission_retry_then_matching_output_saves_success_alert(self):
        sent = ERROR_AT + timedelta(seconds=30)
        with mock.patch.object(outcomes, 'now_iso', return_value=sent.isoformat()), mock.patch.object(
            outcomes.notify, 'webhook_url', return_value='https://example.invalid/hook'
        ):
            self.assertEqual(self.run_at(30), 1)
            self.assertFalse(self.state.get('discord_outbox'))
            events = [
                {'type': 'client/turn/requested', 'data': {'requestId': 'retry',
                    'retryOfRequestId': REQUEST, 'retryAttempt': self.target['submission']['attempt'] + 1}},
                {'type': 'turn/started', 'scope': {'turnId': 'retry-turn'}},
                {'type': 'turn/input/accepted', 'scope': {'turnId': 'retry-turn'},
                 'data': {'clientRequestId': 'retry'}},
                {'type': 'item/completed', 'createdAt': (sent.timestamp() + 5) * 1000,
                 'scope': {'turnId': 'retry-turn'},
                 'data': {'item': {'type': 'agentMessage', 'text': 'Back to work'}}},
            ]
            outcomes.check(self.state, lambda _: events, sent + timedelta(seconds=10))
            outcomes.check(self.state, lambda _: events, sent + timedelta(seconds=20))
        saved = logbook.load_state()
        self.assertEqual(saved['pending_revives'], {})
        self.assertEqual(len(saved['discord_outbox']), 1)
        self.assertIn('Recovery confirmed: Codex in bb · "test"', next(iter(saved['discord_outbox'].values()))['text'])

    def test_three_attempts_backoff_reset_once_then_one_alert(self):
        self.run_at(30)
        self.next_failure(2, 40)
        self.run_at(99)
        self.assertEqual(self.retry.call_count, 1)
        self.run_at(100)
        self.assertTrue(self.retry.call_args.kwargs["reset"])
        self.next_failure(3, 110)
        self.run_at(229)
        self.assertEqual(self.retry.call_count, 2)
        self.run_at(230)
        self.assertFalse(self.retry.call_args.kwargs["reset"])
        self.next_failure(4, 240)
        self.run_at(400)
        self.run_at(500)
        self.assertEqual(self.retry.call_count, 3)
        self.alert.assert_called_once()
        self.assertEqual(self.alert.call_args.kwargs["detail"], "submission_retries_exhausted")

    def test_budget_and_backoff_survive_watcher_restart(self):
        self.run_at(30)
        self.state = logbook.load_state()
        self.next_failure(2, 40)
        self.run_at(99)
        self.assertEqual(self.retry.call_count, 1)
        self.run_at(100)
        self.assertEqual(self.retry.call_count, 2)

    def test_reserves_budget_before_dispatch(self):
        def inspect(target, reset):
            entry = next(iter(logbook.load_state()["revived"].values()))
            self.assertEqual(entry["tries"], 1)
            self.assertEqual(entry["delivery"], "unknown")
            return "sent"
        self.retry.side_effect = inspect
        self.run_at(30)

    def test_missing_reply_never_blindly_resends_same_failed_request(self):
        self.retry.return_value = "unknown"
        self.run_at(30)
        self.state = logbook.load_state()
        self.run_at(100)
        self.run_at(200)
        self.retry.assert_called_once()
        self.alert.assert_called_once()

    def test_queued_or_cancelled_retry_is_not_submitted_twice(self):
        self.retry.return_value = "queued"
        self.run_at(30)
        self.run_at(100)
        self.retry.assert_called_once()

    def test_busy_newer_or_queued_work_is_left_alone(self):
        self.ready.return_value = False
        self.run_at(30)
        self.retry.assert_not_called()

    def test_failed_status_read_defers_recovery(self):
        self.ready.side_effect = host.BbUnavailable("offline")
        self.run_at(30)
        self.retry.assert_not_called()

    def test_permanent_and_unknown_errors_alert_without_retry(self):
        for detail in ("Not logged in", "Invalid model", "codex app-server exited", "permission denied"):
            with self.subTest(detail=detail):
                self.state = {"revived": {}}
                self.alert.reset_mock()
                self.run_at(30, detail)
                self.run_at(60, detail)
                self.retry.assert_not_called()
                self.alert.assert_called_once()

    def test_older_errors_do_not_restart_abandoned_work(self):
        self.run_at(bb_recovery.MAX_AGE_SECS + 1)
        self.retry.assert_not_called()

    def test_turn_start_timeout_is_also_recoverable(self):
        self.run_at(30, bb_recovery.TIMEOUTS[1])
        self.retry.assert_called_once()


class RetryCommandTests(unittest.TestCase):
    def setUp(self):
        error = host.last_error(EVENTS, "codex")
        self.target = {"ref": REF, "submission": error["submission"], "harness_hint": "codex"}

    def test_stale_request_before_reset_is_never_stopped(self):
        with mock.patch.object(host, "submission_ready", return_value=False), \
                mock.patch.object(host, "run_bb") as command:
            self.assertEqual(host.retry_submission(self.target, reset=True), "superseded")
        command.assert_not_called()

    def test_rechecks_request_after_reset(self):
        with mock.patch.object(host, "submission_ready", side_effect=[True, False]), \
                mock.patch.object(host, "run_bb", return_value=mock.Mock(returncode=0)) as command:
            self.assertEqual(host.retry_submission(self.target, reset=True), "superseded")
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0], ["thread", "stop", REF, "--json"])

    def test_failed_reset_does_not_send_input(self):
        with mock.patch.object(host, "submission_ready", return_value=True), \
                mock.patch.object(host, "run_bb", return_value=mock.Mock(returncode=1)) as command:
            self.assertEqual(host.retry_submission(self.target, reset=True), "reset_failed")
        self.assertEqual(command.call_count, 1)

    def test_cli_timeout_is_ambiguous_not_success(self):
        with mock.patch.object(host, "submission_ready", return_value=True), \
                mock.patch.object(host, "run_bb", side_effect=host.subprocess.TimeoutExpired("bb", 60)):
            self.assertEqual(host.retry_submission(self.target), "unknown")

    def test_no_retry_when_user_has_stopped_started_or_archived_thread(self):
        for thread in ({"status": "idle"}, {"status": "active"}, {"status": "starting"},
                       {"status": "error", "archivedAt": 1}, {"status": "error", "queuedMessageCount": 1},
                       {"status": "error", "hasPendingInteraction": True}):
            with self.subTest(thread=thread), mock.patch.object(host, "bb_json", return_value={"thread": thread}):
                self.assertFalse(host.submission_ready(self.target))

    def test_guard_rejects_new_request_or_queued_message(self):
        for queue, events in (([{}], EVENTS), ([], EVENTS + [{"type": "client/turn/requested", "data": {"requestId": "new"}}])):
            with self.subTest(queue=queue), mock.patch.object(host, "bb_json", side_effect=[{"thread": {"status": "error"}}, queue, []]), \
                    mock.patch.object(host, "_thread_events", return_value=events):
                self.assertFalse(host.submission_ready(self.target))

    def test_pending_approval_is_not_interrupted(self):
        with mock.patch.object(host, "bb_json", side_effect=[{"thread": {"status": "error"}}, [], [{}]]):
            self.assertFalse(host.submission_ready(self.target))


class SubmissionOutcomeTests(unittest.TestCase):
    def setUp(self):
        isolate_state(self)

    def test_only_output_from_the_retried_turn_confirms_recovery(self):
        sent = ERROR_AT.isoformat()
        retry = {"original_request_id": REQUEST, "attempt": 2}
        attempt = {"host": "bb", "ref": REF, "sent_at": sent, "bb_retry": retry}
        def requested(request_id, original=None):
            return {"type": "client/turn/requested", "data": {
                "requestId": request_id, "retryOfRequestId": original, "retryAttempt": 2}}
        def started(turn):
            return {"type": "turn/started", "scope": {"turnId": turn}}
        def output(turn):
            return {"type": "item/completed", "createdAt": int(ERROR_AT.timestamp() * 1000) + 5000,
                    "scope": {"turnId": turn}, "data": {"item": {"type": "agentMessage", "text": "Recovered"}}}
        for events in ([requested("unrelated"), started("other"), output("other")],
                       [requested("retry", REQUEST), started("retry-turn"), output("old-turn")],
                       [requested("retry", REQUEST), started("retry-turn"), requested("user"), output("retry-turn")]):
            with self.subTest(events=events):
                self.assertIsNone(outcomes._output_at(attempt, lambda ref: events))
        good = [requested("retry", REQUEST), started("retry-turn"),
                {"type": "turn/input/accepted", "scope": {"turnId": "retry-turn"},
                 "data": {"clientRequestId": "retry"}}, output("retry-turn")]
        self.assertIsNotNone(outcomes._output_at(attempt, lambda ref: good))

    def test_missing_error_details_log_and_alert_once(self):
        target = {"ref": REF, "id": REF, "status": "error", "title": "test", "harness_hint": "codex",
                  "error_at": None, "error_identity": 123}
        state = {"revived": {}}
        with mock.patch.object(host, "available", return_value=True), \
                mock.patch.object(host, "list_targets", return_value=[target]), \
                mock.patch.object(host, "read_screen", return_value=None), \
                mock.patch.object(revive, "announce") as alert, \
                mock.patch.object(revive, "log") as log:
            for _ in range(2):
                revive.revive_pass(state, (ERROR_AT.isoformat(), ERROR_AT.isoformat()), "provider")
        alert.assert_called_once()
        self.assertTrue(any(call.args[0] == "decision" for call in log.call_args_list))


if __name__ == "__main__":
    unittest.main()
