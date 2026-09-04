"""Prompt delivery and actual assistant output are separate recovery events."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import revive
from immortal.core import logbook, outcomes, revive_state
from immortal.detect import claude
from immortal.hosts import bb

NOW = datetime(2026, 9, 4, 10, 5, tzinfo=timezone.utc)


def record(harness, seconds=10, text="I continued the work.", error=False, user=False):
    row = {"timestamp": (NOW + timedelta(seconds=seconds)).isoformat()}
    role = "user" if user else "assistant"
    if harness == "codex":
        row.update(type="event_msg", payload={"type": "user_message" if user else "agent_message", "message": text})
    else:
        row.update(type=role if harness == "claude" else "message",
                   message={"role": role, "content": [{"type": "text", "text": text}]})
        if error:
            row["isApiErrorMessage"] = True
            row["message"]["stopReason"] = "error"
    return row


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(temp, "session.jsonl")
        self.path.touch()
        self.state = {}
        self.enterContext(mock.patch.object(outcomes, "now_iso", return_value=NOW.isoformat()))
        self.enterContext(mock.patch.object(outcomes, "log"))
        self.send = self.enterContext(mock.patch.object(outcomes.telemetry, "send"))
        self.bb_events = mock.Mock(return_value=[])

    def append(self, row):
        with self.path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    def track(self, harness="claude", host="terminal"):
        return outcomes.track(self.state, host, "target", harness, {"path": str(self.path)})

    def check(self, seconds=30):
        outcomes.check(self.state, self.bb_events, NOW + timedelta(seconds=seconds))

    def test_only_new_assistant_output_confirms_each_cli(self):
        for harness in ("claude", "codex", "pi"):
            with self.subTest(harness=harness):
                self.state = {}
                self.send.reset_mock()
                # Existing output cannot confirm a new attempt, even with a recent timestamp.
                self.append(record(harness))
                attempt_id = self.track(harness)
                self.append(record(harness, user=True, text="keep going"))
                self.append(record(harness, text="Error: Connection lost", error=True))
                self.check()
                self.send.assert_not_called()
                self.append(record(harness, seconds=40))
                self.check(60)
                self.check(90)
                self.send.assert_called_once_with(
                    "revive_confirmed", attempt_id=attempt_id, host="terminal", harness=harness,
                    reason="assistant_output", elapsed_secs=40.0)
                self.assertEqual(self.state["pending_revives"], {})

    def test_missing_output_expires_as_unconfirmed(self):
        self.track()
        self.check(outcomes.OBSERVE_SECS)
        self.assertEqual(self.send.call_args.args, ("revive_unconfirmed",))
        self.assertEqual(self.send.call_args.kwargs["reason"], "no_output_observed")
        self.assertEqual(self.state["pending_revives"], {})

    def test_late_output_does_not_confirm_an_expired_window(self):
        self.track()
        self.append(record("claude", seconds=outcomes.OBSERVE_SECS + 1))
        self.check(outcomes.OBSERVE_SECS + 30)
        self.assertEqual(self.send.call_args.args, ("revive_unconfirmed",))

    def test_observation_survives_state_save_and_reload(self):
        self.track()
        with mock.patch.object(logbook, "STATE_PATH", self.path.with_name("state.json")):
            logbook.save_state(self.state)
            self.state = logbook.load_state()
        self.append(record("claude"))
        self.check()
        self.assertEqual(self.send.call_args.args, ("revive_confirmed",))

    def test_replaced_session_file_cannot_confirm_recovery(self):
        self.track()
        other = self.path.with_name("other.jsonl")
        other.write_text(json.dumps(record("claude")) + "\n")
        other.replace(self.path)
        self.check(outcomes.OBSERVE_SECS)
        self.assertEqual(self.send.call_args.args, ("revive_unconfirmed",))

    def test_unknown_session_is_unconfirmed_not_success(self):
        outcomes.track(self.state, "ghostty", "target", "claude", {})
        self.check()
        self.assertEqual(self.send.call_args.kwargs["reason"], "no_session_log")

    def test_retries_do_not_count_the_same_output_twice(self):
        old = self.track()
        new = self.track()
        self.assertNotEqual(old, new)
        self.assertEqual(self.send.call_args.args, ("revive_unconfirmed",))
        self.assertEqual(self.send.call_args.kwargs["reason"], "retried")
        self.append(record("claude"))
        self.check()
        self.assertEqual([c.args[0] for c in self.send.call_args_list],
                         ["revive_unconfirmed", "revive_confirmed"])
        self.assertEqual(self.send.call_args.kwargs["attempt_id"], new)

    def test_output_before_retry_confirms_the_previous_attempt(self):
        old = self.track()
        self.append(record("claude", seconds=45))
        with mock.patch.object(outcomes, "now_iso", return_value=(NOW + timedelta(seconds=60)).isoformat()):
            new = self.track()
        self.assertEqual(self.send.call_args.args, ("revive_confirmed",))
        self.assertEqual(self.send.call_args.kwargs["attempt_id"], old)
        self.assertEqual(self.send.call_args.kwargs["elapsed_secs"], 45)
        self.check(60 + outcomes.OBSERVE_SECS)
        self.assertEqual(self.send.call_args.args, ("revive_unconfirmed",))
        self.assertEqual(self.send.call_args.kwargs["attempt_id"], new)

    def test_codex_tool_calls_confirm_work_but_tool_results_do_not(self):
        for kind in ("function_call", "custom_tool_call"):
            with self.subTest(kind=kind):
                self.state = {}
                self.send.reset_mock()
                self.track("codex")
                row = {"timestamp": (NOW + timedelta(seconds=10)).isoformat(), "type": "response_item",
                       "payload": {"type": kind + "_output", "output": "test output"}}
                self.append(row)
                self.check()
                self.send.assert_not_called()
                row["payload"] = {"type": kind, "name": "exec_command", "arguments": "{}", "input": "pwd"}
                self.append(row)
                self.check()
                self.assertEqual(self.send.call_args.args, ("revive_confirmed",))

    def test_bb_requires_assistant_output_from_the_target_thread(self):
        outcomes.track(self.state, "bb", "thread-1", "acp-cursor", {})
        def event(item_type, text):
            return {"type": "item/completed", "createdAt": (NOW.timestamp() + 10) * 1000,
                    "data": {"item": {"type": item_type, "text": text}}}
        self.bb_events.return_value = [event("userMessage", "keep going"), event("agentMessage", "Error: PING timed out")]
        self.check()
        self.send.assert_not_called()
        self.bb_events.return_value.append(event("agentMessage", "The tests now pass."))
        self.check()
        self.bb_events.assert_called_with("thread-1")
        self.assertEqual(self.send.call_args.args, ("revive_confirmed",))
        # Telemetry contains no thread IDs, paths, prompts, or generated text.
        self.assertEqual(set(self.send.call_args.kwargs),
                         {"attempt_id", "host", "harness", "reason", "elapsed_secs"})

    def test_bb_read_failure_does_not_raise_or_claim_success(self):
        outcomes.track(self.state, "bb", "target", "codex", {})
        self.bb_events.side_effect = OSError("unavailable")
        self.check()
        self.send.assert_not_called()
        self.check(outcomes.OBSERVE_SECS)
        self.assertEqual(self.send.call_args.kwargs["reason"], "observation_unavailable")

    def test_recorded_cursor_recovery_confirms_only_after_output(self):
        events = json.loads((Path(__file__).parent / "fixtures/bb_thr_rsgzd56nhq_events.json").read_text())
        sent = datetime.fromtimestamp(1788472434020 / 1000, timezone.utc)
        with mock.patch.object(outcomes, "now_iso", return_value=sent.isoformat()):
            outcomes.track(self.state, "bb", "thr_rsgzd56nhq", "acp-cursor", {})
        self.bb_events.return_value = [event for event in events if event["createdAt"] <= 1788472434036]
        outcomes.check(self.state, self.bb_events, sent + timedelta(seconds=1))
        self.send.assert_not_called()
        self.bb_events.return_value = events
        outcomes.check(self.state, self.bb_events, sent + timedelta(seconds=30))
        self.assertEqual(self.send.call_args.args, ("revive_confirmed",))
        self.assertEqual(self.send.call_args.kwargs["elapsed_secs"], 6.2)

    def test_bad_records_do_not_hide_later_output(self):
        self.track()
        self.path.write_text('null\n{bad json}\n')
        self.append(record("claude"))
        self.check()
        self.assertEqual(self.send.call_args.args, ("revive_confirmed",))

    def test_failed_delivery_does_not_wait_for_output(self):
        host = mock.Mock(NAME="terminal")
        host.resume.return_value = False
        with mock.patch.object(revive, "log"), mock.patch.object(revive, "announce"):
            self.assertFalse(revive._resume(self.state, host, {"ref": "target"}, "claude", 120, "first", {}))
        self.assertEqual(self.state["pending_revives"], {})
        self.assertEqual(self.send.call_args.args, ("revive_attempt",))
        self.assertIs(self.send.call_args.kwargs["result"], False)

    def test_recovery_pass_and_online_tick_confirm_output_not_delivery(self):
        dead = record("claude", seconds=-60, error=True, text="API Error: Connection lost")
        self.append(dead)
        host = mock.Mock(NAME="terminal", DETECTOR=None)
        host.available.return_value = True
        host.list_targets.return_value = [{"ref": "target", "id": "target", "cwd": str(self.path.parent),
                                          "title": "Claude", "harness_hint": "claude"}]
        host.read_screen.return_value = "API Error: Connection lost"
        host.resume.side_effect = lambda ref: (self.append(record("claude", seconds=1, user=True, text="keep going")) or True)
        with mock.patch.object(revive, "HOSTS", (host,)), mock.patch.object(revive, "log"), \
                mock.patch.object(revive, "announce"), mock.patch.object(revive_state, "save_state"), \
                mock.patch.object(claude, "find_jsonl", return_value=self.path), \
                mock.patch.object(bb, "available", return_value=False):
            revive.revive_pass(self.state, ((NOW - timedelta(minutes=4)).isoformat(), NOW.isoformat()), "first")
            host.resume.assert_called_once_with("target")
            attempt = [c for c in self.send.call_args_list if c.args[0] == "revive_attempt"][0]
            self.assertTrue(attempt.kwargs["result"])
            self.assertNotIn("revive_confirmed", [c.args[0] for c in self.send.call_args_list])
            self.append(record("claude", seconds=10))
            with mock.patch.object(revive, "datetime", wraps=datetime) as clock:
                clock.now.return_value = NOW + timedelta(seconds=30)
                revive.run_provider_check(self.state)
            confirmation = [c for c in self.send.call_args_list if c.args[0] == "revive_confirmed"]
            self.assertEqual(len(confirmation), 1)
            self.assertEqual(confirmation[0].kwargs["attempt_id"], attempt.kwargs["attempt_id"])


if __name__ == "__main__":
    unittest.main()
