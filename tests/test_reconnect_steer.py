"""ADR 0052: after a reconnect or wake, steer every still-active BB thread once."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import revive
import watcher
from immortal.core import logbook, ready, steering
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


class ReconnectSteerTests(unittest.TestCase):
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
        self.now = datetime.now(timezone.utc)
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
        self.fail(f"Unexpected BB read: {args}")

    def tells(self):
        return [call.args[0] for call in self.send.call_args_list if call.args[0][:2] == ["thread", "tell"]]

    def tick(self, online=True):
        self.probe.return_value = online
        watcher.loop()

    def reconnect(self, outage_secs=60):
        self.tick(online=False)
        self.state["outage_started_at"] = now_iso(self.now - timedelta(seconds=outage_secs))
        self.tick(online=True)

    def wake(self, seconds=1200):
        self.tick()
        self.state["steer"]["last_tick_at"] = now_iso(self.now - timedelta(seconds=seconds))
        self.tick()

    def episode(self):
        return self.state["steer"]["episode"]

    # Reproduction: before ADR 0052 nothing reached an active thread here.
    def test_short_outage_steers_active_thread_immediately(self):
        self.reconnect(60)
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertFalse(self.state.get("pending_recovery"))  # below MIN_OUTAGE_SECS

    def test_long_outage_steers_active_thread_and_keeps_failed_recovery_pending(self):
        self.enterContext(mock.patch.object(revive, "run_recheck"))
        self.reconnect(600)
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertTrue(self.state["pending_recovery"])

    def test_wake_while_online_steers_active_thread(self):
        self.wake()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_ordinary_online_ticks_send_nothing(self):
        for _ in range(3):
            self.tick()
        self.assertEqual(self.tells(), [])

    def test_brief_lid_close_already_online_steers_active_thread(self):
        self.wake(seconds=12)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_clock_jitter_is_not_a_wake(self):
        self.wake(seconds=3)
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
            thread("worker"),
        ]
        self.reconnect(60)
        self.assertEqual(self.tells(), [tell("worker")])

    def test_every_supported_provider_is_steered(self):
        self.threads = [thread(p, provider=p) for p in bb.PROVIDERS]
        self.reconnect(60)
        self.assertEqual(sorted(t[2] for t in self.tells()), sorted(bb.PROVIDERS))

    def test_waits_for_dns_readiness_then_sends_once(self):
        self.dns.return_value = False
        self.reconnect(60)
        self.tick()
        self.assertEqual(self.tells(), [])
        self.dns.return_value = True
        self.tick()
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])

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
        self.assertEqual(self.tells(), [tell("worker")])

    def test_candidates_are_frozen_at_the_reconnect(self):
        self.dns.return_value = False
        self.reconnect(60)
        self.threads.append(thread("new-work"))
        self.dns.return_value = True
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])

    def test_thread_that_finished_before_the_send_is_skipped(self):
        def read(args):
            if args[:2] == ["thread", "show"]:
                return {"thread": thread("worker", status="idle")}
            return self.read_bb(args)
        bb.bb_json.side_effect = read
        self.reconnect(60)
        self.tick()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_question_opened_while_readiness_was_delayed_blocks_the_send(self):
        self.dns.return_value = False
        self.reconnect(60)
        # Recorded from a live Claude Code thread blocked on AskUserQuestion (2026-09-10).
        self.interactions["worker"] = [{"id": "pint_zz4fedbwy4", "status": "pending", "resolvedAt": None,
                                        "payload": {"kind": "user_question"}}]
        self.dns.return_value = True
        self.tick()
        self.tick()
        self.assertEqual(self.tells(), [])
        self.assertEqual(self.episode()["sent"]["worker"]["delivery"], "superseded")

    def test_resolved_interactions_do_not_block_the_send(self):
        self.interactions["worker"] = [{"id": "pint_old", "status": "resolved", "resolvedAt": 5}]
        self.reconnect(60)
        self.assertEqual(self.tells(), [tell("worker")])

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

    def test_wake_gap_and_the_reconnect_after_it_are_one_episode(self):
        self.wake()
        self.reconnect(5)
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertEqual(self.episode()["reasons"], ["wake", "reconnect"])
        self.reconnect(5)  # the merge is single-use: this is a distinct reconnect
        self.assertEqual(self.tells(), [tell("worker"), tell("worker")])
        self.assertEqual(self.episode()["reasons"], ["reconnect"])

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
        self.tick()
        self.assertEqual(self.tells(), [tell("worker")])
        self.assertEqual(sorted(self.episode()["candidates"]), ["worker"])

    def test_a_second_reconnect_steers_again(self):
        self.reconnect(60)
        self.reconnect(30)
        self.assertEqual(self.tells(), [tell("worker"), tell("worker")])

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


if __name__ == "__main__":
    unittest.main()
