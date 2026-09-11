#!/usr/bin/env python3
"""Upgrade a real public v0.1.0 checkout to this candidate with the OLD updater code.

Real: the v0.1.0 updater module from the public 6f92cdc tree, a real clone, a
real Git tag fetch and fast-forward, real state files, the candidate's own
component migration in a fresh process. Mocked, exactly like tests/test_updates.py:
GitHub (feed + release API), launchctl, and the watcher restart. No watcher runs
and no live host adapter is touched. Everything lives under one temp directory.

    python3 docs/experiments/0022-upgrade-check.py [--candidate <sha>] [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import textwrap

OLD = "6f92cdccf316a36917436e89656cdbccfd421d75"      # public v0.1.0 (git ls-remote origin)
FIRST = "000250a"                                        # initial public commit, predates the checker
REPO = Path(__file__).resolve().parents[2]
PUBLIC = "https://github.com/vectal-labs/immortal-agents"

# Runs inside the install clone so `immortal` resolves to the code under test.
DRIVER = textwrap.dedent("""
    import json, sys
    from pathlib import Path
    from unittest import mock
    install, state, home, bare, phase = sys.argv[1:6]
    sys.path.insert(0, install)
    from immortal.core import updates, updater
    feed = json.loads(Path(state, "feed.json").read_text())
    release = feed["releases"][0]
    api = {"draft": False, "prerelease": False, "published_at": "2026-09-11T00:00:00Z",
           "tag_name": "v" + release["version"], "html_url": release["notes_url"]}
    real_git = updater.git
    def local_git(repo, *args):
        if args[0] == "fetch":  # the only network call: GitHub -> local bare origin
            assert args == ("fetch", "--no-tags", "origin", "refs/tags/v" + release["version"]), args
            return real_git(repo, "fetch", "--no-tags", bare, "refs/tags/v" + release["version"])
        return real_git(repo, *args)
    def fake_restart(state_dir, repo=None):
        if phase == "new":
            from immortal.core import runtime
            runtime.record(runtime.identity(repo), state_dir)
        Path(state_dir, "restart-called").write_text(phase)
    with mock.patch.object(updates, "fetch_json", side_effect=lambda url: feed if url == updates.FEED_URL else api), \\
         mock.patch.object(updates, "launchctl"), \\
         mock.patch.object(updater, "process_id", return_value=4242), \\
         mock.patch.object(updater, "restart_watcher", side_effect=fake_restart), \\
         mock.patch.object(updater, "git", side_effect=local_git):
        print(updater.apply(Path(install), Path(state), Path(home)))
""")


def sh(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SystemExit(f"{' '.join(args)}\n{result.stdout}{result.stderr}")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", default=sh("git", "-C", str(REPO), "rev-parse", "HEAD"))
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    candidate = sh("git", "-C", str(REPO), "rev-parse", args.candidate + "^{commit}")
    tmp = Path(tempfile.mkdtemp(prefix="immortal-upgrade-"))
    try:
        run(tmp, candidate)
    finally:
        print("kept" if args.keep else "removed", tmp)
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


def run(tmp, candidate):
    bare, install, state, home = tmp / "origin.git", tmp / "immortal-agents", tmp / "state", tmp / "home"
    sh("git", "init", "-q", "--bare", str(bare))
    sh("git", "-C", str(REPO), "push", "-q", str(bare), OLD + ":refs/heads/main", candidate + ":refs/tags/v0.2.0")
    sh("git", "clone", "-q", "-b", "main", str(bare), str(install))
    sh("git", "-C", str(install), "remote", "set-url", "origin", PUBLIC + ".git")
    assert '"0.1.0"' in (install / "immortal/__init__.py").read_text()
    assert not (install / "immortal/core/install_migrations.py").exists()

    plist = home / "Library/LaunchAgents/com.immortal-agents.watcher.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({"Label": "com.immortal-agents.watcher",
                                      "ProgramArguments": [sys.executable, str(install / "watcher.py")],
                                      "WorkingDirectory": str(install),
                                      "EnvironmentVariables": {"WATCHER_STATE_DIR": str(state)}}))
    state.mkdir()
    preserved = {"state.json": json.dumps({"online": True, "pending_revives": {"bb:thr_x": {"host": "bb"}},
                                           "outcomes": {"a1": {"result": "revive_confirmed"}}}),
                 "telemetry": "on\n", "discord_webhook": "https://discord.example/hook\n",
                 "bb_runtime.json": json.dumps({"node": "/opt/homebrew/bin/node", "bb": "/opt/homebrew/bin/bb"})}
    for name, text in preserved.items():
        (state / name).write_text(text)
    feed = {"schema": 1, "releases": [
        {"version": "0.2.0", "published_at": "2026-09-11", "important": True, "summary": "candidate",
         "commit": candidate, "notes_url": PUBLIC + "/releases/tag/v0.2.0"},
        {"version": "0.1.0", "published_at": "2026-09-05", "important": True, "summary": "old",
         "commit": OLD, "notes_url": PUBLIC + "/releases/tag/v0.1.0"}]}
    (state / "feed.json").write_text(json.dumps(feed))
    (state / "updates.json").write_text(json.dumps({"available": feed["releases"][0], "notified_version": "0.2.0"}))

    def apply(phase):
        return sh(sys.executable, "-c", DRIVER, str(install), str(state), str(home), str(bare), phase, cwd=str(install))

    print("old updater:", apply("old"))
    head = sh("git", "-C", str(install), "rev-parse", "HEAD")
    assert head == candidate, head
    assert '"0.2.0"' in (install / "immortal/__init__.py").read_text()
    assert (install / "immortal/core/install_migrations.py").exists()
    for name, text in preserved.items():
        assert (state / name).read_text() == text, name
    assert (state / "restart-called").read_text() == "old"
    updates_state = json.loads((state / "updates.json").read_text())
    assert updates_state["installed_commit"] == candidate and updates_state["available"] is None
    assert not updates_state["restart_required"]
    assert not (home / ".local/share/immortal-agents").exists()

    print("new updater:", apply("new"))
    assert sh("git", "-C", str(install), "rev-parse", "HEAD") == candidate
    updates_state = json.loads((state / "updates.json").read_text())
    assert updates_state["migration_commit"] == candidate and not updates_state["migration_required"]
    assert (state / "restart-called").read_text() == "new"
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["version"] == "0.2.0" and runtime["commit"] == candidate
    assert not (home / ".local/share/immortal-agents").exists(), "Codex component must stay off"
    for name, text in preserved.items():
        assert (state / name).read_text() == text, name

    sh("git", "-C", str(REPO), "push", "-q", str(bare), candidate + ":refs/heads/main")
    ancient = tmp / "ancient"
    sh("git", "clone", "-q", str(bare), str(ancient))
    sh("git", "-C", str(ancient), "checkout", "-q", "-B", "main", FIRST)
    assert not (ancient / "immortal/core/updates.py").exists()
    sh("git", "-C", str(ancient), "pull", "-q", "--ff-only", "origin", "main")
    assert sh("git", "-C", str(ancient), "rev-parse", "HEAD") == candidate
    print("pre-checker clone fast-forwarded:", FIRST, "->", candidate[:12])
    print("PASS: v0.1.0 old-updater upgrade, v0.2.0 same-version migration, pre-checker fast-forward")


if __name__ == "__main__":
    main()
