"""ADR 0052: after a reconnect or wake, steer every still-active BB thread once."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import revive
import watcher
from immortal.core import common, logbook, ready, steering
from immortal.core.common import now_iso
from immortal.hosts import bb
from support import isolate_state

HOST = "host_local"


def tell(ref):
    return ["thread", "tell", ref, "keep going", "--mode", "auto", "--json"]


def thread(thread_id, status="active", provider="claude-code", host=HOST, **extra):
    row = {"id": thread_id, "status": status, "providerId": provider, "title": thread_id,
           "environmentHostId": host, "hasPendingInteraction": False, "updatedAt": 1}
    row.update(extra)
    return row


class ReconnectSteerCase(unittest.TestCase):
    def setUp(self):
        self.root = isolate_state(self)
        (self.root / "host-id").write_text(HOST + "\n")
        self.enterContext(mock.patch.dict(os.environ, WATCHER_ONCE="1", BB_DATA_DIR=str(self.root)))
        self.enterContext(mock.patch.object(watcher.time, "sleep"))
        self.enterContext(mock.patch.object(steering, "_last_mono", None))
        self.clock = self.enterContext(mock.patch.object(steering.time, "monotonic", return_value=1000.0))
        self.enterContext(mock.patch.object(revive.telemetry, "heartbeat"))
        self.enterContext(mock.patch.object(revive.telemetry, "send"))
        self.enterContext(mock.patch.object(revive.discord_outbox, "tick"))
        self.enterContext(mock.patch.object(bb, "available", return_value=True))
        self.enterContext(mock.patch.object(bb, "bb_json", side_effect=self.read_bb))
        self.endpoint = self.enterContext(mock.patch.object(bb, "recovery_endpoint", return_value=None))
        self.dns = self.enterContext(mock.patch.object(ready, "check", return_value=True))
        self.connection = self.enterContext(mock.patch.object(ready, "check_endpoint", return_value="unknown"))
        self.send = self.enterContext(mock.patch.object(bb, "run_bb", return_value=SimpleNamespace(
            returncode=0, stdout='{"ok":true,"delivery":"sent"}', stderr="")))
        self.threads = [thread("worker")]
        self.interactions = {}
        self.events = {}
        self.queues = {}
        self.now = datetime.now(timezone.utc)
        self.turn_started_at = (self.now - timedelta(minutes=5)).timestamp() * 1000
        for module in (watcher, revive, steering, common):
            clock = self.enterContext(mock.patch.object(module, "datetime", wraps=datetime))
            clock.now.side_effect = lambda *args: self.now
        self.send.side_effect = self.record_send
        self.state = {"online": True, "outage_started_at": None, "revived": {},
                      "last_probe_at": now_iso(self.now - timedelta(seconds=10))}
        self.enterContext(mock.patch.object(watcher, "load_state", side_effect=lambda: self.state))
        self.probe = self.enterContext(mock.patch.object(watcher, "probe", return_value=True))

    def read_bb(self, args):
        if args == ["thread", "list"]:
            return self.threads
        if args[:2] == ["thread", "show"]:
            # bb's raw thread object omits hasPendingInteraction; only the list row has it.
            shown = {k: v for k, v in next(t for t in self.threads if t["id"] == args[2]).items()
                     if k != "hasPendingInteraction"}
            return {"thread": shown}
        if args[:3] == ["thread", "interactions", "list"]:
            return self.interactions.get(args[3], [])
        if args[:3] == ["thread", "queue", "list"]:
            return self.queues.get(args[3], [])
        if args[:2] == ["thread", "log"]:
            return [{"seq": 0, "createdAt": self.turn_started_at, "type": "turn/started",
                     "scope": {"turnId": "turn-1"}, "data": {}}] + self.events.get(args[2], [])
        self.fail(f"Unexpected BB read: {args}")

    def event(self, kind, data, ref="worker", turn="turn-1"):
        events = self.events.setdefault(ref, [])
        events.append({"seq": len(events) + 1, "createdAt": self.now.timestamp() * 1000,
                       "type": kind, "scope": {"turnId": turn}, "data": data})

    def record_send(self, args):
        ref = args[2]
        request = f"request-{len(self.events.get(ref, []))}"
        self.event("client/turn/requested", {"requestId": request,
            "input": [{"type": "text", "text": "keep going"}],
            "target": {"kind": "auto", "expectedTurnId": "turn-1"}}, ref)
        self.event("turn/input/accepted", {"clientRequestId": request}, ref)
        return self.send.return_value

    def user_message(self, text="I already resumed this", ref="worker", **metadata):
        request = f"user-{len(self.events.get(ref, []))}"
        self.event("client/turn/requested", {
            "requestId": request, "initiator": "user", "senderThreadId": None,
            "input": [{"type": "text", "text": text}], **metadata,
        }, ref)
        self.event("turn/input/accepted", {"clientRequestId": request}, ref)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.clock.return_value += seconds

    def progress(self, ref="worker"):
        self.event("item/completed", {"item": {"type": "agentMessage", "text": "Continuing."}}, ref)

    def tells(self):
        return [call.args[0] for call in self.send.call_args_list if call.args[0][:2] == ["thread", "tell"]]

    def tick(self, online=True):
        self.probe.return_value = online
        watcher.loop()

    def reconnect(self, outage_secs=60, wait=True):
        # Real reconnects have distinct timestamps, even with our frozen clock.
        if self.state.get("last_recovery_at") == now_iso(self.now):
            self.advance(0.001)
        self.tick(online=False)
        self.state["outage_started_at"] = now_iso(self.now - timedelta(seconds=outage_secs))
        self.tick(online=True)
        if wait:
            self.settle()

    def wake(self, seconds=1200, wait=True):
        self.tick()
        self.state["steer"]["last_tick_at"] = now_iso(self.now - timedelta(seconds=seconds))
        self.tick()
        if wait:
            self.settle()

    def episode(self):
        return self.state["steer"]["episode"]

    def settle(self):
        self.advance(30)
        self.tick()


class ReconnectSteerTests(ReconnectSteerCase):
    def test_long_outage_steers_active_thread_and_keeps_failed_recovery_pending(self):
        self.enterContext(mock.patch.object(revive, "run_recheck"))
        self.reconnect(600)
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertTrue(self.state["pending_recovery"])

    def test_online_wakes_nudge_but_clock_jitter_does_not(self):
        for seconds, expected in ((1200, [tell("worker")]), (12, [tell("worker")]), (3, [])):
            with self.subTest(gap=seconds):
                self.state = {"online": True, "outage_started_at": None, "revived": {}}
                self.events.clear()
                self.send.reset_mock()
                self.wake(seconds=seconds)
                self.assertEqual(self.tells(), expected)

    def test_ordinary_online_ticks_send_nothing(self):
        for _ in range(3):
            self.tick()
        self.assertEqual(self.tells(), [])

    def test_slow_recovery_scan_is_not_a_wake(self):
        self.tick()
        self.state["steer"]["last_tick_at"] = now_iso(self.now - timedelta(seconds=120))
        self.clock.return_value += 120  # the process was busy, not asleep
        self.tick()
        self.assertEqual(self.tells(), [])

    def test_watcher_restart_is_not_a_wake(self):
        self.state["steer"] = {"last_tick_at": now_iso(self.now - timedelta(minutes=20)), "seen_recovery_at": None}
        self.tick()
        self.assertEqual(self.tells(), [])

    def test_first_run_ignores_an_old_persisted_reconnect(self):
        self.state["last_recovery_at"] = now_iso(self.now - timedelta(days=1))
        self.tick()
        self.assertEqual(self.tells(), [])
        self.reconnect(60)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_watcher_started_offline_still_sees_the_reconnect(self):
        self.state["online"] = False
        self.state["last_recovery_at"] = now_iso(self.now - timedelta(days=1))
        self.reconnect(60)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_skips_finished_archived_waiting_remote_and_unsupported_threads(self):
        self.threads = [
            thread("done", status="idle"),
            thread("failed", status="error"),
            thread("starting", status="starting"),
            thread("archived", archivedAt=5),
            thread("deleted", deletedAt=5),
            thread("asking", hasPendingInteraction=True),
            thread("remote", host="host_other"),
            thread("devin", provider="acp-devin-cli"),
            *[thread(p, provider=p) for p in bb.PROVIDERS],
        ]
        self.reconnect(60)
        self.assertEqual(sorted(t[2] for t in self.tells()), sorted(bb.PROVIDERS))

    def test_known_endpoint_is_gated_by_its_own_probe_not_by_dns(self):
        self.threads = [thread("codex", provider="codex"), thread("worker")]
        self.endpoint.side_effect = lambda target: (
            "https://chatgpt.com/backend-api/codex/responses" if target["ref"] == "codex" else None)
        self.dns.return_value = False
        self.connection.return_value = "reachable"
        self.reconnect(60)
        self.assertEqual(self.tells(), [tell("codex")])
        self.connection.return_value = "unreachable"
        self.dns.return_value = True
        self.tick()
        self.assertEqual(self.tells(), [tell("codex")])
        self.settle()
        self.assertEqual(self.tells(), [tell("codex"), tell("worker")])
        self.assertEqual(self.endpoint.call_args.args[0]["environment_host_id"], HOST)

    def test_unreachable_endpoint_blocks_only_its_thread(self):
        self.threads = [thread("codex", provider="codex"), thread("worker")]
        self.endpoint.side_effect = lambda target: "https://api.openai.com/v1/responses" if target["ref"] == "codex" else None
        self.connection.return_value = "unreachable"
        self.reconnect(60)
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_reconnect_invalidates_cached_readiness(self):
        invalidate = self.enterContext(mock.patch.object(ready, "invalidate"))
        endpoints = self.enterContext(mock.patch.object(ready, "close_endpoints"))
        self.reconnect(60)
        invalidate.assert_called()
        endpoints.assert_called()

    def test_no_dispatch_while_offline_even_with_cached_readiness(self):
        self.dns.return_value = False
        self.reconnect(60)
        self.dns.return_value = True
        self.tick(online=False)
        self.assertEqual(self.tells(), [])
        self.state["outage_started_at"] = now_iso(self.now)
        self.tick(online=True)
        self.assertEqual(self.tells(), [])
        self.settle()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_candidates_are_frozen_at_the_reconnect(self):
        self.dns.return_value = False
        self.reconnect(60)
        self.threads.append(thread("new-work"))
        self.dns.return_value = True
        self.tick()
        self.settle()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_user_message_before_reconnect_cancels_mistimed_nudge(self):
        # September 14: user input 16:37:44.653, reconnect 16:37:50.983,
        # an active command, then the unwanted steer at 16:38:06.521 UTC.
        self.enterContext(mock.patch.object(revive, "run_recheck"))
        self.threads = [thread("worker", provider="pi")]
        self.now = datetime(2026, 9, 14, 16, 37, 44, 653000, tzinfo=timezone.utc)
        self.user_message()
        self.advance(6.330343)
        self.dns.return_value = False
        self.reconnect(532.64, wait=False)
        self.advance(15.537657)
        self.event("item/started", {"item": {"type": "commandExecution"}})
        self.dns.return_value = True
        self.tick()
        self.assertEqual(self.tells(), [])
        self.settle()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")
        self.assertNotIn("worker", self.state["steer"]["last_sent"])

    def test_user_input_cutoff_blocks_only_recent_messages(self):
        self.threads = [thread("old"), thread("boundary"), thread("worker")]
        self.user_message(ref="old")
        self.advance(1)
        self.user_message(ref="boundary")
        self.advance(60)
        self.reconnect(60)
        self.assertEqual(self.tells(), [tell("old"), tell("worker")])

    def test_slow_recovery_scan_keeps_the_original_reconnect_cutoff(self):
        self.user_message()
        self.advance(5)
        self.enterContext(mock.patch.object(revive, "run_recheck", side_effect=lambda state: self.advance(120)))
        self.reconnect(60)
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_recent_input_does_not_age_out_while_readiness_is_delayed(self):
        self.user_message()
        self.advance(5)
        self.dns.return_value = False
        self.reconnect(60)
        self.advance(180)
        self.state = logbook.load_state()
        steering._last_mono = None
        self.dns.return_value = True
        self.tick()
        self.settle()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_user_input_during_readiness_wait_cancels_without_delayed_send(self):
        self.dns.return_value = False
        self.reconnect(60)
        self.advance(120)
        self.user_message()
        self.advance(120)
        self.dns.return_value = True
        self.tick()
        self.state = logbook.load_state()
        steering._last_mono = None
        self.advance(120)
        self.tick()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")
        self.reconnect(5)  # A later reconnect is independent, not a deferred nudge.
        self.assertEqual(self.tells(), [tell("worker")])

    def test_input_or_progress_arriving_during_preflight_cancels_nudge(self):
        self.threads = [thread("input"), thread("progress")]
        def read(args):
            if args == ["thread", "queue", "list", "input"]:
                self.user_message(ref="input")
            if args == ["thread", "log", "progress", "--all"]:
                self.event("item/reasoning/textDelta", {"delta": "Thinking"}, ref="progress")
            return self.read_bb(args)
        bb.bb_json.side_effect = read
        self.reconnect(60)
        self.assertEqual(self.tells(), [])
        for ref in ("input", "progress"):
            self.assertEqual(self.episode()["sent"][ref]["delivery"], "superseded")

    def test_user_input_before_wake_cancels_nudge(self):
        self.user_message()
        self.advance(5)
        self.wake(seconds=12)
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_wake_reconnect_merge_preserves_user_input_cutoff(self):
        self.user_message()
        self.advance(59)
        self.dns.return_value = False
        self.wake(seconds=12, wait=False)
        self.advance(50)
        self.reconnect(5, wait=False)
        self.dns.return_value = True
        self.tick()
        self.settle()
        self.assertEqual(self.episode()["reasons"], ["wake", "reconnect"])
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_user_input_guard_distinguishes_authors_and_content(self):
        messages = {
            "system": {"initiator": "system"},
            "agent": {"initiator": "agent", "senderThreadId": "other-thread"},
            "sender": {"senderThreadId": "other-thread"},
            "human": {},
            "manual": {"text": "keep going"},
            "image": {"input": [{"type": "localImage", "path": "example.png"}]},
        }
        self.threads = [thread(ref) for ref in messages]
        for ref, metadata in messages.items():
            self.user_message(ref=ref, **metadata)
        self.user_message(ref="human", initiator="system")  # Must not hide the user's input.
        self.progress(ref="manual")
        self.reconnect(60)
        self.assertEqual([args[2] for args in self.tells()], ["system", "agent", "sender"])
        for ref in ("human", "manual", "image"):
            with self.subTest(ref=ref):
                self.assertEqual(self.episode()["sent"][ref]["delivery"], "superseded")

    def test_user_input_with_bad_timestamp_defers_without_sending(self):
        self.user_message()
        stamp = self.events["worker"][0].pop("createdAt")
        self.reconnect(60)
        self.assertEqual(self.tells(), [])
        self.assertNotIn("worker", self.episode()["sent"])
        self.events["worker"][0]["createdAt"] = stamp
        self.tick()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_fresh_status_and_interactions_override_the_candidate_snapshot(self):
        self.threads = [thread(ref) for ref in ("finished", "question", "resolved", "worker")]
        self.dns.return_value = False
        self.reconnect(60)
        self.threads[0]["status"] = "idle"
        # Recorded from a live Claude Code thread blocked on AskUserQuestion (2026-09-10).
        self.interactions["question"] = [{"id": "pint_zz4fedbwy4", "status": "pending", "resolvedAt": None,
                                          "payload": {"kind": "user_question"}}]
        self.interactions["resolved"] = [{"id": "pint_old", "status": "resolved", "resolvedAt": 5}]
        self.dns.return_value = True
        self.tick()
        self.settle()
        self.tick()
        self.assertEqual(self.tells(), [tell("resolved"), tell("worker")])
        for ref in ("finished", "question"):
            self.assertEqual(self.episode()["sent"][ref]["delivery"], "superseded")

    def test_one_failed_endpoint_lookup_does_not_block_other_threads(self):
        self.threads = [thread("codex", provider="codex"), thread("worker")]
        def resolve(target):
            if target["ref"] == "codex":
                raise bb.BbUnavailable("bb thread log codex failed: timeout")
            return None
        self.endpoint.side_effect = resolve
        self.reconnect(60)
        self.assertEqual(sorted(t[2] for t in self.tells()), ["codex", "worker"])

    def retried_this_tick(self, ref):
        """Ordinary recovery runs after the probe stamp and before steering in the same tick."""
        def retry(state):
            state.setdefault("pending_revives", {})[ref] = {"host": "bb", "ref": ref, "sent_at": now_iso()}
        self.enterContext(mock.patch.object(revive, "run_recheck", side_effect=retry))

    def test_thread_just_retried_by_failed_recovery_is_not_nudged_again(self):
        self.retried_this_tick("worker")
        self.reconnect(60)
        self.tick()
        self.assertEqual(self.tells(), [])

    def test_prior_unconfirmed_recoveries_do_not_suppress_a_new_reconnect_nudge(self):
        self.threads = [thread("stuck"), thread("recent"), thread("worker")]
        self.state["pending_revives"] = {
            "old": {"host": "bb", "ref": "stuck", "sent_at": now_iso(self.now - timedelta(hours=1))},
            "recent": {"host": "bb", "ref": "recent", "sent_at": now_iso(self.now - timedelta(seconds=10))},
        }
        self.retried_this_tick("worker")
        self.reconnect(60)
        self.tick()
        self.assertEqual(sorted(t[2] for t in self.tells()), ["recent", "stuck"])

    def test_deferred_snapshot_still_excludes_retries_made_for_this_reconnect(self):
        bb.available.return_value = False
        self.retried_this_tick("worker")
        self.reconnect(60)
        bb.available.return_value = True
        self.tick()
        self.tick()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["candidates"], {})

    def test_no_duplicate_across_polls_and_watcher_restarts(self):
        self.reconnect(60)
        self.tick()
        self.state = logbook.load_state()  # restart: state comes back from disk only
        self.tick()
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_cloud_collector_reconnect_and_two_wakes_send_only_once(self):
        # September 11: requests 1570/1572/1574 were accepted on the same turn,
        # 45s then 132s apart, with no assistant output between them.
        self.reconnect(60)
        self.advance(45)
        self.wake(seconds=12, wait=False)
        self.advance(132)
        self.wake(seconds=12)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_cooldown_survives_restart_and_allows_new_trigger_at_two_minutes(self):
        self.reconnect(60)
        self.advance(1)
        self.progress()
        self.state = logbook.load_state()
        steering._last_mono = None
        self.advance(118)
        self.reconnect(5, wait=False)
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "cooldown")
        self.advance(1)
        self.reconnect(5, wait=False)
        self.assertNotIn("worker", self.episode()["sent"])
        self.settle()
        self.assertEqual(self.tells(), [tell("worker"), tell("worker")])

    def test_wake_gap_and_the_reconnect_after_it_are_one_episode(self):
        self.wake()
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertEqual(self.episode()["reasons"], ["wake", "reconnect"])
        self.reconnect(5)  # the merge is single-use: this is a distinct reconnect
        self.assertEqual(self.tells(), [tell("worker")])  # shared cooldown still applies
        self.assertEqual(self.episode()["reasons"], ["reconnect"])
        self.advance(120)
        self.progress()
        self.advance(0.001)  # Progress belongs to the previous connection, not the new one.
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker"), tell("worker")])

    def test_wake_with_bb_asleep_at_the_gap_tick_still_steers_after_reconnect(self):
        bb.available.return_value = False
        self.wake()
        bb.available.return_value = True
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_bb_enumeration_failure_defers_the_snapshot_instead_of_freezing_nothing(self):
        def unavailable(args):
            raise bb.BbUnavailable("bb thread list failed: ECONNREFUSED")
        bb.bb_json.side_effect = unavailable
        self.wake()
        self.tick()
        self.assertIsNone(self.episode()["candidates"])
        self.assertEqual(self.tells(), [])
        bb.bb_json.side_effect = self.read_bb
        self.tick()
        self.threads.append(thread("new-work"))  # after the snapshot: not this reconnect's work
        self.settle()
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertEqual(sorted(self.episode()["candidates"]), ["worker"])

    def test_a_second_reconnect_steers_again(self):
        self.reconnect(60)
        self.advance(120)
        self.progress()
        self.advance(0.001)
        self.reconnect(30)
        self.assertEqual(self.tells(), [tell("worker"), tell("worker")])

    def test_pending_send_survives_restart_and_episode_expiration(self):
        self.reconnect(60)
        self.state = logbook.load_state()
        steering._last_mono = None
        self.advance(1000)
        self.tick()
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "pending")

    def test_cooldown_skips_the_trigger_without_delaying_a_second_send(self):
        self.reconnect(60)
        self.advance(1)
        self.progress()
        self.reconnect(5)
        self.advance(120)
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_thread_cooldown_does_not_block_other_threads(self):
        self.reconnect(60)
        self.threads.append(thread("other"))
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker"), tell("other")])

    def test_old_state_keeps_its_last_send_when_a_new_episode_opens(self):
        self.reconnect(60)
        self.state["steer"].pop("last_sent")
        self.advance(1)
        self.progress()
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "cooldown")

    def test_queued_message_blocks_even_when_the_previous_send_was_handled(self):
        self.reconnect(60)
        self.advance(120)
        self.progress()
        self.queues["worker"] = [{"id": "queued", "content": [{"type": "text", "text": "keep going"}]}]
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_unrelated_queued_work_does_not_block_a_reconnect_nudge(self):
        self.queues["worker"] = [{"id": "queued", "content": [{"type": "text", "text": "Review when done"}]}]
        self.reconnect(60)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_latest_keep_going_is_checked_even_when_an_older_one_was_handled(self):
        self.reconnect(60)
        self.advance(1)
        self.progress()
        self.advance(120)
        self.record_send(tell("worker"))  # an independently sent nudge
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_unrelated_output_and_acceptance_do_not_clear_pending_send(self):
        self.reconnect(60)
        self.advance(120)
        self.event("item/completed", {"item": {"type": "agentMessage", "text": "Other turn"}}, turn="other")
        self.event("item/started", {"item": {"type": "agentMessage", "text": ""}})
        self.event("thread/tokenUsage/updated", {"totalTokens": 10})
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_rejected_send_does_not_block_future_reconnects_forever(self):
        self.reconnect(60)
        self.advance(120)
        self.event("client/turn/rejected", {"requestId": self.events["worker"][0]["data"]["requestId"]})
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker"), tell("worker")])

    def test_lost_cli_reply_stays_pending_across_new_triggers(self):
        self.send.side_effect = RuntimeError("lost reply")
        self.reconnect(60)
        self.send.side_effect = self.record_send
        self.advance(120)
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_unreadable_history_defers_the_send(self):
        def read(args):
            if args[:2] == ["thread", "log"]:
                raise bb.BbUnavailable("log unavailable")
            return self.read_bb(args)
        bb.bb_json.side_effect = read
        self.reconnect(60)
        self.assertEqual(self.tells(), [])
        bb.bb_json.side_effect = self.read_bb
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_slow_dispatch_does_not_shorten_the_cooldown(self):
        def slow_send(args):
            self.record_send(args)
            self.advance(90)
            return self.send.return_value
        self.send.side_effect = slow_send
        self.reconnect(60)
        self.send.side_effect = self.record_send
        self.advance(1)
        self.progress()
        self.advance(118)
        self.reconnect(5, wait=False)
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "cooldown")
        self.advance(1)
        self.reconnect(5, wait=False)
        self.assertNotIn("worker", self.episode()["sent"])
        self.settle()
        self.assertEqual(self.tells(), [tell("worker"), tell("worker")])

    def test_failed_reservation_does_not_leave_a_phantom_pending_send(self):
        def save(state):
            if state.get("steer", {}).get("last_sent", {}).get("worker", {}).get("delivery") == "unknown":
                raise OSError("state write failed")
            logbook.save_state(state)
        with mock.patch.object(steering, "save_state", side_effect=save):
            self.reconnect(60)
        self.assertEqual(self.tells(), [])
        self.state = logbook.load_state()
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_stale_episode_expires_without_sending(self):
        self.dns.return_value = False
        self.reconnect(60)
        self.episode()["until"] = now_iso(self.now - timedelta(seconds=1))
        self.dns.return_value = True
        self.tick()
        self.assertEqual(self.tells(), [])
        self.assertIsNone(self.state["steer"].get("episode"))

    def test_send_is_reserved_before_bb_is_called(self):
        def crash(args, **kwargs):
            raise RuntimeError("crash after bb accepted the steer")
        self.send.side_effect = crash
        self.reconnect(60)
        self.assertEqual(logbook.load_state()["steer"]["episode"]["sent"]["worker"]["delivery"], "unknown")
        self.send.side_effect = None
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])  # the crashed call only; no resend

    def test_bb_runtime_failure_retries_on_next_tick(self):
        self.send.side_effect = bb.BbRuntimeError("bb could not be started")
        self.reconnect(60)
        self.send.side_effect = None
        self.tick()
        self.assertEqual(self.tells(), [tell("worker"), tell("worker")])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "sent")


class ReconnectProgressTests(ReconnectSteerCase):
    def reconnect(self, outage_secs=60):
        super().reconnect(outage_secs, wait=False)

    def test_quiet_thread_gets_one_nudge_after_30_seconds(self):
        self.reconnect(60)
        self.assertFalse(self.state.get("pending_recovery"))  # below MIN_OUTAGE_SECS
        self.assertEqual(self.tells(), [])
        self.advance(29)
        self.tick()
        self.assertEqual(self.tells(), [])
        self.advance(1)
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])
        self.settle()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_grace_starts_when_provider_is_ready_and_survives_restart(self):
        self.dns.return_value = False
        self.reconnect(5)
        self.tick()
        self.assertEqual(self.tells(), [])
        self.advance(120)
        self.dns.return_value = True
        self.tick()
        self.assertEqual(self.tells(), [])
        self.advance(29)
        self.state = logbook.load_state()
        steering._last_mono = None
        self.tick()
        self.assertEqual(self.tells(), [])
        self.advance(1)
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_online_wake_waits_and_cancels_for_progress(self):
        self.wake(seconds=12, wait=False)
        self.assertEqual(self.tells(), [])
        self.advance(10)
        self.event("item/reasoning/textDelta", {"delta": "Thinking"})
        self.settle()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_offline_wake_merge_resets_grace_without_losing_progress(self):
        self.wake(seconds=12, wait=False)
        self.advance(5)
        self.progress()
        self.tick(online=False)
        self.state = logbook.load_state()
        self.assertNotIn("ready_at", self.episode()["candidates"]["worker"])
        self.advance(5)
        self.tick()
        self.assertEqual(self.episode()["reasons"], ["wake", "reconnect"])
        self.settle()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_upgrade_of_existing_episode_waits_before_sending(self):
        self.reconnect(5)
        self.episode()["candidates"]["worker"].pop("ready_at", None)
        logbook.save_state(self.state)
        self.state = logbook.load_state()
        steering._last_mono = None
        self.advance(120)
        self.tick()
        self.assertEqual(self.tells(), [])
        self.settle()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_provider_loss_resets_grace(self):
        self.reconnect(5)
        self.advance(20)
        self.dns.return_value = False
        self.tick()
        self.advance(20)
        self.dns.return_value = True
        self.tick()
        self.advance(29)
        self.tick()
        self.assertEqual(self.tells(), [])
        self.advance(1)
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_progress_cancels_episode_even_after_restart_and_long_silence(self):
        self.reconnect(5)
        self.advance(5)
        self.progress()
        self.settle()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")
        self.assertNotIn("worker", self.state["steer"]["last_sent"])
        self.state = logbook.load_state()
        steering._last_mono = None
        self.advance(300)
        self.tick()
        self.assertEqual(self.tells(), [])

    def test_progress_during_readiness_wait_still_cancels_nudge(self):
        self.dns.return_value = False
        self.reconnect(5)
        self.advance(5)
        self.progress()
        self.advance(120)
        self.dns.return_value = True
        self.tick()
        self.settle()
        self.assertEqual(self.tells(), [])

    def test_text_reasoning_and_tool_events_cancel_only_their_threads(self):
        events = [
            ("item/agentMessage/delta", {"delta": "Working"}),
            ("item/reasoning/textDelta", {"delta": "Thinking"}),
            ("item/reasoning/summaryTextDelta", {"delta": "Thinking"}),
            ("item/commandExecution/outputDelta", {"delta": "Still running"}),
            ("item/fileChange/outputDelta", {"delta": "Patched"}),
            ("item/plan/delta", {"delta": "Next step"}),
            ("item/toolCall/progress", {}),
            ("item/mcpToolCall/progress", {}),
            ("item/started", {"item": {"type": "commandExecution", "id": "cmd", "status": "pending"}}),
            ("item/completed", {"item": {"type": "toolCall", "id": "tool", "status": "completed"}}),
            ("item/completed", {"item": {"type": "reasoning", "summary": ["Thought"], "content": []}}),
            ("item/completed", {"item": {"type": "agentMessage", "text": "Working"}}),
        ]
        events += [("item/started", {"item": {"type": kind, "id": kind, "status": "pending"}})
                   for kind in ("fileChange", "fileRead", "webFetch", "delegation", "webSearch",
                                "search", "imageView", "imageGeneration", "planSteps", "contextCompaction")]
        self.threads += [thread(str(i)) for i in range(len(events))]
        self.reconnect(5)
        self.advance(5)
        for i, (kind, data) in enumerate(events):
            self.event(kind, data, ref=str(i))
        self.settle()
        self.assertEqual(self.tells(), [tell("worker")])
        for i in range(len(events)):
            self.assertEqual(self.episode()["sent"][str(i)]["delivery"], "superseded")

    def test_old_unrelated_empty_and_bookkeeping_events_do_not_block(self):
        self.progress()
        self.advance(1)
        self.reconnect(5)
        self.advance(5)
        self.event("item/completed", {"item": {"type": "agentMessage", "text": "Other turn"}}, turn="old")
        self.event("item/started", {"item": {"type": "agentMessage", "text": ""}})
        self.event("item/started", {"item": {"type": "reasoning", "summary": [], "content": []}})
        self.event("item/agentMessage/delta", {"delta": ""})
        self.event("thread/tokenUsage/updated", {"totalTokens": 10})
        self.event("turn/input/accepted", {"clientRequestId": "accepted"})
        self.settle()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_unfinished_command_is_left_alone_but_completed_old_one_is_not(self):
        self.threads += [thread("completed"), thread("old-turn")]
        for ref in ("worker", "completed", "old-turn"):
            self.event("item/started", {"item": {"type": "commandExecution", "id": "cmd", "status": "pending"}}, ref)
        self.event("item/completed", {"item": {"type": "commandExecution", "id": "cmd", "status": "completed"}}, "completed")
        self.event("turn/started", {}, "old-turn", turn="new-turn")
        self.advance(120)
        self.reconnect(5)
        self.settle()
        self.assertEqual(sorted(t[2] for t in self.tells()), ["completed", "old-turn"])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_completion_racing_status_read_cancels_nudge(self):
        self.reconnect(5)
        self.advance(10)
        self.event("turn/completed", {"status": "completed"})
        self.settle()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_new_turn_is_not_blocked_by_old_turn_output_or_commands(self):
        self.reconnect(5)
        self.advance(10)
        self.progress()
        self.event("item/started", {"item": {"type": "commandExecution", "id": "cmd", "status": "pending"}})
        self.event("turn/started", {}, turn="new-turn")
        self.settle()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_bad_progress_scope_defers_without_reserving_a_send(self):
        self.reconnect(5)
        self.progress()
        self.events["worker"][-1]["scope"] = None
        self.settle()
        self.assertEqual(self.tells(), [])
        self.assertNotIn("worker", self.episode()["sent"])
        self.assertNotIn("worker", self.state["steer"]["last_sent"])

    def test_missing_turn_or_progress_timestamp_defers_send(self):
        self.reconnect(5)
        self.advance(5)
        self.progress()
        self.events["worker"][-1].pop("createdAt")
        self.settle()
        self.assertEqual(self.tells(), [])
        self.assertNotIn("worker", self.episode()["sent"])
        def read(args):
            return [] if args[:2] == ["thread", "log"] else self.read_bb(args)
        bb.bb_json.side_effect = read
        self.settle()
        self.assertEqual(self.tells(), [])
        self.assertNotIn("worker", self.episode()["sent"])


if __name__ == "__main__":
    unittest.main()
