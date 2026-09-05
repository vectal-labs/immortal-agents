#!/usr/bin/env python3
"""Tests for the Terminal.app and Ghostty hosts and the shared host loop."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import detect
import detect_codex
import host_ghostty
import host_terminal
import osa
import procs
import watcher

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


class ProcsTests(unittest.TestCase):
    def test_harness_from_program_name_only(self):
        self.assertEqual(procs.harness_of("claude --resume abc"), "claude")
        self.assertEqual(procs.harness_of("/Users/d/.local/bin/claude"), "claude")
        self.assertEqual(procs.harness_of("node /opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/claude"), "claude")
        self.assertEqual(procs.harness_of("codex"), "codex")
        self.assertIsNone(procs.harness_of("rg claude ."))
        self.assertIsNone(procs.harness_of("-/bin/zsh"))
        self.assertIsNone(procs.harness_of(""))

    def test_agent_processes_limited_to_app_descendants(self):
        table = [
            {"pid": 1057, "ppid": 1, "tty": "??", "args": "/Applications/Ghostty.app/Contents/MacOS/ghostty"},
            {"pid": 1060, "ppid": 1057, "tty": "ttys000", "args": "/usr/bin/login"},
            {"pid": 1061, "ppid": 1060, "tty": "ttys000", "args": "-/bin/zsh"},
            {"pid": 2000, "ppid": 1061, "tty": "ttys000", "args": "claude"},
            {"pid": 3000, "ppid": 1, "tty": "ttys009", "args": "codex"},
        ]
        with mock.patch.object(procs, "cwd_of", return_value="/tmp/p"):
            under = procs.agent_processes(table, under_pids=[1057])
            everything = procs.agent_processes(table)
        self.assertEqual([a["pid"] for a in under], [2000])
        self.assertEqual(sorted(a["harness"] for a in everything), ["claude", "codex"])


class UnreadablePaneTests(unittest.TestCase):
    """Ghostty exposes no screen text: the harness log alone must decide."""

    def jsonl(self, **overrides):
        info = {
            "path": "/x.jsonl",
            "last_activity": iso(NOW),
            "api_error": True,
            "stale_error": False,
            "waiting": False,
            "finished_clean": False,
        }
        info.update(overrides)
        return info

    def test_claude_api_error_plus_timing_resumes(self):
        decision, reasons = detect.evaluate(self.jsonl(), detect.UNREADABLE, iso(NOW - timedelta(minutes=2)))
        self.assertEqual(decision, "resume")
        self.assertIn("two_signals_agree", reasons)

    def test_claude_without_api_error_still_skips(self):
        decision, reasons = detect.evaluate(
            self.jsonl(api_error=False, last_activity=iso(NOW - timedelta(minutes=5))),
            detect.UNREADABLE,
            iso(NOW - timedelta(minutes=2)),
        )
        self.assertEqual(decision, "skip")

    def test_claude_finished_clean_skips(self):
        decision, reasons = detect.evaluate(
            self.jsonl(finished_clean=True), detect.UNREADABLE, iso(NOW - timedelta(minutes=2))
        )
        self.assertEqual(decision, "skip")
        self.assertIn("finished_cleanly", reasons)

    def test_codex_task_complete_in_outage_resumes(self):
        loss, recovery = iso(NOW - timedelta(minutes=10)), iso(NOW)
        summary = {"path": "/r.jsonl", "last_task_complete": iso(NOW - timedelta(minutes=5)),
                   "last_task_started": None, "last_activity": None}
        with mock.patch.object(detect_codex, "rollouts_for_cwd", return_value=["/r.jsonl"]), mock.patch.object(
            detect_codex, "summarize_rollout", return_value=summary
        ):
            decision, reasons, _ = detect_codex.evaluate("/p", detect_codex.UNREADABLE, loss, recovery)
        self.assertEqual(decision, "resume")
        self.assertIn("two_signals_agree", reasons)

    def test_codex_without_dead_rollout_skips(self):
        with mock.patch.object(detect_codex, "rollouts_for_cwd", return_value=[]):
            decision, _, _ = detect_codex.evaluate("/p", detect_codex.UNREADABLE, iso(NOW - timedelta(minutes=10)), iso(NOW))
        self.assertEqual(decision, "skip")


class FakeHost:
    NAME = "fake"

    def __init__(self, targets, screen=None):
        self.targets, self.screen, self.resumed = targets, screen, []

    def available(self):
        return True

    def list_targets(self):
        return self.targets

    def read_screen(self, ref):
        return self.screen

    def resume(self, ref):
        self.resumed.append(ref)
        return True


class HostLoopTests(unittest.TestCase):
    def test_ghostty_style_host_revives_dead_claude_once(self):
        host = FakeHost([{"ref": "T1", "id": "T1", "cwd": "/p", "title": "claude", "harness_hint": "claude"}])
        info = {"path": "/x.jsonl", "last_activity": iso(NOW), "api_error": True, "stale_error": False,
                "waiting": False, "finished_clean": False}
        loss, recovery = iso(NOW - timedelta(minutes=3)), iso(NOW + timedelta(minutes=3))
        state = {"resumed": {}}
        with mock.patch.object(watcher, "log"), mock.patch.object(watcher, "save_state"), mock.patch.object(
            detect, "find_jsonl", return_value="/x.jsonl"
        ), mock.patch.object(detect, "summarize_jsonl", return_value=info), mock.patch.object(
            watcher.notify, "notify_revive", return_value=True
        ) as ping:
            watcher.recover_terminal_host(host, state, loss, recovery, 360)
            watcher.recover_terminal_host(host, state, loss, recovery, 360)
        self.assertEqual(host.resumed, ["T1"])
        ping.assert_called_once_with("fake", "claude", 360, "claude", True)

    def test_unknown_harness_is_skipped(self):
        host = FakeHost([{"ref": "T2", "id": "T2", "cwd": "/p", "title": "~", "harness_hint": None}])
        with mock.patch.object(watcher, "log"):
            watcher.recover_terminal_host(host, {"resumed": {}}, iso(NOW), iso(NOW), 300)
        self.assertEqual(host.resumed, [])

    def test_on_recovery_skips_hosts_that_are_not_running(self):
        with mock.patch.object(watcher, "log") as log, mock.patch.object(
            watcher, "recover_bb_threads", return_value=0
        ), mock.patch.object(watcher.ready, "wait_for_apis"), mock.patch.object(
            watcher, "recover_terminal_host"
        ) as per_host:
            for host in watcher.TERMINAL_HOSTS:
                self.enterContext(mock.patch.object(host, "available", return_value=False))
            watcher.on_recovery({"resumed": {}}, iso(NOW), iso(NOW), 300)
        per_host.assert_not_called()
        skipped = [c.kwargs.get("host") for c in log.call_args_list if c.args[0] == "host_skipped"]
        self.assertEqual(sorted(skipped), ["cmux", "ghostty", "terminal"])


class TerminalHostTests(unittest.TestCase):
    def test_targets_get_harness_and_cwd_from_the_tty_process(self):
        tabs = [
            {"tty": "/dev/ttys003", "title": "", "processes": ["login", "-zsh", "claude"], "window": 1, "index": 1},
            {"tty": "/dev/ttys004", "title": "", "processes": ["login", "-zsh"], "window": 1, "index": 2},
        ]
        agents = [{"pid": 9, "ppid": 8, "tty": "ttys003", "args": "claude", "harness": "claude", "cwd": "/p"}]
        with mock.patch.object(osa, "run_jxa_json", return_value=tabs), mock.patch.object(
            procs, "agent_processes", return_value=agents
        ):
            targets = host_terminal.list_targets()
        self.assertEqual(targets[0]["harness_hint"], "claude")
        self.assertEqual(targets[0]["cwd"], "/p")
        self.assertEqual(targets[0]["ref"], "/dev/ttys003")
        self.assertIsNone(targets[1]["harness_hint"])

    def test_resume_types_keep_going_into_the_tab(self):
        with mock.patch.object(osa, "run_jxa", return_value="ok") as run:
            self.assertTrue(host_terminal.resume("/dev/ttys003"))
        script = run.call_args.args[0]
        self.assertIn('"/dev/ttys003"', script)
        self.assertIn('doScript("keep going"', script)

    def test_osa_denial_is_not_fatal(self):
        with mock.patch.object(osa, "run_jxa_json", side_effect=osa.OsaError("-1743")), mock.patch.object(
            host_terminal, "log"
        ):
            self.assertEqual(host_terminal.list_targets(), [])


class GhosttyHostTests(unittest.TestCase):
    def test_hint_only_when_cwd_maps_to_one_terminal(self):
        terminals = [
            {"id": "A", "title": "claude", "cwd": "/one"},
            {"id": "B", "title": "~", "cwd": "/two"},
            {"id": "C", "title": "~", "cwd": "/two"},
        ]
        agents = [
            {"pid": 1, "harness": "claude", "cwd": "/one"},
            {"pid": 2, "harness": "codex", "cwd": "/two"},
        ]
        with mock.patch.object(osa, "run_jxa_json", return_value=terminals), mock.patch.object(
            procs, "agent_processes", return_value=agents
        ), mock.patch.object(procs, "app_pids", return_value=[1057]):
            targets = host_ghostty.list_targets()
        hints = {t["ref"]: t["harness_hint"] for t in targets}
        self.assertEqual(hints, {"A": "claude", "B": None, "C": None})
        self.assertIsNone(host_ghostty.read_screen("A"))

    def test_resume_sends_escape_text_enter(self):
        with mock.patch.object(osa, "run_jxa", return_value="ok") as run:
            self.assertTrue(host_ghostty.resume("A"))
        script = run.call_args.args[0]
        self.assertIn('sendKey("escape"', script)
        self.assertIn('inputText("keep going"', script)
        self.assertIn('sendKey("enter"', script)


if __name__ == "__main__":
    unittest.main()
