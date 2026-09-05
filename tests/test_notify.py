#!/usr/bin/env python3
"""Tests for Discord revive notifications."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import host_bb
import notify
import watcher


class MessageTests(unittest.TestCase):
    def test_revive_message_is_one_short_line(self):
        text = notify.revive_message("bb", "claude-code", 735, "Write Essay", True)
        self.assertEqual(text, 'Revived: Claude Code in bb · offline 12m 15s · "Write Essay"')

    def test_failed_revive_is_labelled(self):
        text = notify.revive_message("cmux", "codex", 3700, None, False)
        self.assertEqual(text, "Revive FAILED: Codex in cmux · offline 1h 1m")

    def test_no_webhook_is_a_silent_noop(self):
        with mock.patch.object(notify, "webhook_url", return_value=None), mock.patch(
            "notify.urllib.request.urlopen"
        ) as urlopen:
            self.assertFalse(notify.send("hi"))
        urlopen.assert_not_called()

    def test_send_posts_json_content(self):
        resp = mock.MagicMock()
        resp.status = 204
        with mock.patch.object(notify, "webhook_url", return_value="https://x/hook"), mock.patch(
            "notify.urllib.request.urlopen"
        ) as urlopen:
            urlopen.return_value.__enter__.return_value = resp
            self.assertTrue(notify.send("hello"))
        req = urlopen.call_args.args[0]
        self.assertEqual(json.loads(req.data)["content"], "hello")

    def test_network_error_never_raises_and_retries(self):
        sleep = mock.Mock()
        with mock.patch.object(notify, "webhook_url", return_value="https://x/hook"), mock.patch(
            "notify.urllib.request.urlopen", side_effect=OSError("down")
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
            "notify.urllib.request.urlopen", side_effect=[OSError("ENOTFOUND"), ok]
        ):
            self.assertTrue(notify.send("hello", sleep=mock.Mock()))


class WatcherWiringTests(unittest.TestCase):
    def test_bb_revive_triggers_notification(self):
        thread = {"id": "thr_x", "status": "error", "providerId": "claude-code", "title": "Essay"}
        with mock.patch.object(watcher, "log"), mock.patch.object(watcher, "save_state"), mock.patch.object(
            host_bb, "list_error_threads", return_value=[thread]
        ), mock.patch.object(host_bb, "thread_events", return_value=[]), mock.patch.object(
            host_bb, "evaluate", return_value=("resume", [], {})
        ), mock.patch.object(host_bb, "resume", return_value=(True, "ok")), mock.patch.object(
            notify, "notify_revive", return_value=True
        ) as ping:
            watcher.recover_bb_threads({"resumed": {}}, "2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z", 600)
        ping.assert_called_once_with("bb", "claude-code", 600, "Essay", True)


if __name__ == "__main__":
    unittest.main()
