import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time

from immortal.core import runtime, updates
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


def restart_watcher(state_dir, repo=updates.REPO):
    expected = runtime.identity(repo)
    previous = process_id()
    since = time.time()
    updates.launchctl("kickstart", "-k", f"gui/{os.getuid()}/{WATCHER_LABEL}")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pid = process_id()
        if pid and pid != previous and started(state_dir, pid, since):
            time.sleep(0.5)
            loaded = runtime.read(state_dir, pid)
            if (process_id() == pid and runtime.matches(loaded, expected)
                    and isinstance(loaded.get("started_at"), (int, float))
                    and loaded["started_at"] >= since
                    and runtime.identity(repo) == expected):
                return
        time.sleep(0.25)
    raise updates.UpdateError("Running code was not verified. Run ./install.sh restart or inspect watcher logs")


def activate(repo=updates.REPO, state_dir=STATE_DIR, home=None, *, ship=False):
    """Activate a committed local checkout; optionally push it first."""
    target = check_checkout(repo)
    watcher_config(repo, state_dir, home)
    with updates.locked(state_dir):
        check_unchanged(repo, target)
        if ship:
            git(repo, "fetch", "origin", "main")
            git(repo, "merge-base", "--is-ancestor", "origin/main", target)
            check_unchanged(repo, target)
            git(repo, "push", "origin", target + ":refs/heads/main")
            if runtime.github_head(repo) != target:
                raise updates.UpdateError("GitHub main differs from the intended commit; activation not confirmed")
        state = updates.load_state(state_dir)
        state["restart_required"] = True
        updates.save_state(state_dir, state)
        check_unchanged(repo, target)
        restart_watcher(state_dir, repo)
        check_unchanged(repo, target)
        state["restart_required"] = False
        state["installed_commit"] = target
        state["installed_at"] = time.time()
        updates.save_state(state_dir, state)
        return f"Active: {updates.installed_version(repo)} ({target[:12]}); running code verified" + (
            "; pushed to GitHub" if ship else "")



def check_unchanged(repo, expected):
    try:
        unchanged = check_checkout(repo) == expected
    except updates.UpdateError as exc:
        raise updates.UpdateError("Checkout changed during update; local edits were preserved. "
                                  "Watcher was not restarted. Finish your edits, then retry ./install.sh update") from exc
    if not unchanged:
        raise updates.UpdateError("Checkout commit changed during update; local work was preserved. "
                                  "Watcher was not restarted. Inspect the checkout before retrying")


def migrate_components(repo, state_dir, state, home=None, expected_commit=None):
    """Run the installed release's migration code, not cached pre-update imports."""
    path = Path(repo) / "immortal/core/install_migrations.py"
    if not path.is_file():
        if updates.version(updates.installed_version(repo)) >= (0, 2, 0):
            raise updates.UpdateError("Release is missing component migrations; update incomplete")
        return ""
    state["migration_required"] = True
    updates.save_state(state_dir, state)
    expected_commit = expected_commit or check_checkout(repo)
    check_unchanged(repo, expected_commit)
    command = [sys.executable, "-m", "immortal.core.install_migrations", "--repo", str(repo)]
    if home is not None:
        command += ["--home", str(home)]
    try:
        result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=900,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    except subprocess.TimeoutExpired as exc:
        raise updates.UpdateError("Component migration timed out; update remains incomplete. "
                                  "Inspect ./install.sh status, then retry ./install.sh update") from exc
    if result.returncode:
        raise updates.UpdateError("Component migration failed; inspect ./install.sh status. "
                                  "Run ./install.sh update again. " + result.stderr[-1500:].strip())
    check_unchanged(repo, expected_commit)
    state["migration_required"] = False
    state["migration_commit"] = expected_commit
    updates.save_state(state_dir, state)
    return result.stdout.strip()


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
        if updates.version(release["version"]) <= current:
            component = migrate_components(repo, state_dir, state, home, expected_commit=before)
            if state.get("restart_required") or not runtime.matches(
                    runtime.read(state_dir, process_id()), runtime.identity(repo)):
                state["restart_required"] = True
                updates.save_state(state_dir, state)
                check_unchanged(repo, before)
                restart_watcher(state_dir, repo)
                check_unchanged(repo, before)
                state["restart_required"] = False
                state["installed_commit"] = before
                state["installed_at"] = time.time()
                updates.save_state(state_dir, state)
            if state.get("available") and updates.version(state["available"]["version"]) <= current:
                state["available"] = None
                updates.save_state(state_dir, state)
            return "Already at or ahead of the latest public release; watcher confirmed active" + ("; " + component if component else "")
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
        if updates.version(shipped) >= (0, 2, 0):
            try:
                git(repo, "cat-file", "-e", target + ":immortal/core/install_migrations.py")
            except updates.UpdateError as exc:
                raise updates.UpdateError("Release is missing component migrations; checkout unchanged") from exc
        if check_checkout(repo) != before:
            raise updates.UpdateError("Checkout changed while fetching; retry after other work finishes")
        state["restart_required"] = True
        updates.save_state(state_dir, state)
        if before != target:
            git(repo, "merge", "--ff-only", "--no-overwrite-ignore", target)
        if git(repo, "rev-parse", "HEAD") != target:
            raise updates.UpdateError("Checkout changed during update; watcher was not restarted")
        component = migrate_components(repo, state_dir, state, home, expected_commit=target)
        try:
            check_unchanged(repo, target)
        except updates.UpdateError:
            state["migration_required"] = True
            updates.save_state(state_dir, state)
            raise
        restart_watcher(state_dir, repo)
        check_unchanged(repo, target)
        state["restart_required"] = False
        state["installed_commit"] = target
        state["installed_at"] = time.time()
        state["available"] = None
        state.pop("check_error", None)
        updates.save_state(state_dir, state)
        return (f"Updated to {release['version']}; watcher restart confirmed. Running agents were left alone."
                + (" " + component if component else ""))


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restart", action="store_true")
    mode.add_argument("--ship", action="store_true")
    args = parser.parse_args()
    try:
        print(activate(ship=args.ship) if args.restart or args.ship else apply())
    except (updates.UpdateError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Update failed: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
