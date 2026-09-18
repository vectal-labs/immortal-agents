"""Behavior tests for every Codex detector decision reason."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from immortal.detect import codex as detect

FIXTURES = Path(__file__).resolve().parent / "fixtures/codex"
CWD = "/nonexistent/immortal-agents-test"
LOSS = "2026-08-26T20:15:09Z"
RECOVERY = "2026-08-26T20:30:25Z"


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        sessions = home / ".codex/sessions"
        self.rollout = sessions / "2026/08/26/rollout-test.jsonl"
        self.rollout.parent.mkdir(parents=True)
        self.target = {"ref": "surface:2", "id": "surface:2", "cwd": CWD,
                       "title": "Codex", "harness_hint": "codex"}
        self.dead = (FIXTURES / "dead-network-pane.txt").read_text()
        self.idle = (FIXTURES / "idle-prompt-pane.txt").read_text()
        self.enterContext(mock.patch("pathlib.Path.home", return_value=home))
        self.enterContext(mock.patch.object(detect, "CODEX_SESSIONS", sessions))

    def install_fixture(self, name):
        self.rollout.write_text((FIXTURES / name).read_text().replace("/Users/user/project", CWD))

    def test_outage_decisions(self):
        cases = [
            ("waiting_for_user", None, "Would you like to run the following command?\n› Ask Codex to do anything", (LOSS, RECOVERY), "skip"),
            ("bad_outage_window", None, self.dead, ("not-a-time", RECOVERY), "skip"),
            ("pane_not_network_error", "dead-network.jsonl", "› Ask Codex to do anything", (LOSS, RECOVERY), "skip"),
            ("finished_after_recovery", "dead-network.jsonl", self.dead, (LOSS, "2026-08-26T20:18:00Z"), "skip"),
            ("no_task_complete_in_outage", "no-complete.jsonl", self.dead, (LOSS, RECOVERY), "skip"),
            ("two_signals_agree", "dead-network.jsonl", None, (LOSS, RECOVERY), "resume"),
            ("all_three_agree", "dead-network.jsonl", self.dead, (LOSS, RECOVERY), "resume"),
        ]
        for reason, fixture, pane, window, expected in cases:
            with self.subTest(reason=reason):
                self.rollout.unlink(missing_ok=True)
                if fixture:
                    self.install_fixture(fixture)
                decision, reasons, info = detect.evaluate(self.target, pane, window)
                self.assertEqual((decision, reasons[-1]), (expected, reason))
                if expected == "resume":
                    self.assertTrue(info)

    def test_post_recovery_liveness_and_grace(self):
        self.target["recovery_at"] = RECOVERY
        cases = [
            ("alive_after_recovery", "alive-after-recovery.jsonl", "2026-08-26T20:41:25Z", "skip"),
            ("grace_pending", "no-complete.jsonl", "2026-08-26T20:32:25Z", "skip"),
            ("idle_unfinished_turn", "no-complete.jsonl", "2026-08-26T20:41:25Z", "resume"),
        ]
        for reason, fixture, end, expected in cases:
            with self.subTest(reason=reason):
                self.install_fixture(fixture)
                decision, reasons, _ = detect.evaluate(self.target, self.idle, (LOSS, end))
                self.assertEqual((decision, reasons[-1]), (expected, reason))

    def test_live_pane_markers_are_not_confused_with_ordinary_prose(self):
        self.install_fixture("no-complete.jsonl")
        for fixture in ("working-pane.txt", "reconnecting-pane.txt"):
            with self.subTest(pane=fixture):
                decision, reasons, _ = detect.evaluate(self.target, (FIXTURES / fixture).read_text(), (LOSS, RECOVERY))
                self.assertEqual((decision, reasons[-1]), ("skip", "pane_alive"))
        pane = "The factory has been working for decades.\n› Ask Codex to do anything"
        self.assertEqual(detect.classify_pane(pane), "other")
        self.target["recovery_at"] = RECOVERY
        decision, reasons, _ = detect.evaluate(self.target, pane, (LOSS, "2026-08-26T20:41:25Z"))
        self.assertEqual((decision, reasons[-1]), ("resume", "idle_unfinished_turn"))


if __name__ == "__main__":
    unittest.main()
