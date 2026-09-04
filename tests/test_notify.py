#!/usr/bin/env python3
"""Tests for Discord revive notifications."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from immortal.detect import bb as detect_bb
from immortal.hosts import bb as host_bb
from immortal.core import notify
import revive
from immortal.core import revive_state
import watcher


class MessageTests(unittest.TestCase):
    def test_revive_message_is_one_short_line(self):
        text = notify.revive_message("bb", "claude-code", 735, "Write Essay", True)
        self.assertEqual(text, 'Resume sent: Claude Code in bb · offline 12m 15s · "Write Essay"')

    def test_failed_revive_is_labelled(self):
        text = notify.revive_message("cmux", "codex", 3700, None, False)
        self.assertEqual(text, "Revive FAILED: Codex in cmux · offline 1h 1m")

    def test_provider_revive_includes_trigger_and_detail(self):
        text = notify.revive_message(
            "bb", "pi", 0, "Essay", True, "at capacity", "provider_outage"
        )
        self.assertEqual(text, 'Resume sent: Pi in bb · provider outage · "Essay" · at capacity')

    def test_no_webhook_is_a_silent_noop(self):
        with mock.patch.object(notify, "webhook_url", return_value=None), mock.patch(
            "immortal.core.notify.urllib.request.urlopen"
        ) as urlopen:
            self.assertFalse(notify.send("hi"))
        urlopen.assert_not_called()

    def test_send_posts_json_content(self):
        resp = mock.MagicMock()
        resp.status = 204
        with mock.patch.object(notify, "webhook_url", return_value="https://x/hook"), mock.patch(
            "immortal.core.notify.urllib.request.urlopen"
        ) as urlopen:
            urlopen.return_value.__enter__.return_value = resp
            self.assertTrue(notify.send("hello"))
        req = urlopen.call_args.args[0]
        self.assertEqual(json.loads(req.data)["content"], "hello")

    def test_network_error_never_raises_and_retries(self):
        sleep = mock.Mock()
        with mock.patch.object(notify, "webhook_url", return_value="https://x/hook"), mock.patch(
            "immortal.core.notify.urllib.request.urlopen", side_effect=OSError("down")
        ) as urlopen:
            self.assertFalse(notify.send("hello", sleep=sleep))
        self.assertEqual(urlopen.call_count, len(notify.RETRY_DELAYS) + 1)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], list(notify.RETRY_DELAYS))

    def test_send_succeeds_on_a_retry(self):
        # Experiment 0009: stale DNS right after reconnect; the second try lands.
        resp = mock.MagicMock()
        resp.status = 204
        ok = mock.MagicMock()
        ok.__enter__.return_value = resp
        with mock.patch.object(notify, "webhook_url", return_value="https://x/hook"), mock.patch(
            "immortal.core.notify.urllib.request.urlopen", side_effect=[OSError("ENOTFOUND"), ok]
        ):
            self.assertTrue(notify.send("hello", sleep=mock.Mock()))


class WatcherWiringTests(unittest.TestCase):
    def test_bb_revive_triggers_notification(self):
        target = {
            "ref": "thr_x", "id": "thr_x", "cwd": None, "title": "Essay",
            "harness_hint": "claude-code", "status": "error", "error_at": None,
        }
        with mock.patch.object(watcher, "log"), mock.patch.object(revive, "log"), mock.patch.object(watcher, "save_state"), mock.patch.object(revive_state, "save_state"), mock.patch.object(
            host_bb, "list_targets", return_value=[target]
        ), mock.patch.object(host_bb, "read_screen", return_value=None), mock.patch.object(
            detect_bb, "evaluate", return_value=("resume", [], {})
        ), mock.patch.object(host_bb, "resume", return_value=True), mock.patch.object(
            notify, "notify_revive", return_value=True
        ) as ping, mock.patch.object(
            host_bb, "available", return_value=True
        ), mock.patch.object(
            revive, "HOSTS", (host_bb,)
        ):
            revive.revive_pass(
                {"revived": {}},
                ("2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"),
                "first",
            )
        ping.assert_called_once_with("bb", "claude-code", 600, "Essay", True, None, None)


if __name__ == "__main__":
    unittest.main()
