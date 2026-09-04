"""A recovery pass must not type into a session based on its neighbour's log."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import revive
from immortal.detect import codex


class SessionIdentityTests(unittest.TestCase):
    def test_same_folder_sessions_are_not_combined(self):
        with tempfile.TemporaryDirectory() as directory:
            sessions = Path(directory, "sessions")
            rollouts = sessions / "2026/09/04"
            rollouts.mkdir(parents=True)
            for name, events in {
                "unfinished": [("09:30:00", "task_started")],
                "finished": [("09:58:00", "task_started"), ("10:07:00", "task_complete")],
            }.items():
                rows = [{"type": "session_meta", "payload": {"cwd": directory}}]
                rows += [{"timestamp": f"2026-09-04T{ts}Z", "type": "event_msg",
                          "payload": {"type": kind}} for ts, kind in events]
                (rollouts / f"rollout-{name}.jsonl").write_text("\n".join(map(json.dumps, rows)))
            host = mock.Mock(NAME="test", DETECTOR=None)
            host.available.return_value = True
            host.list_targets.return_value = [
                {"ref": name, "id": name, "cwd": directory, "title": "Codex"}
                for name in ("unfinished", "finished")
            ]
            host.read_screen.return_value = "Ask Codex anything"
            state = {"revived": {}, "last_api_ready_at": "2026-09-04T10:05:00Z"}
            with mock.patch.object(codex, "CODEX_SESSIONS", sessions), \
                    mock.patch.object(revive, "HOSTS", (host,)), \
                    mock.patch.object(revive, "log") as log, \
                    mock.patch.object(revive, "_resume", return_value=False) as send:
                revive.revive_pass(state, ("2026-09-04T10:00:00Z", "2026-09-04T10:16:00Z"), "recheck")
            send.assert_not_called()
            decisions = [call.kwargs for call in log.call_args_list if call.args[0] == "decision"]
            self.assertEqual(len(decisions), 2)
            self.assertTrue(all(d["reasons"][-1] == "ambiguous_session" for d in decisions))

    def test_dead_neighbour_cannot_revive_an_unreadable_finished_pane(self):
        summaries = {
            "dead": {"path": "dead", "last_task_started": "2026-09-04T09:58:00Z",
                     "last_task_complete": "2026-09-04T10:02:00Z", "last_activity": None},
            "finished": {"path": "finished", "last_task_started": None,
                         "last_task_complete": "2026-09-04T10:07:00Z", "last_activity": None},
        }
        with mock.patch.object(codex, "rollouts_for_cwd", return_value=list(summaries)), \
                mock.patch.object(codex, "summarize_rollout", side_effect=summaries.__getitem__):
            decision, reasons, _ = codex.evaluate(
                {"cwd": "/same", "recovery_at": "2026-09-04T10:05:00Z"}, None,
                ("2026-09-04T10:00:00Z", "2026-09-04T10:16:00Z"))
        self.assertEqual(decision, "skip")
        self.assertEqual(reasons[-1], "ambiguous_session")
