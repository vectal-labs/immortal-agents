"""Recovery runs through the watcher with real state files and fake host boundaries."""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import revive
import watcher
from immortal.core import logbook, outcomes, ready, revive_state


class RecoveryFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.multiple(logbook, STATE_DIR=self.root,
            STATE_PATH=self.root / 'state.json', LOG_PATH=self.root / 'watcher.log'))
        self.enterContext(mock.patch.object(watcher, 'log'))
        self.enterContext(mock.patch.object(revive, 'log'))
        self.enterContext(mock.patch.object(revive, 'announce'))
        self.enterContext(mock.patch.object(revive.telemetry, 'send'))
        self.enterContext(mock.patch.object(revive.telemetry, 'heartbeat'))
        self.enterContext(mock.patch.object(revive, 'run_provider_check'))
        self.enterContext(mock.patch.object(watcher.time, 'sleep'))
        self.enterContext(mock.patch.dict(os.environ, {'WATCHER_ONCE': '1'}))
        self.now = datetime.now(timezone.utc)
        self.loss = (self.now - timedelta(minutes=5)).isoformat()
        self.target = {'ref': 'one', 'id': 'one', 'cwd': '/tmp/test',
                       'title': 'claude', 'harness_hint': 'claude'}
        self.info = {'api_error_ts': self.loss, 'api_error': True}
        self.detector = self.enterContext(mock.patch.object(revive.claude_detect, 'evaluate',
            return_value=('resume', ['all_three_agree'], self.info)))
        self.host = SimpleNamespace(NAME='test', DETECTOR=None,
            available=mock.Mock(return_value=True),
            list_targets=mock.Mock(return_value=[self.target]),
            read_screen=mock.Mock(return_value='shift+tab to cycle\nAPI Error'),
            resume=mock.Mock(return_value='sent'))
        self.enterContext(mock.patch.object(revive, 'HOSTS', (self.host,)))

    def state(self):
        return {'online': False, 'outage_started_at': self.loss, 'revived': {}}

    def test_one_host_timeout_does_not_lose_later_hosts_or_recheck(self):
        bad = SimpleNamespace(NAME='broken', available=mock.Mock(return_value=True),
            list_targets=mock.Mock(side_effect=subprocess.TimeoutExpired('host', 20)))
        state = self.state()
        with mock.patch.object(ready, 'check', return_value=True), \
             mock.patch.object(watcher, 'load_state', return_value=state), \
             mock.patch.object(watcher, 'probe', return_value=True), \
             mock.patch.object(revive, 'HOSTS', (bad, self.host)):
            watcher.loop()
        self.host.resume.assert_called_once()
        self.assertTrue(state.get('recheck'))

    def test_failed_readiness_sends_nothing_and_keeps_durable_work(self):
        state = self.state()
        with mock.patch.object(ready, 'check', return_value=False), \
             mock.patch.object(watcher, 'load_state', return_value=state), \
             mock.patch.object(watcher, 'probe', return_value=True):
            watcher.loop()
        self.host.resume.assert_not_called()
        saved = logbook.load_state()
        self.assertTrue(saved.get('pending_recovery'))
        self.assertFalse(saved.get('last_api_ready_at'))

    def test_attempt_is_saved_before_external_send(self):
        def send(target):
            saved = logbook.load_state()
            entries = list(saved['revived'].values())
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['delivery'], 'unknown')
            self.assertEqual(len(saved['pending_revives']), 1)
            raise KeyboardInterrupt('watcher killed after delivery')
        self.host.resume.side_effect = send
        with self.assertRaises(KeyboardInterrupt):
            revive.revive_pass({}, (self.loss, self.now.isoformat()), 'first')

    def test_revival_then_assistant_output_saves_named_discord_success(self):
        session = self.root / 'session.jsonl'
        session.touch()
        self.info['path'] = str(session)
        self.target['title'] = 'Recover my project'
        state = {}
        with mock.patch.object(revive.notify, 'webhook_url', return_value='https://example.invalid/hook'):
            self.assertEqual(revive.revive_pass(state, (self.loss, self.now.isoformat()), 'first'), 1)
            self.assertFalse(state.get('discord_outbox'))
            stamp = self.now + timedelta(seconds=30)
            session.write_text(json.dumps({'type': 'assistant', 'timestamp': stamp.isoformat(),
                'message': {'content': [{'type': 'text', 'text': 'Resumed work'}]}}) + '\n')
            outcomes.check(state, mock.Mock(), stamp + timedelta(seconds=1))
        saved = logbook.load_state()
        self.assertEqual(saved['pending_revives'], {})
        self.assertEqual(len(saved['discord_outbox']), 1)
        self.assertIn('"Recover my project"', next(iter(saved['discord_outbox'].values()))['text'])

    def test_notification_label_does_not_change_recovery_deduplication(self):
        self.info.pop('api_error_ts')
        state = {}
        window = (self.loss, self.now.isoformat())
        revive.revive_pass(state, window, 'first')
        revive.revive_pass(state, window, 'recheck')
        self.host.resume.assert_called_once()

    def test_unknown_delivery_is_not_repeated_after_restart(self):
        self.host.resume.return_value = 'unknown'
        state = {}
        revive.revive_pass(state, (self.loss, self.now.isoformat()), 'first')
        saved = logbook.load_state()
        revive.revive_pass(saved, (self.loss, self.now.isoformat()), 'recheck')
        self.assertEqual(self.host.resume.call_count, 1)
        self.assertTrue(saved.get('pending_revives'))

    def test_definite_failure_is_retried_after_backoff(self):
        self.host.resume.return_value = 'not_sent'
        state = {}
        revive.revive_pass(state, (self.loss, self.now.isoformat()), 'first')
        revive.revive_pass(state, (self.loss, self.now.isoformat()), 'recheck')
        self.assertEqual(self.host.resume.call_count, 1)
        later = (self.now + timedelta(seconds=61)).isoformat()
        self.host.resume.return_value = 'sent'
        with mock.patch.object(revive, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = self.now + timedelta(seconds=61)
            revive.revive_pass(state, (self.loss, later), 'recheck')
        self.assertEqual(self.host.resume.call_count, 2)

    def test_slow_scan_and_failed_command_do_not_consume_retry_delay(self):
        dispatched = self.now + timedelta(minutes=1)
        finished = dispatched + timedelta(seconds=45)
        self.host.resume.return_value = 'not_sent'
        state = {}
        with mock.patch.object(revive, 'datetime', wraps=datetime) as dispatch_clock, \
             mock.patch.object(revive_state, 'datetime', wraps=datetime) as finish_clock:
            dispatch_clock.now.return_value = dispatched
            finish_clock.now.return_value = finished
            revive.revive_pass(state, (self.loss, self.now.isoformat()), 'first')
        entry = next(iter(state['revived'].values()))
        self.assertEqual(datetime.fromisoformat(entry['at']), dispatched)
        self.assertEqual(datetime.fromisoformat(entry['next_at']), finished + timedelta(seconds=30))
        with mock.patch.object(revive, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = finished + timedelta(seconds=10)
            revive.revive_pass(state, (self.loss, clock.now.return_value.isoformat()), 'recheck')
        self.assertEqual(self.host.resume.call_count, 1)

    def test_user_intervention_between_detection_and_send_prevents_input(self):
        self.detector.side_effect = [('resume', ['error'], self.info), ('skip', ['waiting_for_user'], self.info)]
        revive.revive_pass({}, (self.loss, self.now.isoformat()), 'first')
        self.host.resume.assert_not_called()

    def test_reservation_write_failure_does_not_create_a_phantom_send(self):
        state = {}
        with mock.patch.object(revive_state, 'save_state', side_effect=OSError('disk busy')):
            revive.revive_pass(state, (self.loss, self.now.isoformat()), 'first')
        self.host.resume.assert_not_called()
        self.assertFalse(state.get('pending_revives'))
        logbook.save_state(state)
        loaded = logbook.load_state()
        revive.revive_pass(loaded, (self.loss, self.now.isoformat()), 'recheck')
        self.host.resume.assert_called_once()

    def test_one_bad_target_does_not_block_later_targets(self):
        self.host.list_targets.return_value = [None, self.target]
        # A fresh read also ignores malformed entries.
        revive.revive_pass({}, (self.loss, self.now.isoformat()), 'first')
        self.host.resume.assert_called_once()

    def test_malformed_host_listing_does_not_block_other_hosts(self):
        bad = SimpleNamespace(NAME='broken', available=lambda: True, list_targets=lambda: None)
        with mock.patch.object(revive, 'HOSTS', (bad, self.host)):
            revive.revive_pass({}, (self.loss, self.now.isoformat()), 'first')
        self.host.resume.assert_called_once()

    def test_target_closing_after_scan_prevents_send(self):
        self.host.list_targets.side_effect = [[self.target], []]
        revive.revive_pass({}, (self.loss, self.now.isoformat()), 'first')
        self.host.resume.assert_not_called()

    def test_readiness_failure_survives_restart_then_recovery_confirms_output(self):
        path = self.root / 'session.jsonl'
        path.touch()
        self.info['path'] = str(path)
        state = self.state()
        with mock.patch.object(ready, 'check', return_value=False), \
             mock.patch.object(watcher, 'load_state', return_value=state), \
             mock.patch.object(watcher, 'probe', return_value=True):
            watcher.loop()
        saved = logbook.load_state()
        self.assertEqual(saved['pending_recovery']['loss_at'], self.loss)
        with mock.patch.object(ready, 'check', return_value=True), \
             mock.patch.object(watcher, 'probe', return_value=True):
            watcher.loop()  # load_state now reads the actual disk, like a restarted watcher.
        self.host.resume.assert_called_once()
        saved = logbook.load_state()
        self.assertIsNone(saved['pending_recovery'])
        self.assertEqual(saved['recheck']['loss_at'], self.loss)
        path.write_text(json.dumps({'type':'assistant', 'timestamp': datetime.now(timezone.utc).isoformat(),
            'message': {'content': [{'type':'text','text':'Recovered'}]}}) + '\n')
        with mock.patch.object(outcomes, 'log') as output_log:
            outcomes.check(saved, mock.Mock())
        self.assertEqual(output_log.call_args.args[0], 'revive_confirmed')
        self.assertFalse(saved['pending_revives'])

    def test_waiting_for_readiness_does_not_start_the_recheck_deadline(self):
        state = {}
        watcher.on_recovery(state, self.loss, self.now.isoformat(), 300)
        with mock.patch.object(ready, 'check', return_value=False), \
             mock.patch.object(revive, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = self.now + timedelta(hours=1)
            revive.run_recheck(state)
        self.assertTrue(state['pending_recovery'])
        self.assertIsNone(state['recheck'])
        self.host.resume.assert_not_called()

    def test_crash_before_first_pass_survives_restart_after_recheck_deadline(self):
        state = {}
        watcher.on_recovery(state, self.loss, self.now.isoformat(), 300)
        with mock.patch.object(ready, 'check', return_value=True), \
             mock.patch.object(revive, 'revive_pass', side_effect=KeyboardInterrupt('killed before scan')):
            with self.assertRaises(KeyboardInterrupt):
                revive.run_recheck(state)
        loaded = logbook.load_state()
        self.assertTrue(loaded['pending_recovery'])
        with mock.patch.object(ready, 'check', return_value=True), \
             mock.patch.object(revive, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = self.now + timedelta(minutes=16)
            revive.run_recheck(loaded)
        self.host.resume.assert_called_once()
        self.assertIsNone(loaded['pending_recovery'])
        self.assertTrue(loaded['recheck'])

    def test_legacy_identityless_attempt_is_not_sent_again(self):
        key = revive_state.target_key(self.host, self.target, self.info, (self.loss, ''), 'recheck')
        state = {'revived': {key: {'tries':1,'at':self.now.isoformat(),'error_at':None}}}
        revive.revive_pass(state, (self.loss, self.now.isoformat()), 'recheck')
        self.host.resume.assert_not_called()

    def test_unknown_after_actual_send_confirms_after_restart_without_resending(self):
        path = self.root / 'session.jsonl'
        path.touch()
        self.info['path'] = str(path)
        def sent_but_no_reply(target):
            path.write_text(json.dumps({'type':'assistant', 'timestamp':datetime.now(timezone.utc).isoformat(),
                'message':{'content':[{'type':'text','text':'Resumed'}]}})+'\n')
            raise KeyboardInterrupt('process killed before reply')
        self.host.resume.side_effect = sent_but_no_reply
        with self.assertRaises(KeyboardInterrupt):
            revive.revive_pass({}, (self.loss, self.now.isoformat()), 'first')
        loaded = logbook.load_state()
        with mock.patch.object(outcomes, 'log') as output_log:
            outcomes.check(loaded, mock.Mock())
        self.assertEqual(output_log.call_args.args[0], 'revive_confirmed')
        revive.revive_pass(loaded, (self.loss, self.now.isoformat()), 'recheck')
        self.assertEqual(self.host.resume.call_count, 1)

    def test_online_provider_checks_continue_when_recheck_raises(self):
        state = {'online':True,'revived':{}}
        with mock.patch.object(watcher, 'load_state', return_value=state), \
             mock.patch.object(watcher, 'probe', return_value=True), \
             mock.patch.object(revive, 'run_recheck', side_effect=OSError('failed read')):
            watcher.loop()
        revive.run_provider_check.assert_called_once()
