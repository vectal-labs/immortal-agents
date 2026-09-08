"""Recovery lifecycle boundaries with real state files, independent of wall time."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from immortal.core import logbook, outcomes
from support import isolate_state

NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


class ReportingLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = isolate_state(self)
        self.path = self.root / "session.jsonl"
        self.path.touch()
        self.state = {}
        self.enterContext(mock.patch.object(outcomes, "now_iso", return_value=NOW.isoformat()))
        self.enterContext(mock.patch.object(outcomes.telemetry, "send"))
        self.enterContext(mock.patch.object(outcomes.notify, "webhook_url", return_value=None))

    def track(self, harness="claude", host="terminal", **info):
        attempt = outcomes.track(self.state, host, "target", harness, {"path": str(self.path), **info})
        logbook.save_state(self.state)
        return attempt

    def append(self, row, seconds=1):
        with self.path.open("a") as stream:
            stream.write(json.dumps({"timestamp": (NOW + timedelta(seconds=seconds)).isoformat(), **row}) + "\n")

    def check(self, events=(), seconds=3600):
        outcomes.check(self.state, lambda ref: events, NOW + timedelta(seconds=seconds))

    def bb_events(self):
        return [
            {"seq": 1, "type": "client/turn/requested", "data": {"requestId": "resume",
             "input": [{"type": "text", "text": "keep going"}]}},
            {"seq": 2, "type": "turn/input/accepted", "scope": {"turnId": "recovered"},
             "data": {"clientRequestId": "resume"}},
        ]

    def bb_output(self, turn="recovered", kind="commandExecution", seconds=900):
        return {"seq": 10, "createdAt": (NOW.timestamp() + seconds) * 1000,
                "type": "item/completed", "scope": {"turnId": turn},
                "data": {"item": {"type": kind, "text": "Resumed", "exitCode": 0}}}

    def test_queued_followup_does_not_hide_accepted_recovery_progress(self):
        attempt = self.track(host="bb", harness="codex")
        events = self.bb_events() + [
            {"seq": 3, "createdAt": (NOW.timestamp() + 5) * 1000,
             "type": "client/turn/requested", "data": {"requestId": "queued",
             "input": [{"type": "text", "text": "Later work"}]}}, self.bb_output()]
        self.check(events)
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_different_accepted_request_ends_old_observation(self):
        attempt = self.track(host="bb", harness="codex")
        events = self.bb_events() + [
            {"seq": 3, "type": "client/turn/requested", "data": {"requestId": "new"}},
            {"seq": 4, "type": "turn/input/accepted", "scope": {"turnId": "new-turn"},
             "data": {"clientRequestId": "new"}}, self.bb_output()]
        self.check(events)
        self.assertNotIn(attempt, self.state["pending_revives"])
        self.assertFalse(self.state["discord_outbox"])

    def test_started_turn_without_matching_acceptance_is_not_success(self):
        self.track(host="bb", harness="codex")
        events = [self.bb_events()[0], {"seq": 2, "type": "turn/started",
                  "scope": {"turnId": "unrelated"}}, self.bb_output("unrelated")]
        self.check(events)
        self.assertFalse(self.state.get("discord_outbox"))
        self.assertTrue(self.state["pending_revives"])

    def test_bb_terminal_failure_or_interruption_cannot_be_confirmed_by_later_output(self):
        for status in ("failed", "interrupted", "completed"):
            with self.subTest(status=status):
                self.state = {}
                self.track(host="bb", harness="codex")
                events = self.bb_events() + [
                    {"seq": 3, "type": "turn/completed", "scope": {"turnId": "recovered"},
                     "data": {"status": status}}, self.bb_output()]
                self.check(events)
                self.assertFalse(self.state["pending_revives"])
                self.assertFalse(self.state["discord_outbox"])

    def test_retryable_error_remains_observable_for_delayed_tool_progress(self):
        attempt = self.track(host="bb", harness="codex")
        events = self.bb_events() + [
            {"seq": 3, "type": "provider/error", "scope": {"turnId": "recovered"},
             "data": {"willRetry": True}}]
        self.check(events)
        self.state = logbook.load_state()
        self.assertIn(attempt, self.state["pending_revives"])
        self.check(events + [self.bb_output(seconds=90000)], seconds=90001)
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_bb_replaced_provider_session_ends_observation(self):
        attempt = self.track(host="bb", harness="codex")
        self.state["pending_revives"][attempt]["provider_session_id"] = "old-session"
        events = [{"seq": 1, "type": "thread/identity", "data": {"providerThreadId": "new-session"}}]
        self.check(events + self.bb_events() + [self.bb_output()])
        self.assertFalse(self.state["pending_revives"])
        self.assertFalse(self.state["discord_outbox"])

    def test_partial_record_is_retried_after_restart(self):
        attempt = self.track()
        record = json.dumps({"timestamp": (NOW + timedelta(hours=2)).isoformat(),
                             "type": "assistant", "message": {"content": "Recovered"}})
        self.path.write_text(record[:25])
        self.check()
        self.state = logbook.load_state()
        with self.path.open("a") as stream:
            stream.write(record[25:] + "\n")
        self.check(seconds=7201)
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_unavailable_session_can_return_after_a_day(self):
        attempt = self.track()
        hidden = self.path.with_name("unmounted.jsonl")
        self.path.rename(hidden)
        self.check()
        self.state = logbook.load_state()
        hidden.rename(self.path)
        self.append({"type": "assistant", "message": {"content": "Recovered"}}, seconds=90000)
        self.check(seconds=90001)
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_claude_tool_results_and_metadata_do_not_supersede_recovery(self):
        attempt = self.track()
        self.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "A tool result", "tool_use_id": "call"}]}})
        self.append({"type": "user", "isMeta": True, "message": {"content": "Automatic context"}}, 2)
        self.append({"type": "user", "isCompactSummary": True,
                     "message": {"content": "Automatic compacted history"}}, 3)
        self.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "call2", "name": "Bash"}]}}, 901)
        self.check()
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_new_human_request_does_not_confirm_old_cli_recovery(self):
        for harness, user in (
            ("claude", {"type": "user", "message": {"content": "Different work"}}),
            ("pi", {"type": "message", "message": {"role": "user", "content": "Different work"}}),
            ("codex", {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "Different work"}]}}),
        ):
            with self.subTest(harness=harness):
                self.state = {}
                self.path.write_text("")
                self.track(harness=harness)
                self.append(user)
                output = ({"type": "response_item", "payload": {"role": "assistant", "type": "message",
                    "content": [{"type": "output_text", "text": "Unrelated progress"}]}}
                    if harness == "codex" else {"type": "assistant", "message": {"content": "Unrelated progress"}})
                self.append(output, 2)
                self.check()
                self.assertFalse(self.state["pending_revives"])
                self.assertFalse(self.state["discord_outbox"])

    def test_pi_tool_call_after_transient_error_is_progress(self):
        attempt = self.track(harness="pi")
        self.append({"type": "message", "message": {"role": "assistant", "stopReason": "error",
                     "content": [{"type": "text", "text": "Connection error"}]}})
        self.append({"type": "message", "message": {"role": "assistant", "stopReason": "toolUse",
                     "content": [{"type": "toolCall", "name": "read", "id": "call"}]}}, 1000)
        self.check()
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_codex_native_context_is_not_a_new_user_request(self):
        attempt = self.track(harness="codex")
        for index, kind in enumerate(("agents_md.instructions", "environments.environment_context", "plugins.recommendations")):
            self.append({"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Automatic context"}],
                "internal_chat_message_metadata_passthrough": {
                    "turn_id": "recovered", "content_item_kinds": [kind]}}}, index + 1)
        self.append({"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "keep going"}],
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "recovered", "content_item_kinds": ["user.text"]}}}, 4)
        self.append({"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}}, 1000)
        self.check()
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_codex_context_without_metadata_waits_for_authoritative_input(self):
        attempt = self.track(harness="codex")
        self.append({"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Automatic context"}]}}, 1)
        self.check()
        self.state = logbook.load_state()
        self.append({"type": "event_msg", "payload": {"type": "user_message", "message": "keep going"}}, 2)
        self.append({"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}}, 1000)
        self.check()
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_failed_save_keeps_in_memory_observation_and_retries_completion(self):
        attempt = self.track()
        self.append({"type": "assistant", "message": {"content": "Recovered"}})
        with mock.patch.object(outcomes, "save_state", side_effect=OSError("disk busy")):
            with self.assertRaises(OSError):
                self.check()
        self.assertIn(attempt, self.state["pending_revives"])
        self.assertFalse(self.state.get("discord_outbox"))
        self.check()
        self.assertIn(attempt, logbook.load_state()["discord_outbox"])

    def test_native_confirmation_before_watcher_output_does_not_alert_twice(self):
        attempt = self.track(harness="codex")
        self.state["recovery_confirmations"] = {"native": {
            "source": "native", "path": str(self.path), "turn_id": "recovered",
            "sent_at": (NOW + timedelta(seconds=1)).isoformat(),
            "confirmed_at": (NOW + timedelta(seconds=11)).isoformat()}}
        self.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "recovered"}}, 1)
        self.append({"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}}, 10)
        self.check()
        self.assertNotIn(attempt, self.state["pending_revives"])
        self.assertFalse(self.state["discord_outbox"])

    def test_native_confirmation_of_another_turn_does_not_hide_watcher_success(self):
        attempt = self.track(harness="codex")
        self.state["recovery_confirmations"] = {"native": {
            "source": "native", "path": str(self.path), "turn_id": "other",
            "sent_at": (NOW + timedelta(seconds=1)).isoformat(),
            "confirmed_at": (NOW + timedelta(seconds=11)).isoformat()}}
        self.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "recovered"}}, 1)
        self.append({"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}}, 10)
        self.check()
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_bb_native_dedup_uses_accepted_request_interval_not_bb_turn_id(self):
        attempt = self.track(host="bb", harness="codex")
        self.state["pending_revives"][attempt]["provider_session_id"] = "session"
        self.state["recovery_confirmations"] = {"native": {
            "source": "native", "provider_session_id": "session", "turn_id": "native-turn",
            "sent_at": (NOW + timedelta(seconds=10)).isoformat(),
            "confirmed_at": (NOW + timedelta(seconds=901)).isoformat()}}
        events = self.bb_events()
        events[1]["createdAt"] = (NOW.timestamp() + 5) * 1000
        self.check(events + [self.bb_output()])
        self.assertNotIn(attempt, self.state["pending_revives"])
        self.assertFalse(self.state["discord_outbox"])

    def test_native_recovery_before_bb_acceptance_does_not_hide_later_recovery(self):
        attempt = self.track(host="bb", harness="codex")
        self.state["pending_revives"][attempt]["provider_session_id"] = "session"
        self.state["recovery_confirmations"] = {"native": {
            "source": "native", "provider_session_id": "session", "turn_id": "native-turn",
            "sent_at": (NOW + timedelta(seconds=1)).isoformat(),
            "confirmed_at": (NOW + timedelta(seconds=2)).isoformat()}}
        events = self.bb_events()
        events[1]["createdAt"] = (NOW.timestamp() + 5) * 1000
        self.check(events + [self.bb_output()])
        self.assertIn(attempt, self.state["discord_outbox"])

    def test_bb_completion_checkpoint_maps_native_turn_for_dedup(self):
        attempt = self.track(host="bb", harness="codex")
        self.state["pending_revives"][attempt]["provider_session_id"] = "session"
        events = self.bb_events() + [self.bb_output(), {
            "seq": 11, "type": "turn/completed", "scope": {"turnId": "recovered"},
            "data": {"status": "completed", "providerCheckpointId": "native-turn"}}]
        self.check(events)
        self.assertEqual(self.state["recovery_confirmations"][attempt]["provider_turn_id"], "native-turn")


if __name__ == "__main__":
    unittest.main()
