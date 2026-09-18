#!/usr/bin/env python3
"""Tests for Discord revive notifications."""

from __future__ import annotations

import json
import io
import threading
import unittest
from unittest import mock

from immortal.core import notify


class MessageTests(unittest.TestCase):
    def test_messages_distinguish_delivery_status_and_error_trigger(self):
        cases = [
            (("bb", "claude-code", 735, "Write Essay", True), 'Resume sent: Claude Code in bb · offline 12m 15s · "Write Essay"'),
            (("cmux", "codex", 3700, None, False), "Revive FAILED: Codex in cmux · offline 1h 1m"),
            (("bb", "codex", 0, None, None), "Resume status unknown: Codex in bb · offline 0s"),
            (("bb", "codex", 0, None, None, "at capacity", "unhandled_provider_error"), "Unhandled provider error: Codex in bb · at capacity"),
            (("bb", "pi", 0, "Essay", True, "at capacity", "provider_outage"), 'Resume sent: Pi in bb · provider outage · "Essay" · at capacity'),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(notify.revive_message(*args), expected)

    def test_no_webhook_is_a_silent_noop(self):
        with mock.patch.object(notify, "webhook_url", return_value=None), mock.patch(
            "immortal.core.notify.urllib.request.urlopen"
        ) as urlopen:
            self.assertFalse(notify.send("hi"))
        urlopen.assert_not_called()

    def test_send_posts_json_content(self):
        resp = mock.MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"id":"123456789"}'
        with mock.patch.object(notify, "webhook_url", return_value="https://x/hook"), mock.patch(
            "immortal.core.notify.urllib.request.urlopen"
        ) as urlopen:
            urlopen.return_value.__enter__.return_value = resp
            self.assertTrue(notify.send("hello"))
        req = urlopen.call_args.args[0]
        self.assertEqual(json.loads(req.data)["content"], "hello")
        self.assertIn("wait=true", req.full_url)

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
        resp.status = 200
        resp.read.return_value = b'{"id":"123456789"}'
        ok = mock.MagicMock()
        ok.__enter__.return_value = resp
        with mock.patch.object(notify, "webhook_url", return_value="https://x/hook"), mock.patch(
            "immortal.core.notify.urllib.request.urlopen", side_effect=[OSError("ENOTFOUND"), ok]
        ):
            self.assertTrue(notify.send("hello", sleep=mock.Mock()))

    def test_webhook_read_error_is_a_silent_noop(self):
        webhook_file = mock.Mock()
        webhook_file.is_file.return_value = True
        webhook_file.read_text.side_effect = OSError("unreadable webhook")
        with mock.patch.dict(notify.os.environ, {"DISCORD_WEBHOOK_URL": ""}), mock.patch.object(
            notify, "WEBHOOK_FILE", webhook_file
        ), mock.patch.object(notify, "post") as post:
            self.assertFalse(notify.send("hello"))
            self.assertFalse(notify.notify_revive("bb", "codex", 0, None, True))
        post.assert_not_called()

    def test_request_construction_error_never_raises(self):
        with mock.patch.object(notify, "webhook_url", return_value="invalid webhook"), mock.patch(
            "immortal.core.notify.urllib.request.urlopen"
        ) as urlopen:
            self.assertFalse(notify.send("hello", sleep=mock.Mock()))
        urlopen.assert_not_called()

    def test_unexpected_send_error_never_raises(self):
        with mock.patch.object(notify, "webhook_url", side_effect=RuntimeError("webhook error")):
            self.assertFalse(notify.send("hello"))
            self.assertFalse(notify.notify_revive("bb", "codex", 0, None, True))


class TransportTests(unittest.TestCase):
    def test_rate_limit_uses_header_even_with_malformed_body(self):
        for body in (b'null', b'[]', b'broken JSON', b'{"retry_after":"invalid"}',
                     b'{"retry_after":null}', b'{"retry_after":1e999}'):
            with self.subTest(body=body):
                error = notify.urllib.error.HTTPError('https://example.invalid/hook', 429, 'limited',
                                                     {'Retry-After': '120.25'}, io.BytesIO(body))
                with mock.patch.object(notify, '_rate_limit_until', 0), mock.patch.object(
                    notify.urllib.request, 'urlopen', side_effect=error
                ):
                    result = notify.post('https://example.invalid/hook', 'hello')
                self.assertFalse(result)
                self.assertEqual(result.error, 'http_429')
                self.assertEqual(result.retry_after, 120.25)

    def test_malformed_webhook_is_permanent_and_does_not_make_a_request(self):
        with mock.patch.object(notify.urllib.request, 'urlopen') as request:
            result = notify.post('https://[broken', 'hello')
        self.assertTrue(result.permanent)
        self.assertEqual(result.error, 'invalid_webhook')
        request.assert_not_called()

    def test_existing_wait_parameter_is_replaced_without_losing_thread(self):
        response = mock.MagicMock(status=200)
        response.read.return_value = b'{"id":"123456789"}'
        with mock.patch.object(notify.urllib.request, 'urlopen') as request:
            request.return_value.__enter__.return_value = response
            result = notify.post('https://example.invalid/hook?wait=false&thread_id=42', 'hello')
        self.assertTrue(result)
        query = notify.urllib.parse.parse_qs(notify.urllib.parse.urlsplit(request.call_args.args[0].full_url).query)
        self.assertEqual(query, {'wait': ['true'], 'thread_id': ['42']})


