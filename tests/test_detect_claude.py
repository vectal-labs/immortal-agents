"""Behavior tests for every Claude detector decision reason."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from immortal.detect import claude as detect

FIXTURES = Path(__file__).resolve().parent / "fixtures/claude"
CWD = "/nonexistent/immortal-agents-test"
LOSS = "2026-08-21T15:59:26Z"
RECOVERY = "2026-08-21T16:02:00Z"


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.projects = self.home / ".claude/projects"
        self.session_dir = self.projects / CWD.replace("/", "-")
        self.session_dir.mkdir(parents=True)
        self.session = self.session_dir / "session.jsonl"
        self.target = {"ref": "surface:1", "id": "surface:1", "cwd": CWD,
                       "title": "Claude Code", "harness_hint": "claude"}
        self.dead_pane = (FIXTURES / "dead-network-pane.txt").read_text()
        self.log = self.enterContext(mock.patch.object(detect, "log"))
        self.enterContext(mock.patch("pathlib.Path.home", return_value=self.home))
        self.enterContext(mock.patch.object(detect, "CLAUDE_PROJECTS", self.projects))
        self.enterContext(mock.patch.object(detect, "CMUX_HOOK_SESSIONS", self.home / "missing-hooks"))

    def test_no_jsonl(self):
        decision, reasons, _ = detect.evaluate(self.target, self.dead_pane, (LOSS, RECOVERY))
        self.assertEqual((decision, reasons[-1]), ("skip", "no_jsonl"))
        self.assertEqual(self.log.call_args.args[0], "no_session_mapping")

    def test_stale_error(self):
        resumed = {"type": "user", "timestamp": "2026-08-21T16:00:40Z",
                   "message": {"role": "user", "content": "keep going"}}
        self.session.write_text((FIXTURES / "dead-network.jsonl").read_text() + json.dumps(resumed) + "\n")
        decision, reasons, _ = detect.evaluate(self.target, self.dead_pane, (LOSS, RECOVERY))
        self.assertEqual((decision, reasons[-1]), ("skip", "stale_error"))

    def test_nonfailed_session_states_are_left_alone(self):
        cases = [
            ("waiting_for_user", {"type": "tool_use", "name": "AskUserQuestion"}, "tool_use",
             "2026-08-21T15:59:25Z", "Permission request\nshift+tab to cycle", LOSS),
            ("finished_cleanly", {"type": "text", "text": "Done."}, "end_turn",
             "2026-08-21T15:59:25Z", "Ready\nshift+tab to cycle", LOSS),
            ("jsonl_not_api_error", {"type": "tool_use", "name": "Write"}, "tool_use",
             "2026-08-21T16:00:10Z", self.dead_pane, "2026-08-21T16:00:00Z"),
        ]
        for reason, content, stop, stamp, pane, loss in cases:
            with self.subTest(reason=reason):
                self.session.write_text(json.dumps({"type": "assistant", "timestamp": stamp,
                    "message": {"role": "assistant", "content": [content], "stop_reason": stop}}) + "\n")
                decision, reasons, _ = detect.evaluate(self.target, pane, (loss, RECOVERY))
                self.assertEqual((decision, reasons[-1]), ("skip", reason))

    def test_outage_requires_matching_log_pane_and_timing(self):
        cases = [
            ("timing_miss", "dead-network.jsonl", self.dead_pane,
             ("2026-08-21T18:00:00Z", "2026-08-21T18:05:00Z"), "skip"),
            ("silent_hang_observed", "mid-task.jsonl", "Working\nshift+tab to cycle", (LOSS, RECOVERY), "skip"),
            ("pane_not_network_error", "dead-network.jsonl", "Ready\nshift+tab to cycle", (LOSS, RECOVERY), "skip"),
            ("two_signals_agree", "dead-network.jsonl", None, (LOSS, RECOVERY), "resume"),
            ("all_three_agree", "dead-network.jsonl", self.dead_pane, (LOSS, RECOVERY), "resume"),
        ]
        for reason, fixture, pane, window, expected in cases:
            with self.subTest(reason=reason):
                self.session.write_text((FIXTURES / fixture).read_text())
                decision, reasons, info = detect.evaluate(self.target, pane, window)
                self.assertEqual((decision, reasons[-1]), (expected, reason))
                if expected == "resume":
                    self.assertTrue(info)

    def test_current_and_legacy_project_slugs_are_readable(self):
        self.target["cwd"] = "/nonexistent/.bb/thread_storage/My-project"
        for slug in ("-nonexistent--bb-thread-storage-My-project", "-nonexistent-.bb-thread_storage-My-project"):
            with self.subTest(slug=slug):
                folder = self.projects / slug
                folder.mkdir()
                session = folder / "session.jsonl"
                session.write_text((FIXTURES / "dead-network.jsonl").read_text())
                try:
                    decision, reasons, info = detect.evaluate(self.target, self.dead_pane, (LOSS, RECOVERY))
                    self.assertEqual((decision, reasons[-1]), ("resume", "all_three_agree"))
                    self.assertEqual(info["path"], str(session))
                finally:
                    session.unlink()
                    folder.rmdir()


if __name__ == "__main__":
    unittest.main()
