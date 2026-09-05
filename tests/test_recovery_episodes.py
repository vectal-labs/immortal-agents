"""Repeated outages use the real Claude detector and durable recovery state."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import revive
from immortal.core import logbook, revive_state
from immortal.core.common import parse_ts
from immortal.detect import claude


class RecoveryEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.base = datetime.now(timezone.utc)
        self.now = self.base
        self.state = {}
        cwd = self.root / "project"
        cwd.mkdir()
        projects = self.root / "claude-projects"
        directory = projects / claude.encode_cwd(str(cwd))[0]
        directory.mkdir(parents=True)
        self.session = directory / "session.jsonl"
        self.error_at = self.base - timedelta(minutes=5)
        self.append_error(self.error_at)
        self.enterContext(mock.patch.object(claude, "CLAUDE_PROJECTS", projects))
        self.enterContext(mock.patch.object(claude, "CMUX_HOOK_SESSIONS", self.root / "missing-hooks"))
        self.enterContext(mock.patch.multiple(logbook, STATE_DIR=self.root,
            STATE_PATH=self.root / "state.json", LOG_PATH=self.root / "watcher.log"))
        self.enterContext(mock.patch.object(revive, "log"))
        self.enterContext(mock.patch.object(revive, "announce"))
        self.enterContext(mock.patch.object(revive.telemetry, "send"))
        clock = mock.Mock()
        clock.now.side_effect = lambda *args: self.now
        self.enterContext(mock.patch.object(revive, "datetime", clock))
        self.enterContext(mock.patch.object(revive_state, "datetime", clock))
        self.target = {"ref": "one", "id": "one", "cwd": str(cwd),
                       "title": "claude", "harness_hint": "claude"}
        self.host = SimpleNamespace(NAME="cmux", DETECTOR=None,
            available=lambda: True, list_targets=lambda: [dict(self.target)],
            read_screen=lambda ref: "shift+tab to cycle\nAPI Error: Connection error",
            resume=mock.Mock(return_value="unknown"))
        self.enterContext(mock.patch.object(revive, "HOSTS", (self.host,)))

    def append_error(self, at):
        with self.session.open("a") as stream:
            stream.write(json.dumps({"type": "assistant", "timestamp": at.isoformat(),
                "isApiErrorMessage": True,
                "message": {"content": [{"type": "text", "text": "API Error: Connection error"}]}}) + "\n")

    def window(self, index):
        end = self.base + timedelta(minutes=4 * index)
        loss = self.base - timedelta(minutes=10) if index == 0 else end - timedelta(minutes=3)
        return loss.isoformat(), end.isoformat()

    def outage(self, index, at=None):
        window = self.window(index)
        self.now = at or parse_ts(window[1])
        revive.revive_pass(self.state, window, "first")

    def test_unknown_delivery_is_not_repeated_after_restart_and_another_outage(self):
        self.outage(0)
        self.state = logbook.load_state()
        self.outage(1)
        self.host.resume.assert_called_once()
        self.assertEqual(len(self.state["revived"]), 1)

    def test_crash_after_dispatch_keeps_protection_in_the_next_outage(self):
        self.host.resume.side_effect = KeyboardInterrupt("watcher stopped after dispatch")
        with self.assertRaises(KeyboardInterrupt):
            self.outage(0)
        self.state = logbook.load_state()
        self.host.resume.side_effect = None
        self.outage(1)
        self.host.resume.assert_called_once()

    def test_sent_queued_and_superseded_errors_are_not_repeated_in_another_outage(self):
        for delivery in ("sent", "queued", "superseded"):
            with self.subTest(delivery=delivery):
                self.state = {}
                self.host.resume.reset_mock()
                self.host.resume.return_value = delivery
                self.outage(0)
                self.state = logbook.load_state()
                self.outage(1)
                self.host.resume.assert_called_once()

    def test_same_session_in_another_pane_retains_unknown_delivery_protection(self):
        self.outage(0)
        self.state = logbook.load_state()
        self.target.update(ref="other-pane", id="other-pane")
        self.outage(1)
        self.host.resume.assert_called_once()

    def test_definite_failure_backoff_survives_another_outage_key(self):
        self.host.resume.return_value = "not_sent"
        # Slow discovery can delay the first dispatch until the next outage.
        sent_at = self.base + timedelta(minutes=4)
        self.outage(0, at=sent_at)
        self.state = logbook.load_state()
        self.outage(1, at=sent_at + timedelta(seconds=29))
        self.host.resume.assert_called_once()
        self.outage(1, at=sent_at + timedelta(seconds=30))
        self.assertEqual(self.host.resume.call_count, 2)
        latest = max(self.state["revived"].values(), key=lambda entry: entry["at"])
        self.assertEqual(parse_ts(latest["next_at"]), sent_at + timedelta(seconds=90))

    def test_same_failure_gets_at_most_three_attempts_across_outages(self):
        self.host.resume.return_value = "not_sent"
        for index in range(4):
            self.outage(index)
            self.state = logbook.load_state()
        self.assertEqual(self.host.resume.call_count, 3)
        entries = list(self.state["revived"].values())
        self.assertEqual([entry["tries"] for entry in entries], [1, 1, 1])
        self.assertEqual([entry["error_tries"] for entry in entries], [1, 2, 3])

    def test_new_failures_remain_eligible_within_the_existing_outage_cap(self):
        self.host.resume.return_value = "sent"
        self.outage(0)
        for index in range(1, 4):
            self.now = self.base + timedelta(minutes=index)
            self.append_error(self.now)
            revive.revive_pass(self.state, (self.window(0)[0], self.now.isoformat()), "recheck")
        self.assertEqual(self.host.resume.call_count, 3)
        self.assertEqual(len(self.state["revived"]), 1)
        entry = next(iter(self.state["revived"].values()))
        self.assertEqual(entry["tries"], 3)
        self.assertEqual(entry["error_tries"], 1)

    def test_legacy_entry_keeps_same_error_protection_and_allows_a_new_error(self):
        key = f"{self.session}:{self.window(0)[0]}"
        self.state = {"revived": {key: {"tries": 1, "at": self.base.isoformat(),
            "error_at": self.error_at.isoformat()}}}
        revive.revive_pass(self.state, self.window(0), "recheck")
        self.host.resume.assert_not_called()
        self.now = self.base + timedelta(minutes=1)
        self.append_error(self.now)
        revive.revive_pass(self.state, (self.window(0)[0], self.now.isoformat()), "recheck")
        self.host.resume.assert_called_once()
        self.assertEqual(self.state["revived"][key]["tries"], 2)


if __name__ == "__main__":
    unittest.main()