class NotificationQueueTests(unittest.TestCase):
    def wait_for_delivery(self):
        drained = threading.Event()

        def wait():
            notify._notifications.join()
            drained.set()

        threading.Thread(target=wait, daemon=True).start()
        self.assertTrue(drained.wait(3), "notification worker did not drain")

    def test_no_webhook_does_not_start_worker(self):
        with mock.patch.object(notify, "webhook_url", return_value=None), mock.patch.object(
            notify.threading, "Thread"
        ) as thread, mock.patch.object(notify, "post") as post:
            self.assertFalse(notify.notify_revive("bb", "codex", 0, None, True))
        thread.assert_not_called()
        post.assert_not_called()

    def test_slow_delivery_does_not_block_caller_and_queue_is_bounded(self):
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        results, delivery_threads = [], set()

        def blocked_post(*args):
            delivery_threads.add(threading.get_ident())
            entered.set()
            release.wait(3)
            return True

        def caller():
            results.append(notify.notify_revive("bb", "codex", 0, "first", True))
            returned.set()

        with mock.patch.object(notify, "webhook_url", return_value="https://example.invalid/hook"), mock.patch.object(
            notify, "post", side_effect=blocked_post
        ) as post:
            thread = threading.Thread(target=caller, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(1), "delivery never reached the fake post")
                self.assertTrue(returned.wait(0.1), "recovery caller waited for delivery")
                self.assertEqual(results, [True])
                for index in range(notify.MAX_PENDING_NOTIFICATIONS):
                    self.assertTrue(notify.notify_revive("bb", "codex", 0, str(index), True))
                self.assertFalse(notify.notify_revive("bb", "codex", 0, "overflow", True))
                self.assertEqual(post.call_count, 1)
            finally:
                release.set()
                thread.join(3)
                self.wait_for_delivery()
        self.assertEqual(post.call_count, notify.MAX_PENDING_NOTIFICATIONS + 1)
        self.assertEqual(len(delivery_threads), 1)
        self.assertNotIn(thread.ident, delivery_threads)
        self.assertNotIn(threading.get_ident(), delivery_threads)

    def test_delivery_failure_does_not_stop_worker(self):
        with mock.patch.object(notify, "webhook_url", return_value="https://example.invalid/hook"), mock.patch.object(
            notify, "post", side_effect=[RuntimeError("delivery failed"), True]
        ) as post:
            self.assertTrue(notify.notify_revive("bb", "codex", 0, "first", True))
            self.assertTrue(notify.notify_revive("bb", "codex", 0, "second", True))
            self.wait_for_delivery()
        self.assertEqual(post.call_count, 2)

    def test_retry_sleep_does_not_block_caller(self):
        sleeping, release, returned = threading.Event(), threading.Event(), threading.Event()
        results, sleep_threads = [], set()

        def blocked_sleep(delay):
            sleep_threads.add(threading.get_ident())
            sleeping.set()
            release.wait(3)

        def caller():
            results.append(notify.notify_revive("bb", "codex", 0, None, True))
            returned.set()

        with mock.patch.object(notify, "webhook_url", return_value="https://example.invalid/hook"), mock.patch.object(
            notify, "post", side_effect=[False, True]
        ) as post, mock.patch.object(notify.time, "sleep", side_effect=blocked_sleep) as sleep:
            thread = threading.Thread(target=caller, daemon=True)
            thread.start()
            try:
                self.assertTrue(sleeping.wait(1), "delivery never reached the retry delay")
                self.assertTrue(returned.wait(0.1), "recovery caller waited for the retry delay")
                self.assertEqual(results, [True])
            finally:
                release.set()
                thread.join(3)
                self.wait_for_delivery()
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(notify.RETRY_DELAYS[0])
        self.assertNotIn(thread.ident, sleep_threads)


if __name__ == "__main__":
    unittest.main()
