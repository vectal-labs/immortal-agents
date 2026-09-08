"""Native retry observation through actual SQLite and rollout files."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from immortal.core import native_recovery


class NativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.rollout = self.home / 'rollout.jsonl'
        self.rollout.touch()
        with closing(sqlite3.connect(self.home / 'logs_2.sqlite')) as db, db:
            db.execute('CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, ts_nanos INTEGER, thread_id TEXT, target TEXT, feedback_log_body TEXT)')
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES (?, ?)', ('session-1', str(self.rollout)))
        self.saved = self.home / 'watcher.json'
        self.save = patch.object(native_recovery.logbook, 'save_state', side_effect=lambda state: self.saved.write_text(json.dumps(state))).start()
        self.addCleanup(patch.stopall)
        patch.object(native_recovery.logbook, 'log').start()
        patch.object(native_recovery.telemetry, 'send').start()
        patch.object(native_recovery.outcomes.notify, 'webhook_url', return_value='https://discord.com/api/webhooks/1/test').start()
        self.state = {}
        self.tick()

    def tick(self):
        native_recovery.tick(self.state, homes=[self.home])

    def retry(self, at=1000, turn='turn-1'):
        with closing(sqlite3.connect(self.home / 'logs_2.sqlite')) as db, db:
            db.execute('INSERT INTO logs (ts,ts_nanos,thread_id,target,feedback_log_body) VALUES (?,0,?,?,?)',
                       (at, 'session-1', 'codex_core::responses_retry',
                        f'stream disconnected - retrying sampling request (1/5) turn_id={turn} sampling_error=PRIVATE_ERROR'))

    def event(self, at, row_type, payload):
        row = {'timestamp': datetime.fromtimestamp(at, timezone.utc).isoformat(), 'type': row_type, 'payload': payload}
        with self.rollout.open('a') as stream:
            stream.write(json.dumps(row) + '\n')

    def output(self, at=1001, turn='turn-1', tool=False):
        self.event(at, 'turn_context', {'turn_id': turn})
        payload = ({'type': 'function_call', 'name': 'exec_command', 'arguments': 'PRIVATE_ARGUMENTS'} if tool else
                   {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'PRIVATE_OUTPUT'}]})
        self.event(at, 'response_item', payload)

    def test_success_is_saved_and_restart_does_not_repeat(self):
        self.retry()
        self.output()
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)
        attempt = next(iter(self.state['discord_outbox']))
        self.state = json.loads(self.saved.read_text())
        self.state['discord_outbox'] = {}
        self.state['discord_delivered'] = {attempt: {'message_id': 'receipt'}}
        self.tick()
        self.assertEqual(self.state['discord_outbox'], {})
        self.assertNotIn('PRIVATE', self.saved.read_text())

    def test_delayed_tool_progress_after_restart(self):
        self.retry()
        self.tick()
        self.state = json.loads(self.saved.read_text())
        self.output(at=90000, tool=True)
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_other_turn_never_confirms(self):
        self.retry()
        self.output(turn='other-turn')
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))

    def test_completion_without_output_is_not_success(self):
        self.retry()
        self.event(1001, 'turn_context', {'turn_id': 'turn-1'})
        self.event(1002, 'event_msg', {'type': 'task_complete', 'turn_id': 'turn-1'})
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))
        self.assertEqual(self.state['native_codex'][str(self.home)]['pending'], {})

    def test_repeated_retries_before_progress_are_one_recovery(self):
        self.retry()
        self.retry(at=1001)
        self.output(at=1002)
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_two_recoveries_in_same_turn_are_both_reported(self):
        self.retry()
        self.output()
        self.retry(at=1002)
        self.output(at=1003)
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 2)

    def test_pending_watcher_uses_its_existing_id(self):
        self.state['pending_revives'] = {'watcher-id': {
            'host': 'bb', 'harness': 'codex', 'provider_session_id': 'session-1', 'provider_turn_id': 'turn-1',
            'sent_at': datetime.fromtimestamp(900, timezone.utc).isoformat()}}
        self.retry()
        self.output()
        self.tick()
        self.assertEqual(list(self.state['discord_outbox']), ['watcher-id'])
        self.assertFalse(self.state['pending_revives'])

    def test_save_failure_preserves_retry_cursor_and_pending_watcher(self):
        self.retry()
        self.output()
        before = json.loads(json.dumps(self.state))
        self.save.side_effect = OSError('disk full')
        with self.assertRaises(OSError):
            self.tick()
        self.assertEqual(self.state, before)
        self.save.side_effect = lambda state: self.saved.write_text(json.dumps(state))
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_missing_rollout_is_retried(self):
        self.retry()
        self.rollout.unlink()
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))
        self.output()
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_first_install_does_not_replay_history(self):
        self.retry()
        self.output()
        self.state = {}
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))

    def test_partial_record_waits_for_newline(self):
        self.retry()
        self.event(1001, 'turn_context', {'turn_id': 'turn-1'})
        row = {'timestamp': datetime.fromtimestamp(1002, timezone.utc).isoformat(), 'type': 'event_msg',
               'payload': {'type': 'agent_message', 'message': 'done'}}
        with self.rollout.open('a') as stream:
            stream.write(json.dumps(row))
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))
        with self.rollout.open('a') as stream:
            stream.write('\n')
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_late_warning_after_watcher_confirmation_is_not_duplicated(self):
        self.state['recovery_confirmations'] = {'watcher': {
            'provider_session_id': 'session-1',
            'sent_at': datetime.fromtimestamp(900, timezone.utc).isoformat(),
            'confirmed_at': datetime.fromtimestamp(1001, timezone.utc).isoformat()}}
        self.retry()
        self.output()
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))

    def test_backlog_split_does_not_duplicate_one_episode(self):
        self.retry()
        self.retry(at=1001)
        self.output(at=1002)
        with patch.object(native_recovery, 'BATCH_SIZE', 1):
            self.tick()
            self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_log_rotation_does_not_replay_resolved_episodes(self):
        self.retry()
        self.output()
        self.tick()
        replacement = self.home / 'replacement.sqlite'
        with closing(sqlite3.connect(self.home / 'logs_2.sqlite')) as source:
            with closing(sqlite3.connect(replacement)) as target:
                source.backup(target)
        replacement.replace(self.home / 'logs_2.sqlite')
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_new_turn_closes_previous_pending_retry(self):
        self.event(999, 'turn_context', {'turn_id': 'turn-1'})
        self.retry()
        self.output(turn='replacement')
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))
        self.assertFalse(self.state['native_codex'][str(self.home)]['pending'])

    def test_real_app_server_recovery_capture(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/native_codex_recovery.json').read_text())
        retry = fixture['retry']
        with closing(sqlite3.connect(self.home / 'logs_2.sqlite')) as db, db:
            db.execute('INSERT INTO logs (ts,ts_nanos,thread_id,target,feedback_log_body) VALUES (?,?,?,?,?)',
                       tuple(retry[key] for key in ('ts', 'ts_nanos', 'thread_id', 'target', 'feedback_log_body')))
        self.rollout.write_text(''.join(json.dumps(row) + '\n' for row in fixture['rollout']))
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)
        self.assertFalse(self.state['native_codex'][str(self.home)]['pending'])
        self.state = json.loads(self.saved.read_text())
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def native_event(self, name, at, recovery_id='recovery-1'):
        with closing(sqlite3.connect(self.home / 'logs_2.sqlite')) as db, db:
            db.execute('INSERT INTO logs (ts,ts_nanos,thread_id,target,feedback_log_body) VALUES (?,0,?,?,?)',
                       (at, 'session-1', 'codex_core::recovery_reporting',
                        f'{name} turn_id=turn-1 recovery_id={recovery_id}'))

    def test_dedicated_native_confirmation_covers_ephemeral_session(self):
        self.rollout.unlink()
        self.native_event('recovery_started', 1000)
        self.native_event('recovery_confirmed', 1001)
        self.tick()
        self.assertEqual(list(self.state['discord_outbox']), ['native-codex-recovery-1'])
        self.assertFalse(self.state['native_codex'][str(self.home)]['pending'])

    def test_dedicated_event_without_retained_start_is_still_success(self):
        self.native_event('recovery_confirmed', 1001)
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_started_event_never_counts_as_success(self):
        self.native_event('recovery_started', 1000)
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))

    def test_dedicated_event_deduplicates_legacy_rollout_confirmation(self):
        self.retry()
        self.native_event('recovery_started', 1000)
        self.output(at=1002)
        self.tick()
        self.native_event('recovery_confirmed', 1003)
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_late_started_and_confirmed_events_deduplicate_rollout(self):
        self.retry()
        self.output(at=1002)
        self.tick()
        self.native_event('recovery_started', 1000)
        self.native_event('recovery_confirmed', 1003)
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_two_native_episodes_same_turn_both_reported(self):
        self.native_event('recovery_started', 1000)
        self.native_event('recovery_confirmed', 1001)
        self.native_event('recovery_started', 1002, 'recovery-2')
        self.native_event('recovery_confirmed', 1003, 'recovery-2')
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 2)

    def test_unaccepted_watcher_is_not_removed_by_native_progress(self):
        self.state['pending_revives'] = {'watcher-id': {
            'host': 'bb', 'harness': 'codex', 'provider_session_id': 'session-1',
            'sent_at': datetime.fromtimestamp(900, timezone.utc).isoformat()}}
        self.retry()
        self.output()
        self.tick()
        self.assertIn('watcher-id', self.state['pending_revives'])

    def test_native_first_receipt_survives_restart_for_watcher_dedupe(self):
        self.retry()
        self.output()
        self.tick()
        self.state = json.loads(self.saved.read_text())
        attempt_id = next(iter(self.state['discord_outbox']))
        receipt = self.state['recovery_confirmations'][attempt_id]
        self.assertEqual(receipt['source'], 'native')
        self.assertEqual(receipt['provider_session_id'], 'session-1')
        self.assertEqual(receipt['turn_id'], 'turn-1')
        self.assertEqual(receipt['path'], str(self.rollout))
        self.assertEqual(receipt['sent_at'], datetime.fromtimestamp(1000, timezone.utc).isoformat())
        self.assertEqual(receipt['confirmed_at'], datetime.fromtimestamp(1001, timezone.utc).isoformat())
        self.tick()
        self.assertEqual(self.state['recovery_confirmations'][attempt_id], receipt)

    def test_ephemeral_native_receipt_has_safe_session_label(self):
        self.rollout.unlink()
        self.native_event('recovery_started', 1000)
        self.native_event('recovery_confirmed', 1001)
        self.tick()
        self.state = json.loads(self.saved.read_text())
        receipt = self.state['recovery_confirmations']['native-codex-recovery-1']
        self.assertEqual(receipt['provider_session_id'], 'session-1')
        self.assertNotIn('path', receipt)
        self.assertIn('session-1', self.state['discord_outbox']['native-codex-recovery-1']['text'])

    def test_central_dedupe_does_not_create_native_receipt(self):
        self.retry()
        self.output()
        with patch.object(native_recovery.outcomes, 'stage_complete', return_value=None):
            self.tick()
        self.assertFalse(self.state.get('recovery_confirmations'))

    def metadata_item(self, at, turn, *, tool=False):
        payload = ({'type': 'function_call', 'name': 'exec_command'} if tool else
                   {'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': 'PRIVATE_OUTPUT'}]})
        payload['internal_chat_message_metadata_passthrough'] = {
            'turn_id': turn, 'content_item_kinds': ['assistant.text']}
        self.event(at, 'response_item', payload)

    def test_legacy_metadata_binds_same_turn_text(self):
        self.retry()
        self.metadata_item(1001, 'turn-1')
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_legacy_metadata_binds_same_turn_tool(self):
        self.retry()
        self.metadata_item(1001, 'turn-1', tool=True)
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_changed_legacy_metadata_cannot_confirm_previous_turn(self):
        self.metadata_item(999, 'turn-1')
        self.retry()
        self.metadata_item(1001, 'other-turn')
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))
        self.assertFalse(self.state['native_codex'][str(self.home)]['pending'])

    def test_legacy_metadata_user_context_binds_following_tool(self):
        self.event(999, 'response_item', {'type': 'message', 'role': 'user',
            'content': [{'type': 'input_text', 'text': 'PRIVATE_INPUT'}],
            'internal_chat_message_metadata_passthrough': {
                'turn_id': 'turn-1', 'content_item_kinds': ['user.text']}})
        self.retry()
        self.event(1001, 'response_item', {'type': 'custom_tool_call', 'name': 'exec'})
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_native_terminal_failure_never_reports_success(self):
        self.native_event('recovery_started', 1000)
        self.tick()
        self.native_event('recovery_unconfirmed', 1001)
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))
        self.assertFalse(self.state['native_codex'][str(self.home)]['pending'])
        self.assertIn('recovery-1', self.state['native_codex'][str(self.home)]['completed_events'])
        self.assertFalse(self.state.get('recovery_confirmations'))

    def test_native_terminal_failure_blocks_late_success_after_restart(self):
        self.native_event('recovery_started', 1000)
        self.native_event('recovery_unconfirmed', 1001)
        self.tick()
        self.state = json.loads(self.saved.read_text())
        self.native_event('recovery_confirmed', 1002)
        self.retry(at=1000)
        self.output(at=1003)
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))
        self.assertFalse(self.state['native_codex'][str(self.home)]['pending'])

    def test_new_native_episode_after_failure_can_succeed(self):
        self.native_event('recovery_started', 1000)
        self.native_event('recovery_unconfirmed', 1001)
        self.native_event('recovery_started', 1002, 'recovery-2')
        self.native_event('recovery_confirmed', 1003, 'recovery-2')
        self.tick()
        self.assertEqual(list(self.state['discord_outbox']), ['native-codex-recovery-2'])

    def bb_receipt(self, accepted=950, provider_turn=None):
        receipt = {'host': 'bb', 'provider_session_id': 'session-1', 'turn_id': 'bb-turn',
                   'sent_at': datetime.fromtimestamp(900, timezone.utc).isoformat(),
                   'confirmed_at': datetime.fromtimestamp(1001, timezone.utc).isoformat()}
        if accepted is not None:
            receipt['accepted_at'] = datetime.fromtimestamp(accepted, timezone.utc).isoformat()
        if provider_turn:
            receipt['provider_turn_id'] = provider_turn
        self.state['recovery_confirmations'] = {'watcher-id': receipt}

    def test_bb_receipt_matches_accepted_interval_not_bb_turn_namespace(self):
        self.bb_receipt()
        self.retry()
        self.output()
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))

    def test_bb_receipt_cannot_match_before_accepted(self):
        self.bb_receipt(accepted=1000.5)
        self.retry()
        self.output()
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_bb_receipt_unknown_native_turn_requires_acceptance(self):
        self.bb_receipt(accepted=None)
        self.retry()
        self.output()
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_bb_receipt_known_native_turn_supports_legacy_sent_time(self):
        self.bb_receipt(accepted=None, provider_turn='turn-1')
        self.retry()
        self.output()
        self.tick()
        self.assertFalse(self.state.get('discord_outbox'))

    def test_bb_receipt_wrong_native_turn_does_not_dedupe(self):
        self.bb_receipt(provider_turn='other-native-turn')
        self.retry()
        self.output()
        self.tick()
        self.assertEqual(len(self.state['discord_outbox']), 1)

    def test_first_recovery_after_missing_log_database_is_not_baselined_away(self):
        logs = self.home / 'logs_2.sqlite'
        held = self.home / 'held.sqlite'
        logs.rename(held)
        self.state = {}
        self.tick()
        self.state = json.loads(self.saved.read_text())
        held.rename(logs)
        self.native_event('recovery_started', 1000)
        self.native_event('recovery_confirmed', 1001)
        self.tick()
        self.assertEqual(list(self.state.get('discord_outbox', {})), ['native-codex-recovery-1'])

    def test_missing_state_database_does_not_reset_existing_log_cursor(self):
        self.retry()
        self.tick()
        source = self.state['native_codex'][str(self.home)]
        cursor = source['cursor']
        (self.home / 'state_5.sqlite').unlink()
        self.tick()
        source = self.state['native_codex'][str(self.home)]
        self.assertEqual(source['cursor'], cursor)
        self.assertEqual(source['error'], 'observation_unavailable')


if __name__ == '__main__':
    unittest.main()
