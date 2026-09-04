#!/usr/bin/env python3
"""Behavior tests for detector harness identification."""

from __future__ import annotations

import unittest

from revive import detect_harness


class DetectHarnessTests(unittest.TestCase):
    def test_identifies_claude(self):
        screen = "⏵⏵ bypass permissions on (shift+tab to cycle)"
        self.assertEqual(detect_harness(screen, "~", "codex"), ("claude", "pane_text"))

    def test_identifies_codex(self):
        screen = "› Ask Codex to do anything"
        self.assertEqual(detect_harness(screen, "~", "claude"), ("codex", "pane_text"))

    def test_identifies_pi(self):
        screen = "π - code\n↑12.3k ↓4.5k  2.1%/200k"
        self.assertEqual(detect_harness(screen, "~", "claude"), ("pi", "pane_text"))

    def test_rejects_ambiguous_pane(self):
        screen = "› Ask Codex to do anything\n⏵⏵ bypass permissions on (shift+tab to cycle)"
        self.assertEqual(detect_harness(screen, "~", None), (None, "ambiguous_pane_text"))


if __name__ == "__main__":
    unittest.main()
