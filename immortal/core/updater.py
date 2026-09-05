import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time

from immortal.core import updates
from immortal.core.common import STATE_DIR, parse_ts

WATCHER_LABEL = "com.immortal-agents.watcher"
ALLOWED_REMOTES = {updates.PUBLIC_REPO, updates.PUBLIC_REPO + ".git",
                   "git@github.com:vectal-labs/immortal-agents.git",
                   "ssh://git@github.com/vectal-labs/immortal-agents.git"}


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise updates.UpdateError("Git " + args[0] + " failed; no local changes were discarded")
    return result.stdout.strip()


def check_checkout(repo):
    repo = Path(repo).resolve()
    if not (repo / ".git").is_dir() or Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise updates.UpdateError("Update only the installed primary checkout, not a worktree")
    if git(repo, "branch", "--show-current") != "main":
        raise updates.UpdateError("Update requires the main branch; switch branches yourself")
    if git(repo, "remote", "get-url", "--all", "origin") not in ALLOWED_REMOTES:
        raise updates.UpdateError("Automatic update only supports the public immortal-agents origin")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise updates.UpdateError("Checkout has local changes or untracked files; commit or move them yourself")
    return git(repo, "rev-parse", "HEAD")


def watcher_config(repo, state_dir, home=None):
    path = Path(home or Path.home()) / "Library" / "LaunchAgents" / (WATCHER_LABEL + ".plist")
    try:
        with path.open("rb") as stream:
            config = plistlib.load(stream)
        arguments = config["ProgramArguments"]
        valid = (config.get("Label") == WATCHER_LABEL and len(arguments) == 2
                 and Path(arguments[1]).resolve() == Path(repo).resolve() / "watcher.py"
                 and Path(config["WorkingDirectory"]).resolve() == Path(repo).resolve()
                 and Path(config["EnvironmentVariables"]["WATCHER_STATE_DIR"]).resolve() == Path(state_dir).resolve()
                 and os.access(arguments[0], os.X_OK))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        valid = False
    if not valid:
        raise updates.UpdateError("Watcher installation does not match this checkout; run ./install.sh first")
    return path


def process_id():
    result = updates.launchctl("print", f"gui/{os.getuid()}/{WATCHER_LABEL}", check=False)
    found = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.MULTILINE)
    return int(found[1]) if result.returncode == 0 and found else None


def started(state_dir, pid, since):
    try:
        with (Path(state_dir) / "watcher.log").open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            lines = stream.read().splitlines()
        for line in reversed(lines):
            try:
                record = json.loads(line)
                if isinstance(record, dict) and record.get("event") == "start" and record.get("pid") == pid:
                    stamp = parse_ts(record.get("ts"))
                    if stamp and stamp.timestamp() >= since:
                        return True
            except (ValueError, UnicodeError):
                continue
    except OSError:
        pass
    return False


def restart_watcher(state_dir):
    previous = process_id()
    since = time.time()
    updates.launchctl("kickstart", "-k", f"gui/{os.getuid()}/{WATCHER_LABEL}")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pid = process_id()
        if pid and pid != previous and started(state_dir, pid, since):
            time.sleep(0.5)
            if process_id() == pid:
                return
        time.sleep(0.25)
    raise updates.UpdateError("Code updated, but watcher startup was not confirmed. Run ./install.sh update again or inspect watcher logs")


def apply(repo=updates.REPO, state_dir=STATE_DIR, home=None):
    repo = Path(repo).resolve()
    check_checkout(repo)
    watcher_config(repo, state_dir, home)
    with updates.locked(state_dir):
        before = check_checkout(repo)
        state = updates.load_state(state_dir)
        releases = updates.validate_feed(updates.fetch_feed())
        if not releases:
            raise updates.UpdateError("No public release has been announced yet")
        release = releases[0]
        current = updates.version(updates.installed_version(repo))
        if updates.version(release["version"]) <= current and not state.get("restart_required"):
            return "Already at or ahead of the latest public release"
        if updates.version(release["version"]) < current:
            raise updates.UpdateError("Refusing to downgrade this checkout")
        if not updates.published(release):
            raise updates.UpdateError("The public release is not available; nothing was changed")
        git(repo, "fetch", "--no-tags", "origin", "refs/tags/v" + release["version"])
        target = git(repo, "rev-parse", "FETCH_HEAD^{commit}")
        if target != release["commit"]:
            raise updates.UpdateError("Release tag does not match the announced commit")
        git(repo, "merge-base", "--is-ancestor", before, target)
        shipped = updates.source_version(git(repo, "show", target + ":immortal/__init__.py"))
        if shipped != release["version"]:
            raise updates.UpdateError("Release code has the wrong version")
        installer = git(repo, "show", target + ":install.sh")
        syntax = subprocess.run(["/bin/bash", "-n"], input=installer, capture_output=True, text=True, timeout=10)
        if syntax.returncode:
            raise updates.UpdateError("Release installer is invalid")
        for path in ("watcher.py", "immortal/core/updates.py", "immortal/core/updater.py"):
            git(repo, "cat-file", "-e", target + ":" + path)
        if check_checkout(repo) != before:
            raise updates.UpdateError("Checkout changed while fetching; retry after other work finishes")
        state["restart_required"] = True
        updates.save_state(state_dir, state)
        if before != target:
            git(repo, "merge", "--ff-only", "--no-overwrite-ignore", target)
        if git(repo, "rev-parse", "HEAD") != target:
            raise updates.UpdateError("Checkout changed during update; watcher was not restarted")
        restart_watcher(state_dir)
        state["restart_required"] = False
        state["installed_commit"] = target
        state["installed_at"] = time.time()
        state["available"] = None
        state.pop("check_error", None)
        updates.save_state(state_dir, state)
        return f"Updated to {release['version']}; watcher restart confirmed. Other agents and settings were left alone."


def main():
    try:
        print(apply())
    except (updates.UpdateError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Update failed: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
