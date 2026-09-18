"""Harness identification, including conflicting pane markers."""
import unittest

from revive import detect_harness


class DetectHarnessTests(unittest.TestCase):
    def test_pane_markers_override_process_hints_unless_ambiguous(self):
        cases = [
            ("⏵⏵ bypass permissions on (shift+tab to cycle)", "codex", "claude", "pane_text"),
            ("› Ask Codex to do anything", "claude", "codex", "pane_text"),
            ("π - code\n↑12.3k ↓4.5k  2.1%/200k", "claude", "pi", "pane_text"),
            ("› Ask Codex to do anything\n⏵⏵ bypass permissions on (shift+tab to cycle)", None, None, "ambiguous_pane_text"),
        ]
        for screen, hint, harness, reason in cases:
            with self.subTest(harness=harness):
                self.assertEqual(detect_harness(screen, "~", hint), (harness, reason))


if __name__ == "__main__":
    unittest.main()
