#!/usr/bin/env python3
"""Behavior tests for every Claude detector decision reason."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from immortal.detect import claude as detect

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "claude"
CWD = "/nonexistent/immortal-agents-test"
LOSS = "2026-08-21T15:59:26Z"
RECOVERY = "2026-08-21T16:02:00Z"


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.home = Path(temporary)
        self.projects = self.home / ".claude" / "projects"
        self.session_dir = self.projects / CWD.replace("/", "-")
        self.session_dir.mkdir(parents=True)
        self.session = self.session_dir / "session.jsonl"
        self.target = {
            "ref": "surface:1",
            "id": "surface:1",
            "cwd": CWD,
            "title": "Claude Code",
            "harness_hint": "claude",
        }
        self.dead_pane = (FIXTURES / "dead-network-pane.txt").read_text()
        self.log = self.enterContext(mock.patch.object(detect, "log"))
        self.enterContext(mock.patch("pathlib.Path.home", return_value=self.home))
        self.enterContext(mock.patch.object(detect, "CLAUDE_PROJECTS", self.projects))
        self.enterContext(
            mock.patch.object(
                detect,
                "CMUX_HOOK_SESSIONS",
                self.home / ".cmuxterm" / "claude-hook-sessions.json",
            )
        )

    def install_fixture(self, name):
        self.session.write_text((FIXTURES / name).read_text())

    def write_records(self, *records):
        self.session.write_text("".join(json.dumps(record) + "\n" for record in records))

    def assert_reason(self, expected, screen, window=(LOSS, RECOVERY)):
        decision, reasons, info = detect.evaluate(self.target, screen, window)
        self.assertEqual(reasons[-1], expected)
        return decision, info

    def test_no_jsonl(self):
        decision, _ = self.assert_reason("no_jsonl", self.dead_pane)
        self.assertEqual(decision, "skip")
        self.assertEqual(self.log.call_args.args[0], "no_session_mapping")

    def test_stale_error(self):
        self.install_fixture("dead-network.jsonl")
        resumed = {
            "type": "user",
            "timestamp": "2026-08-21T16:00:40Z",
            "message": {"role": "user", "content": "keep going"},
        }
        with self.session.open("a") as stream:
            stream.write(json.dumps(resumed) + "\n")
        decision, _ = self.assert_reason("stale_error", self.dead_pane)
        self.assertEqual(decision, "skip")

    def test_waiting_for_user(self):
        self.write_records(
            {
                "type": "assistant",
                "timestamp": "2026-08-21T15:59:25Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "AskUserQuestion"}],
                    "stop_reason": "tool_use",
                },
            }
        )
        decision, _ = self.assert_reason("waiting_for_user", "Permission request\nshift+tab to cycle")
        self.assertEqual(decision, "skip")

    def test_finished_cleanly(self):
        self.write_records(
            {
                "type": "assistant",
                "timestamp": "2026-08-21T15:59:25Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done."}],
                    "stop_reason": "end_turn",
                },
            }
        )
        decision, _ = self.assert_reason("finished_cleanly", "Ready\nshift+tab to cycle")
        self.assertEqual(decision, "skip")

    def test_timing_miss(self):
        self.install_fixture("dead-network.jsonl")
        decision, _ = self.assert_reason(
            "timing_miss",
            self.dead_pane,
            ("2026-08-21T18:00:00Z", "2026-08-21T18:05:00Z"),
        )
        self.assertEqual(decision, "skip")

    def test_jsonl_not_api_error(self):
        self.write_records(
            {
                "type": "assistant",
                "timestamp": "2026-08-21T16:00:10Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Write"}],
                    "stop_reason": "tool_use",
                },
            }
        )
        decision, _ = self.assert_reason(
            "jsonl_not_api_error",
            self.dead_pane,
            ("2026-08-21T16:00:00Z", RECOVERY),
        )
        self.assertEqual(decision, "skip")

    def test_silent_hang_observed(self):
        self.install_fixture("mid-task.jsonl")
        decision, _ = self.assert_reason("silent_hang_observed", "Working\nshift+tab to cycle")
        self.assertEqual(decision, "skip")

    def test_pane_not_network_error(self):
        self.install_fixture("dead-network.jsonl")
        decision, _ = self.assert_reason("pane_not_network_error", "Ready\nshift+tab to cycle")
        self.assertEqual(decision, "skip")

    def test_two_signals_agree(self):
        self.install_fixture("dead-network.jsonl")
        decision, info = self.assert_reason("two_signals_agree", None)
        self.assertEqual(decision, "resume")
        self.assertTrue(info)

    def test_all_three_agree(self):
        self.install_fixture("dead-network.jsonl")
        decision, info = self.assert_reason("all_three_agree", self.dead_pane)
        self.assertEqual(decision, "resume")
        self.assertTrue(info)

    def test_real_claude_project_slug_with_dots_and_underscores(self):
        self.target['cwd'] = '/nonexistent/.bb/thread_storage/My-project'
        folder = self.projects / '-nonexistent--bb-thread-storage-My-project'
        folder.mkdir()
        session = folder / 'session.jsonl'
        session.write_text((FIXTURES / 'dead-network.jsonl').read_text())
        decision, info = self.assert_reason('all_three_agree', self.dead_pane)
        self.assertEqual(decision, 'resume')
        self.assertEqual(info['path'], str(session))

    def test_old_project_slug_remains_readable(self):
        self.target['cwd'] = '/nonexistent/.bb/thread_storage/My-project'
        folder = self.projects / '-nonexistent-.bb-thread_storage-My-project'
        folder.mkdir()
        session = folder / 'session.jsonl'
        session.write_text((FIXTURES / 'dead-network.jsonl').read_text())
        decision, info = self.assert_reason('all_three_agree', self.dead_pane)
        self.assertEqual(decision, 'resume')
        self.assertEqual(info['path'], str(session))


if __name__ == "__main__":
    unittest.main()
