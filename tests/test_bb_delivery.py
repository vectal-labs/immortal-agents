"""Ordinary bb recovery through target enumeration and CLI dispatch."""

import copy
import json
import subprocess
import unittest
from unittest import mock

from immortal.hosts import bb as host


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.thread = {
            "id": "thr_test", "providerId": "codex", "status": "error",
            "updatedAt": 1788185043065, "latestAttentionAt": 1788185043065,
            "hasPendingInteraction": False,
        }
        self.events = [
            {"seq": 1, "type": "client/turn/requested", "createdAt": 1788185000000,
             "data": {"requestId": "request-1", "input": [{"text": "private prompt"}]}},
            {"seq": 2, "type": "turn/started", "scope": {"turnId": "turn-1"}},
            {"seq": 3, "type": "provider/error", "createdAt": 1788185043065,
             "data": {"message": "Connection error", "willRetry": False}},
        ]
        self.queue = []
        self.interactions = []
        self.enterContext(mock.patch.object(host, "_LOG_CACHE", {}))
        self.enterContext(mock.patch.object(host, "_ERRORS", {}))
        self.enterContext(mock.patch.object(host, "log"))
        self.enterContext(mock.patch.object(host, "bb_json", side_effect=self.read_bb))
        self.command = self.enterContext(mock.patch.object(host, "run_bb", return_value=mock.Mock(
            returncode=0, stdout='{"ok":true,"delivery":"sent"}', stderr="")))

    def read_bb(self, args):
        if args == ["thread", "list"]:
            return [self.thread]
        if args[:2] == ["thread", "show"]:
            return {"thread": self.thread}
        if args[:3] == ["thread", "queue", "list"]:
            return self.queue
        if args[:3] == ["thread", "interactions", "list"]:
            return self.interactions
        if args[:2] == ["thread", "log"]:
            return self.events
        self.fail(f"Unexpected read: {args}")

    def target(self):
        return host.list_targets()[0]

    def test_pending_interaction_created_after_scan_prevents_dispatch(self):
        target = self.target()
        self.interactions.append({"id": "approval-new"})
        self.assertEqual(host.resume(target), "superseded")
        self.command.assert_not_called()

    def test_lost_delivery_reply_is_unknown_and_sent_once(self):
        target = self.target()
        self.command.side_effect = subprocess.TimeoutExpired(["bb", "thread", "retry"], 30)
        self.assertEqual(host.resume(target), "unknown")
        self.command.assert_called_once()

    def test_native_retry_guards_the_original_request(self):
        target = self.target()
        self.assertEqual(target["bb_retry"], {"original_request_id": "request-1", "attempt": 2})
        self.assertEqual(host.resume(target), "sent")
        self.command.assert_called_once_with(
            ["thread", "retry", "thr_test", "--turn", "request-1", "--json"])

    def test_native_retry_observation_follows_original_request_and_attempt(self):
        self.events[0]["data"].update(requestId="retry-3", retryOfRequestId="request-1", retryAttempt=3)
        target = self.target()
        self.assertEqual(target["bb_retry"], {"original_request_id": "request-1", "attempt": 4})
        self.assertEqual(host.resume(target), "sent")
        self.command.assert_called_once_with(
            ["thread", "retry", "thr_test", "--turn", "retry-3", "--json"])

    def test_queued_delivery_is_retained(self):
        self.command.return_value.stdout = '{"ok":true,"delivery":"queued","queuedMessageId":"q1"}'
        self.assertEqual(host.resume(self.target()), "queued")
        self.command.assert_called_once()

    def test_command_error_after_dispatch_is_unknown(self):
        self.command.return_value.returncode = 1
        self.command.return_value.stderr = "Lost connection after submitting"
        self.assertEqual(host.resume(self.target()), "unknown")
        self.command.assert_called_once()

    def test_unrecognized_delivery_reply_is_unknown(self):
        target = self.target()
        for reply in ("", "not-json", "[]", "null", "{}", '{"ok":false,"delivery":"sent"}',
                      '{"ok":true,"delivery":"accepted"}'):
            with self.subTest(reply=reply):
                self.command.return_value.stdout = reply
                self.assertEqual(host.resume(target), "unknown")

    def test_native_request_guard_rejection_is_superseded(self):
        target = self.target()
        for code in ("no_failed_turn", "retry_already_queued"):
            with self.subTest(code=code):
                self.command.return_value.returncode = 1
                self.command.return_value.stderr = code
                self.assertEqual(host.resume(target), "superseded")

    def test_user_thread_changes_prevent_dispatch(self):
        target = self.target()
        original = copy.deepcopy(self.thread)
        for field, value in (
            ("status", "active"), ("status", "starting"), ("status", "idle"),
            ("status", "stopping"), ("archivedAt", 123), ("deletedAt", 123),
            ("providerId", "claude-code"), ("hasPendingInteraction", True),
            ("queuedMessageCount", 1), ("activeBackgroundAgentCount", 1),
            ("updatedAt", original["updatedAt"] + 1),
        ):
            with self.subTest(field=field, value=value):
                self.thread = {**original, field: value}
                self.assertEqual(host.resume(target), "superseded")
                self.command.assert_not_called()

    def test_queued_work_created_after_scan_prevents_dispatch(self):
        target = self.target()
        self.queue.append({"id": "user-message"})
        self.assertEqual(host.resume(target), "superseded")
        self.command.assert_not_called()

    def test_new_request_with_identical_error_prevents_dispatch(self):
        target = self.target()
        self.events += [
            {"seq": 4, "type": "client/turn/requested", "data": {"requestId": "request-2"}},
            {**copy.deepcopy(self.events[-1]), "seq": 5},
        ]
        self.assertEqual(host.resume(target), "superseded")
        self.command.assert_not_called()

    def test_new_error_in_same_request_prevents_dispatch(self):
        target = self.target()
        self.events[-1]["seq"] += 1
        self.assertEqual(host.resume(target), "superseded")
        self.command.assert_not_called()

    def test_completed_or_stopped_work_prevents_dispatch(self):
        target = self.target()
        original = copy.deepcopy(self.events)
        for event in (
            {"type": "turn/completed", "data": {"status": "completed"}},
            {"type": "system/thread/interrupted", "data": {"reason": "user"}},
            {"type": "client/turn/requested", "data": {"requestId": "new"}},
        ):
            with self.subTest(event=event):
                self.events = [*original, event]
                self.assertEqual(host.resume(target), "superseded")
                self.command.assert_not_called()

    def test_observation_survives_later_scans_and_contains_no_prompt(self):
        target = self.target()
        before = copy.deepcopy(target)
        self.events[-1]["data"]["message"] = "Another failure"
        self.thread["updatedAt"] += 1
        host.list_targets()
        self.assertEqual(target, before)
        self.assertNotIn("private prompt", json.dumps(target))
        self.assertEqual(host.resume(target), "superseded")

    def test_idle_cursor_error_continues_with_auto_and_json(self):
        self.thread.update(providerId="acp-cursor", status="idle", latestAttentionAt=9999999999999)
        self.events[-1] = {
            "seq": 3, "type": "item/completed", "createdAt": 1788185043065,
            "data": {"item": {"type": "agentMessage", "text": "Error: Connection stalled"}},
        }
        self.events.append({"seq": 4, "type": "turn/completed", "data": {"status": "completed"}})
        target = self.target()
        self.assertIsNone(target["bb_retry"])
        self.assertEqual(host.resume(target), "sent")
        self.command.assert_called_once_with(
            ["thread", "tell", "thr_test", host.RESUME_TEXT, "--mode", "auto", "--json"])

    def test_unknown_preflight_shapes_never_dispatch(self):
        target = self.target()
        read = self.read_bb
        for route in ("show", "queue", "interactions", "log"):
            for malformed in (None, {}, "bad", [None]):
                with self.subTest(route=route, malformed=malformed):
                    def response(args):
                        return malformed if args[1] == route else read(args)
                    with mock.patch.object(host, "bb_json", side_effect=response):
                        self.assertEqual(host.resume(target), "not_sent")
                    self.command.assert_not_called()

    def test_malformed_event_data_prevents_dispatch(self):
        target = self.target()
        for field in ("data", "scope"):
            with self.subTest(field=field):
                self.events.append({"type": "client/turn/requested", field: []})
                self.assertEqual(host.resume(target), "not_sent")
                self.events.pop()
        self.command.assert_not_called()

    def test_unavailable_preflight_is_definitely_not_sent(self):
        target = self.target()
        with mock.patch.object(host, "bb_json", side_effect=host.BbUnavailable("closed")):
            self.assertEqual(host.resume(target), "not_sent")
        self.command.assert_not_called()

    def test_missing_runtime_cannot_dispatch(self):
        target = self.target()
        self.command.side_effect = host.BbRuntimeError("bb could not be started")
        self.assertEqual(host.resume(target), "not_sent")
        self.command.assert_called_once()

    def test_invalid_request_identity_never_falls_back_to_tell(self):
        target = self.target()
        self.events[0]["data"]["requestId"] = []
        self.assertEqual(host.resume(target), "not_sent")
        self.command.assert_not_called()

    def test_invalid_listing_is_conservatively_unavailable(self):
        for listing in ({}, None, "bad", [None]):
            with self.subTest(listing=listing), mock.patch.object(host, "bb_json", return_value=listing):
                self.assertEqual(host.list_targets(), [])

    def test_failed_thread_read_does_not_hide_later_targets(self):
        bad_thread = {**self.thread, "id": "thr_unreadable"}
        read = self.read_bb
        for failure in (host.BbUnavailable("bad events"), subprocess.TimeoutExpired(["bb"], 30),
                        {}, [{"type": "provider/error", "data": []}]):
            with self.subTest(failure=failure):
                def response(args):
                    if args == ["thread", "list"]:
                        return [bad_thread, self.thread]
                    if args[:3] == ["thread", "log", "thr_unreadable"]:
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                    return read(args)
                with mock.patch.object(host, "bb_json", side_effect=response):
                    self.assertEqual([t["ref"] for t in host.list_targets()], ["thr_test"])

    def test_malformed_listing_row_does_not_hide_other_targets(self):
        read = self.read_bb
        def response(args):
            if args == ["thread", "list"]:
                return [None, {"id": []}, self.thread]
            return read(args)
        with mock.patch.object(host, "bb_json", side_effect=response):
            self.assertEqual([t["ref"] for t in host.list_targets()], ["thr_test"])
