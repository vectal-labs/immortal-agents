#!/usr/bin/env python3
"""Tests for Pi coding-agent detection against captured session fixtures."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from immortal.detect import pi as detect_pi
from immortal.core import procs
import revive

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pi"
DEAD_FILE = (
    FIXTURES
    / "--Users-user-project--"
    / "2026-08-31T19-50-24-403Z_01a0595f-7993-7617-b70d-2223d42e53ef.jsonl"
)
CWD = "/Users/user/project"


def session_lines(last_message):
    header = {
        "type": "session",
        "version": 3,
        "id": "test",
        "timestamp": "2026-08-31T19:50:24.403Z",
        "cwd": CWD,
    }
    user = {
        "type": "message",
        "id": "u1",
        "parentId": None,
        "timestamp": "2026-08-31T19:51:03.467Z",
        "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    }
    return "\n".join(json.dumps(obj) for obj in (header, user, last_message)) + "\n"


class EncodeCwdTests(unittest.TestCase):
    def test_wraps_slashes_as_pi_folder_name(self):
        self.assertIn("--Users-user-project--", detect_pi.encode_cwd(CWD))


class PaneTests(unittest.TestCase):
    def test_death_fingerprints_are_network_error(self):
        self.assertEqual(detect_pi.classify_pane("Error: Retry failed after 3 attempts: Connection error."), "network_error")
        self.assertEqual(detect_pi.classify_pane("Error: Connection error."), "network_error")
        self.assertEqual(detect_pi.classify_pane("Error: fetch failed"), "network_error")

    def test_retrying_is_not_dead_yet(self):
        screen = "Error: Connection error.\nRetrying (1/3) in 2s... (esc to cancel)"
        self.assertEqual(detect_pi.classify_pane(screen), "other")

    def test_pane_markers_identify_pi(self):
        self.assertTrue(detect_pi.is_pane("π - code"))
        self.assertTrue(detect_pi.is_pane("↑12.3k ↓4.5k  2.1%/200k  superdeepseek"))
        self.assertFalse(detect_pi.is_pane("ask codex"))


class ProcsTests(unittest.TestCase):
    def test_pi_command_line(self):
        self.assertEqual(procs.harness_of("pi"), "pi")
        self.assertEqual(procs.harness_of("/opt/homebrew/bin/pi"), "pi")
        self.assertEqual(
            procs.harness_of("node /opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"),
            "pi",
        )
        self.assertIsNone(procs.harness_of("rg pi-coding-agent"))


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(detect_pi, "PI_SESSIONS", FIXTURES))

    def test_dead_session_inside_outage_resumes(self):
        self.assertTrue(DEAD_FILE.is_file())
        decision, reasons, info = detect_pi.evaluate(
            {"cwd": CWD}, "Error: Connection error.",
            ("2026-08-31T19:50:00Z", "2026-08-31T19:52:00Z"),
        )
        self.assertEqual(decision, "resume")
        self.assertIn("all_three_agree", reasons)
        self.assertTrue(info["dead"])

    def test_error_before_outage_skips_stale(self):
        decision, reasons, _ = detect_pi.evaluate(
            {"cwd": CWD}, "Error: Connection error.",
            ("2026-08-31T19:52:00Z", "2026-08-31T20:00:00Z"),
        )
        self.assertEqual(decision, "skip")
        self.assertIn("stale_error", reasons)


class SyntheticEvaluateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        folder = self.tmp / "--Users-user-project--"
        folder.mkdir()
        self.path = folder / "session.jsonl"
        self.enterContext(mock.patch.object(detect_pi, "PI_SESSIONS", self.tmp))

    def write(self, last_message):
        self.path.write_text(session_lines(last_message))

    def eval_dead_pane(self, last_message):
        self.write(last_message)
        return detect_pi.evaluate(
            {"cwd": CWD}, "Error: Connection error.",
            ("2026-08-31T19:50:00Z", "2026-08-31T19:52:00Z"),
        )

    def test_user_message_after_error_skips_resumed(self):
        error = {
            "type": "message",
            "id": "a1",
            "parentId": "u1",
            "timestamp": "2026-08-31T19:51:17.731Z",
            "message": {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "Connection error."},
        }
        user = {
            "type": "message",
            "id": "u2",
            "parentId": "a1",
            "timestamp": "2026-08-31T19:51:30.000Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "keep going"}]},
        }
        self.path.write_text(session_lines(error) + json.dumps(user) + "\n")
        decision, reasons, _ = detect_pi.evaluate(
            {"cwd": CWD}, "Error: Connection error.",
            ("2026-08-31T19:50:00Z", "2026-08-31T19:52:00Z"),
        )
        self.assertEqual(decision, "skip")
        self.assertIn("already_resumed", reasons)

    def test_aborted_skips(self):
        decision, reasons, _ = self.eval_dead_pane({
            "type": "message",
            "id": "a1",
            "parentId": "u1",
            "timestamp": "2026-08-31T19:51:17.731Z",
            "message": {"role": "assistant", "content": [], "stopReason": "aborted", "errorMessage": "Connection error."},
        })
        self.assertEqual(decision, "skip")
        self.assertIn("aborted", reasons)

    def test_402_and_429_skip(self):
        for code in ("402 Payment Required", "429 Too Many Requests"):
            decision, reasons, _ = self.eval_dead_pane({
                "type": "message",
                "id": "a1",
                "parentId": "u1",
                "timestamp": "2026-08-31T19:51:17.731Z",
                "message": {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": code},
            })
            self.assertEqual(decision, "skip", code)
            self.assertIn("error_not_network", reasons)


class WatcherHarnessTests(unittest.TestCase):
    def test_title_and_footer_identify_pi(self):
        self.assertEqual(revive.detect_harness("", "π - code"), ("pi", "surface_title"))
        self.assertEqual(revive.detect_harness("↑12.3k ↓4.5k  2.1%/200k", "~"), ("pi", "pane_text"))
        self.assertEqual(revive.detect_harness("", "~", "pi"), ("pi", "process"))


if __name__ == "__main__":
    unittest.main()
