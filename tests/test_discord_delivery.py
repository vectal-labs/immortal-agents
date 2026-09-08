"""Recovery to HTTP delivery, with real state files and isolated child processes."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from immortal.core import logbook, notify, outcomes
from support import isolate_state


class DiscordDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.root = isolate_state(self)
        self.received = queue.Queue()
        self.responses = queue.Queue()
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.received.put((self.path, body))
                try:
                    status, payload = owner.responses.get_nowait()
                except queue.Empty:
                    status, payload = 200, {'id': '123456789'}
                if status == 'stall':
                    owner.release.wait(5)
                    status = 200
                if status == 'disconnect':
                    self.connection.close()
                    return
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.release.set)
        self.url = f'http://127.0.0.1:{self.server.server_port}/webhooks/test?thread_id=42'
        self.enterContext(mock.patch.object(notify, 'webhook_url', return_value=self.url))
        self.enterContext(mock.patch.object(outcomes.telemetry, 'send'))
        self.now = datetime.now(timezone.utc)

    def confirm(self, host='terminal', harness='claude', extra=None, finish=True):
        state = {}
        path = self.root / 'session.jsonl'
        path.write_text('')
        info = {'path': str(path), 'label': 'My task', 'offline_secs': 120,
                'trigger': 'network_outage', **(extra or {})}
        with mock.patch.object(outcomes, 'now_iso', return_value=self.now.isoformat()):
            attempt_id = outcomes.track(state, host, 'target', harness, info)
        logbook.save_state(state)
        stamp = self.now + timedelta(seconds=5)
        if harness == 'codex':
            row = {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'Working again'}}
        else:
            row = {'type': 'assistant' if harness == 'claude' else 'message',
                   'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Working again'}]}}
        path.write_text(json.dumps({**row, 'timestamp': stamp.isoformat()}) + '\n')
        events = [{'type': 'item/completed', 'createdAt': stamp.timestamp() * 1000,
                   'data': {'item': {'type': 'agentMessage', 'text': 'Working again'}}}]
        if finish:
            outcomes.check(state, lambda _: events, self.now + timedelta(seconds=10))
        return state, attempt_id

    def child(self, code, url=None):
        env = dict(os.environ, WATCHER_STATE_DIR=str(self.root), DISCORD_WEBHOOK_URL=url or self.url)
        return subprocess.run([sys.executable, '-c', code], env=env,
                              cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True, timeout=10)

    def drain(self):
        result = self.child('''
import time
from immortal.core import discord_outbox, logbook
state = logbook.load_state()
for _ in range(300):
    discord_outbox.tick(state)
    if not state.get('discord_outbox'):
        break
    time.sleep(.01)
else:
    raise AssertionError('outbox did not drain')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        return logbook.load_state()

    def attempt_once(self, now=None, url=None):
        result = self.child(f'''
import time
from immortal.core import discord_outbox, logbook
state = logbook.load_state()
before = sum(item['attempts'] for item in state['discord_outbox'].values())
now = {now!r} or time.time()
for _ in range(300):
    discord_outbox.tick(state, now=now)
    if not state['discord_outbox'] or sum(item['attempts'] for item in state['discord_outbox'].values()) > before:
        break
    time.sleep(.01)
else:
    raise AssertionError('no delivery result')
''', url=url)
        self.assertEqual(result.returncode, 0, result.stderr)
        return logbook.load_state()

    def idle_tick(self, now=None, url=None):
        result = self.child(f'''
import time
from immortal.core import discord_outbox, logbook
state = logbook.load_state()
discord_outbox.tick(state, now={now!r})
time.sleep(.1)
print(discord_outbox.status())
''', url=url)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.received.empty(), 'unexpected webhook request')
        return result.stdout

    def test_confirmed_recovery_is_saved_with_its_alert(self):
        state, attempt_id = self.confirm()
        saved = logbook.load_state()
        self.assertEqual(saved['pending_revives'], {})
        message = saved['discord_outbox'][attempt_id]['text']
        self.assertIn('Recovery confirmed: Claude Code in terminal', message)
        self.assertIn('My task', message)
        self.assertEqual(saved['discord_outbox'], state['discord_outbox'])

    def test_bb_submission_confirmation_has_a_success_alert(self):
        state, attempt_id = self.confirm('bb', 'codex')
        self.assertIn('Recovery confirmed: Codex in bb', state['discord_outbox'][attempt_id]['text'])

    def test_every_host_and_harness_uses_the_same_confirmation_path(self):
        for host, harness in [('terminal', 'claude'), ('cmux', 'codex'), ('ghostty', 'pi'),
                              ('bb', 'claude-code'), ('bb', 'codex'), ('bb', 'acp-cursor')]:
            with self.subTest(host=host, harness=harness):
                state, attempt_id = self.confirm(host, harness)
                self.assertEqual(len(state['discord_outbox']), 1)
                self.assertIn(f'in {host}', state['discord_outbox'][attempt_id]['text'])
                self.assertIn('My task', state['discord_outbox'][attempt_id]['text'])

    def test_pending_alert_is_delivered_by_a_new_process_and_not_repeated(self):
        _, attempt_id = self.confirm()
        saved = self.drain()
        path, body = self.received.get(timeout=1)
        self.assertIn('wait=true', path)
        self.assertIn('thread_id=42', path)
        self.assertIn('Recovery confirmed', body['content'])
        self.assertEqual(body['allowed_mentions'], {'parse': []})
        self.assertEqual(saved['discord_delivered'][attempt_id]['message_id'], '123456789')
        self.drain()
        self.assertTrue(self.received.empty())

    def test_temporary_failure_survives_restart_and_keeps_retrying(self):
        _, attempt_id = self.confirm()
        for index in range(5):
            self.responses.put((503, {'message': 'temporarily unavailable'}))
            saved = self.attempt_once(now=time.time() + index * 1000)
            self.received.get(timeout=1)
            self.assertEqual(saved['discord_outbox'][attempt_id]['attempts'], index + 1)
            self.assertEqual(saved['discord_outbox'][attempt_id]['error'], 'http_503')
        saved = self.attempt_once(now=time.time() + 6000)
        self.received.get(timeout=1)
        self.assertEqual(saved['discord_outbox'], {})
        self.assertEqual(saved['discord_delivered'][attempt_id]['message_id'], '123456789')

    def test_rate_limit_delay_is_persisted_and_honored_after_restart(self):
        _, attempt_id = self.confirm()
        self.responses.put((429, {'retry_after': 120.25}))
        now = time.time()
        saved = self.attempt_once(now=now)
        self.received.get(timeout=1)
        due = saved['discord_outbox'][attempt_id]['next_at']
        self.assertGreaterEqual(due, now + 120.25)
        self.idle_tick(now=due - 1)
        self.attempt_once(now=due + 1)
        self.received.get(timeout=1)

    def test_success_without_message_id_remains_pending(self):
        _, attempt_id = self.confirm()
        self.responses.put((204, {}))
        saved = self.attempt_once()
        self.received.get(timeout=1)
        self.assertIn(attempt_id, saved['discord_outbox'])
        self.assertNotIn(attempt_id, saved['discord_delivered'])

    def test_acknowledgement_can_include_a_long_unicode_message(self):
        self.responses.put((200, {'id': '123456789', 'content': '😀' * 1900}))
        result = notify.post(self.url, '😀' * 1900)
        self.assertEqual(result.message_id, '123456789')

    def test_deleted_webhook_pauses_until_configuration_changes(self):
        _, attempt_id = self.confirm()
        self.responses.put((404, {'message': 'Unknown Webhook'}))
        saved = self.attempt_once()
        self.received.get(timeout=1)
        status = self.idle_tick(now=time.time() + 1000)
        self.assertIn('http_404', status)
        self.assertIn(attempt_id, saved['discord_outbox'])
        self.attempt_once(url=self.url + '&replacement=1')
        self.received.get(timeout=1)

    def test_replacement_webhook_transient_failure_still_waits_before_retry(self):
        self.confirm()
        self.responses.put((404, {}))
        self.attempt_once()
        self.received.get(timeout=1)
        self.responses.put((500, {}))
        replacement = self.url + '&replacement=1'
        now = time.time()
        saved = self.attempt_once(now=now, url=replacement)
        self.received.get(timeout=1)
        self.assertGreater(next(iter(saved['discord_outbox'].values()))['next_at'], now)
        self.idle_tick(now=now, url=replacement)

    def test_manual_retry_signals_watcher_without_racing_state_file(self):
        self.confirm()
        self.responses.put((403, {}))
        self.attempt_once()
        self.received.get(timeout=1)
        before = logbook.STATE_PATH.read_bytes()
        result = self.child('''
import runpy, sys
sys.argv = ['discord_outbox', '--retry']
runpy.run_module('immortal.core.discord_outbox', run_name='__main__')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('retry requested', result.stdout)
        self.assertEqual(logbook.STATE_PATH.read_bytes(), before)
        self.assertTrue(self.received.empty())
        self.attempt_once()
        self.received.get(timeout=1)
        self.assertFalse(self.root.joinpath('discord-retry').exists())

    def test_manual_retry_does_not_override_discord_rate_limit(self):
        self.confirm()
        self.responses.put((429, {'retry_after': 120}))
        now = time.time()
        self.attempt_once(now=now)
        self.received.get(timeout=1)
        self.root.joinpath('discord-retry').touch()
        self.idle_tick(now=now + 1)

    def test_other_success_does_not_hide_a_permanently_blocked_alert(self):
        state, _ = self.confirm()
        from immortal.core import discord_outbox
        discord_outbox.stage(state, 'second', 'bb', 'codex', 'Another task')
        logbook.save_state(state)
        self.responses.put((400, {}))
        result = self.child('''
import time
from immortal.core import discord_outbox, logbook
state = logbook.load_state()
for _ in range(300):
    discord_outbox.tick(state)
    if state.get('discord_delivered'):
        break
    time.sleep(.01)
print(discord_outbox.status())
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('http_400', result.stdout)
        self.assertIn('1 pending', result.stdout)
        self.received.get(timeout=1)
        self.received.get(timeout=1)

    def test_disappearing_webhook_does_not_discard_an_enabled_recovery(self):
        state, attempt_id = self.confirm(finish=False)
        with mock.patch.object(notify, 'webhook_url', return_value=None):
            outcomes.check(state, None, self.now + timedelta(seconds=10))
        self.assertIn(attempt_id, logbook.load_state()['discord_outbox'])
        self.drain()
        self.received.get(timeout=1)

    def test_discord_disabled_does_not_accumulate_historical_messages(self):
        with mock.patch.object(notify, 'webhook_url', return_value=None):
            state, _ = self.confirm()
        self.assertFalse(state.get('discord_outbox'))

    def test_crash_before_atomic_completion_is_recovered_without_losing_alert(self):
        _, attempt_id = self.confirm(finish=False)
        stamp = (self.now + timedelta(seconds=10)).isoformat()
        result = self.child(f'''
import os
from datetime import datetime
from immortal.core import logbook, outcomes
state = logbook.load_state()
outcomes.save_state = lambda state: os._exit(17)
outcomes.check(state, None, datetime.fromisoformat({stamp!r}))
''')
        self.assertEqual(result.returncode, 17)
        saved = logbook.load_state()
        self.assertIn(attempt_id, saved['pending_revives'])
        outcomes.check(saved, None, self.now + timedelta(seconds=10))
        self.drain()
        self.received.get(timeout=1)
        self.assertTrue(self.received.empty())

    def test_failed_receipt_save_does_not_repeat_post_in_same_process(self):
        self.confirm()
        result = self.child('''
import time
from unittest import mock
from immortal.core import discord_outbox, logbook
state = logbook.load_state()
discord_outbox.tick(state)
time.sleep(.15)
with mock.patch.object(logbook, 'save_state', side_effect=OSError('disk temporarily unavailable')):
    for _ in range(100):
        try:
            discord_outbox.tick(state)
        except OSError:
            break
        time.sleep(.01)
    else:
        raise AssertionError('receipt save was not exercised')
for _ in range(100):
    discord_outbox.tick(state)
    if not state['discord_outbox']:
        break
    time.sleep(.01)
assert not state['discord_outbox']
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.received.get(timeout=1)
        self.assertTrue(self.received.empty())

    def test_stalled_request_does_not_block_watcher_and_restart_retries(self):
        self.confirm()
        self.responses.put(('stall', {'id': '123456789'}))
        started = time.monotonic()
        result = self.child('''
import time
from immortal.core import discord_outbox, logbook
state = logbook.load_state()
discord_outbox.tick(state)
time.sleep(.1)
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 2)
        self.received.get(timeout=1)
        self.assertTrue(logbook.load_state()['discord_outbox'])
        self.release.set()
        self.drain()
        self.received.get(timeout=1)

    def test_success_queue_has_no_memory_queue_size_drop(self):
        from immortal.core import discord_outbox
        state, _ = self.confirm()
        for index in range(notify.MAX_PENDING_NOTIFICATIONS + 5):
            discord_outbox.stage(state, str(index), 'bb', 'codex', f'Task {index}')
        logbook.save_state(state)
        expected = len(state['discord_outbox'])
        saved = self.drain()
        self.assertEqual(self.received.qsize(), expected)
        self.assertEqual(len(saved['discord_delivered']), expected)

    def test_large_retry_backlog_does_not_starve_later_alerts(self):
        result = self.child('''
import time
from unittest import mock
from immortal.core import discord_outbox, logbook, notify
state = {}
for index in range(40):
    discord_outbox.stage(state, str(index), 'bb', 'codex', f'Task {index}')
    state['discord_outbox'][str(index)]['attempts'] = 9
logbook.save_state(state)
with mock.patch.object(notify, 'post', return_value=notify.Delivery(error='http_503')):
    for index in range(500):
        discord_outbox.tick(state, now=index * 10)
        time.sleep(.002)
assert all(entry['attempts'] > 9 for entry in state['discord_outbox'].values())
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_pre_dispatch_save_keeps_alert_without_sending(self):
        self.confirm()
        result = self.child('''
from unittest import mock
from immortal.core import discord_outbox, logbook, notify
state = logbook.load_state()
with mock.patch.object(logbook, 'save_state', side_effect=OSError('disk unavailable')), mock.patch.object(notify, 'post') as post:
    try:
        discord_outbox.tick(state)
    except OSError:
        pass
    else:
        raise AssertionError('dispatch did not require persistence')
    post.assert_not_called()
assert logbook.load_state()['discord_outbox']
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.drain()
        self.received.get(timeout=1)

    def test_webhook_secret_never_appears_in_state_or_logs(self):
        self.confirm()
        self.responses.put((401, {'message': 'private details'}))
        self.attempt_once(url=self.url + '&token=VERY_PRIVATE_TOKEN')
        self.received.get(timeout=1)
        for path in (logbook.STATE_PATH, logbook.LOG_PATH):
            text = path.read_text()
            self.assertNotIn('VERY_PRIVATE_TOKEN', text)
            self.assertNotIn('private details', text)


if __name__ == '__main__':
    unittest.main()
