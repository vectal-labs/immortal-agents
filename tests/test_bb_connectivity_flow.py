"""Replay the September 8 compaction failure through the watcher and BB action."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import revive
import watcher
from immortal.core import logbook, ready
from immortal.hosts import bb
from support import isolate_state


class ConnectivityFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = isolate_state(self)
        self.events = json.loads((Path(__file__).parent / 'fixtures/bb_compaction_connection.json').read_text())
        self.now = datetime.now(timezone.utc)
        shift = self.now.timestamp() * 1000 - self.events[-1]['createdAt'] - 180_000
        for event in self.events:
            event['createdAt'] += shift
        self.thread = {'id': 'connection-fixture', 'status': 'error', 'providerId': 'codex',
                       'updatedAt': self.events[-1]['createdAt'], 'title': 'Connection fixture'}
        self.state = {'online': False, 'outage_started_at': (self.now - timedelta(minutes=5)).isoformat(),
                      'revived': {}}
        self.enterContext(mock.patch.dict(os.environ, WATCHER_ONCE='1'))
        self.enterContext(mock.patch.object(watcher, 'load_state', return_value=self.state))
        self.apple = self.enterContext(mock.patch.object(watcher, 'probe', return_value=False))
        self.enterContext(mock.patch.object(watcher.time, 'sleep'))
        self.enterContext(mock.patch.object(watcher, 'log'))
        self.enterContext(mock.patch.object(revive, 'announce'))
        self.enterContext(mock.patch.object(revive.telemetry, 'heartbeat'))
        self.enterContext(mock.patch.object(revive.telemetry, 'send'))
        self.enterContext(mock.patch.object(revive.discord_outbox, 'tick'))
        self.enterContext(mock.patch.object(revive.notify, 'webhook_url', return_value=None))
        self.enterContext(mock.patch.object(bb, 'available', return_value=True))
        self.enterContext(mock.patch.object(bb, '_LOG_CACHE', {}))
        self.enterContext(mock.patch.object(bb, 'bb_json', side_effect=self.read_bb))
        self.enterContext(mock.patch.object(ready, 'check', return_value=True))
        self.endpoint = self.enterContext(mock.patch.object(bb, 'recovery_endpoint',
            return_value='https://chatgpt.com/backend-api/codex', create=True))
        self.connection = self.enterContext(mock.patch.object(ready, 'check_endpoint',
            return_value='reachable', create=True))
        self.send = self.enterContext(mock.patch.object(bb, 'run_bb', return_value=SimpleNamespace(
            returncode=0, stdout='{"ok":true,"delivery":"sent"}', stderr='')))
        self.queue = []
        self.interactions = []

    def read_bb(self, args):
        if args == ['thread', 'list']:
            return [self.thread]
        if args[:2] == ['thread', 'log']:
            return self.events
        if args[:2] == ['thread', 'show']:
            return {'thread': self.thread}
        if args[:3] == ['thread', 'queue', 'list']:
            return self.queue
        if args[:3] == ['thread', 'interactions', 'list']:
            return self.interactions
        self.fail(f'Unexpected BB read: {args}')

    def test_apple_failure_does_not_block_reachable_codex(self):
        watcher.loop()
        self.send.assert_called_once()
        args = self.send.call_args.args[0]
        self.assertEqual(args[:3], ['thread', 'retry', self.thread['id']])
        self.assertIn('--turn', args)
        self.assertFalse(self.state['online'])
        saved = logbook.load_state()
        self.assertEqual(next(iter(saved['revived'].values()))['delivery'], 'sent')
        self.assertTrue(saved['pending_revives'])

    def tick(self):
        self.state.pop('provider_check_next_at', None)
        watcher.loop()

    def test_anthropic_dns_failure_cannot_block_codex(self):
        with mock.patch.object(ready, 'check', return_value=False):
            watcher.loop()
        self.send.assert_called_once()

    def test_failed_or_unknown_probe_does_not_consume_attempts(self):
        for status in ('unreachable', 'unknown'):
            self.connection.return_value = status
            self.tick()
            self.send.assert_not_called()
            self.assertFalse(self.state.get('revived'))
            self.assertFalse(self.state.get('pending_revives'))
        self.connection.return_value = 'reachable'
        self.tick()
        self.send.assert_called_once()

    def test_unknown_endpoint_does_not_default_to_openai(self):
        self.endpoint.return_value = None
        watcher.loop()
        self.connection.assert_not_called()
        self.send.assert_not_called()

    def test_apple_recovery_cannot_bypass_failed_provider_probe(self):
        self.connection.return_value = 'unreachable'
        self.tick()
        self.apple.return_value = True
        with mock.patch.object(revive, 'HOSTS', (bb,)):
            self.tick()
        self.send.assert_not_called()
        self.assertFalse(self.state.get('revived'))

    def test_new_stale_and_future_failures_do_not_retry(self):
        original = self.events[-2]['createdAt']
        for age in (30, 3600, -60):
            self.events[-2]['createdAt'] = self.now.timestamp() * 1000 - age * 1000
            bb._LOG_CACHE.clear()
            self.tick()
            self.send.assert_not_called()
        self.events[-2]['createdAt'] = original

    def test_queued_work_or_question_prevents_dispatch(self):
        self.queue = [{'id': 'new-user-work'}]
        self.tick()
        self.send.assert_not_called()
        self.assertFalse(self.state.get('pending_revives'))

    def test_pending_permission_prevents_dispatch(self):
        self.interactions = [{'id': 'permission'}]
        watcher.loop()
        self.send.assert_not_called()

    def test_user_stop_or_completed_work_invalidates_the_error(self):
        for status in ('interrupted', 'completed'):
            self.events[-1]['data']['status'] = status
            bb._LOG_CACHE.clear()
            self.tick()
            self.send.assert_not_called()

    def test_restart_and_later_apple_recovery_do_not_duplicate_retry(self):
        watcher.loop()
        self.state.clear()
        self.state.update(logbook.load_state())
        self.tick()
        self.apple.return_value = True
        with mock.patch.object(revive, 'HOSTS', (bb,)):
            self.tick()
        self.send.assert_called_once()

    def test_lost_cli_response_never_causes_a_second_retry(self):
        self.send.side_effect = bb.subprocess.TimeoutExpired('bb', 20)
        watcher.loop()
        self.assertEqual(next(iter(self.state['revived'].values()))['delivery'], 'unknown')
        self.state.clear()
        self.state.update(logbook.load_state())
        self.tick()
        self.send.assert_called_once()

    def test_accepted_retry_is_confirmed_only_by_matching_assistant_output(self):
        watcher.loop()
        attempt = next(iter(self.state['pending_revives'].values()))
        retry = attempt['bb_retry']
        now = datetime.now(timezone.utc).timestamp() * 1000 + 1000
        self.events.extend([
            {'type': 'client/turn/requested', 'createdAt': now, 'data': {
                'retryOfRequestId': retry['original_request_id'], 'retryAttempt': retry['attempt']}},
            {'type': 'turn/started', 'createdAt': now, 'scope': {'turnId': 'retry'}, 'data': {}},
        ])
        revive.outcomes.check(self.state, bb._thread_events)
        self.assertTrue(self.state['pending_revives'])
        self.events.append({'type': 'item/completed', 'createdAt': now + 1,
            'scope': {'turnId': 'retry'}, 'data': {'item': {'type': 'agentMessage', 'text': 'Work resumed'}}})
        revive.outcomes.check(self.state, bb._thread_events)
        self.assertFalse(self.state['pending_revives'])


if __name__ == '__main__':
    unittest.main()
