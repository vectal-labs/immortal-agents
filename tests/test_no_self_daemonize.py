"""Guard test (ADR 0013): fails if self-daemonizing code returns to watcher.py."""

import unittest
from pathlib import Path


class NoSelfDaemonizeTests(unittest.TestCase):
    def test_watcher_has_no_fork_or_setsid(self):
        source = (Path(__file__).resolve().parent.parent / "watcher.py").read_text()
        hits = [token for token in ("fork(", "setsid(") if token in source]
        self.assertEqual(hits, [], f"watcher.py contains banned calls (ADR 0013): {hits}")
