#!/usr/bin/env python3
"""Behavior tests for every Codex detector decision reason."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from immortal.detect import codex as detect_codex

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "codex"
CWD = "/nonexistent/immortal-agents-test"
LOSS = "2026-08-26T20:15:09Z"
RECOVERY = "2026-08-26T20:30:25Z"


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.home = Path(temporary)
        self.sessions = self.home / ".codex" / "sessions"
        self.rollout_dir = self.sessions / "2026" / "08" / "26"
        self.rollout_dir.mkdir(parents=True)
        self.rollout = self.rollout_dir / "rollout-test.jsonl"
        self.target = {
            "ref": "surface:2",
            "id": "surface:2",
            "cwd": CWD,
            "title": "Codex",
            "harness_hint": "codex",
        }
        self.dead_pane = (FIXTURES / "dead-network-pane.txt").read_text()
        self.idle_pane = (FIXTURES / "idle-prompt-pane.txt").read_text()
        self.working_pane = (FIXTURES / "working-pane.txt").read_text()
        self.reconnecting_pane = (FIXTURES / "reconnecting-pane.txt").read_text()
        self.enterContext(mock.patch("pathlib.Path.home", return_value=self.home))
        self.enterContext(mock.patch.object(detect_codex, "CODEX_SESSIONS", self.sessions))

    def install_fixture(self, name):
        captured = (FIXTURES / name).read_text()
        self.rollout.write_text(captured.replace("/Users/user/project", CWD))

    def assert_reason(self, expected, screen, window=(LOSS, RECOVERY)):
        decision, reasons, info = detect_codex.evaluate(self.target, screen, window)
        self.assertEqual(reasons[-1], expected)
        return decision, info

    def test_waiting_for_user(self):
        decision, _ = self.assert_reason(
            "waiting_for_user",
            "Would you like to run the following command?\n› Ask Codex to do anything",
        )
        self.assertEqual(decision, "skip")

    def test_bad_outage_window(self):
        decision, _ = self.assert_reason(
            "bad_outage_window",
            self.dead_pane,
            ("not-a-time", RECOVERY),
        )
        self.assertEqual(decision, "skip")

    def test_pane_not_network_error(self):
        self.install_fixture("dead-network.jsonl")
        decision, _ = self.assert_reason("pane_not_network_error", "› Ask Codex to do anything")
        self.assertEqual(decision, "skip")

    def test_finished_after_recovery(self):
        self.install_fixture("dead-network.jsonl")
        decision, _ = self.assert_reason(
            "finished_after_recovery",
            self.dead_pane,
            (LOSS, "2026-08-26T20:18:00Z"),
        )
        self.assertEqual(decision, "skip")

    def test_no_task_complete_in_outage(self):
        self.install_fixture("no-complete.jsonl")
        decision, _ = self.assert_reason("no_task_complete_in_outage", self.dead_pane)
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

    def test_alive_after_recovery(self):
        self.install_fixture("alive-after-recovery.jsonl")
        self.target["recovery_at"] = RECOVERY
        decision, _ = self.assert_reason(
            "alive_after_recovery",
            self.idle_pane,
            (LOSS, "2026-08-26T20:41:25Z"),
        )
        self.assertEqual(decision, "skip")

    def test_grace_pending(self):
        self.install_fixture("no-complete.jsonl")
        self.target["recovery_at"] = RECOVERY
        decision, _ = self.assert_reason(
            "grace_pending",
            self.idle_pane,
            (LOSS, "2026-08-26T20:32:25Z"),
        )
        self.assertEqual(decision, "skip")

    def test_idle_unfinished_turn(self):
        self.install_fixture("no-complete.jsonl")
        self.target["recovery_at"] = RECOVERY
        decision, _ = self.assert_reason(
            "idle_unfinished_turn",
            self.idle_pane,
            (LOSS, "2026-08-26T20:41:25Z"),
        )
        self.assertEqual(decision, "resume")

    def test_idle_essay_containing_working(self):
        pane = "The factory has been working for decades.\n› Ask Codex to do anything"
        self.assertEqual(detect_codex.classify_pane(pane), "other")
        self.install_fixture("no-complete.jsonl")
        self.target["recovery_at"] = RECOVERY
        decision, _ = self.assert_reason(
            "idle_unfinished_turn",
            pane,
            (LOSS, "2026-08-26T20:41:25Z"),
        )
        self.assertEqual(decision, "resume")

    def test_pane_alive_working(self):
        self.install_fixture("no-complete.jsonl")
        decision, _ = self.assert_reason("pane_alive", self.working_pane)
        self.assertEqual(decision, "skip")

    def test_pane_alive_reconnecting(self):
        self.install_fixture("no-complete.jsonl")
        decision, _ = self.assert_reason("pane_alive", self.reconnecting_pane)
        self.assertEqual(decision, "skip")


if __name__ == "__main__":
    unittest.main()
