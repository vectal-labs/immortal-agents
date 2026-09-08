"""Identify the code loaded by the watcher, separately from files on disk."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from immortal.core import updates
from immortal.core.common import STATE_DIR


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, timeout=10)
    if result.returncode:
        raise OSError("Git identity unavailable")
    return result.stdout.strip()


def identity(repo=updates.REPO):
    repo = Path(repo).resolve()
    digest = hashlib.sha256()
    paths = [repo / "watcher.py", repo / "revive.py", *sorted((repo / "immortal").rglob("*.py"))]
    for path in paths:
        if path.is_file():
            digest.update(str(path.relative_to(repo)).encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    try:
        commit = git(repo, "rev-parse", "HEAD")
        modified = bool(git(repo, "status", "--porcelain", "--untracked-files=all", "--",
                            "watcher.py", "revive.py", "immortal"))
    except (OSError, subprocess.SubprocessError):
        commit = None
        modified = True
    return {"repo": str(repo), "version": updates.installed_version(repo),
            "commit": commit, "source_hash": digest.hexdigest(), "modified": modified}


def record(loaded, state_dir=STATE_DIR):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    data = {**loaded, "pid": os.getpid(), "started_at": time.time()}
    fd, name = tempfile.mkstemp(prefix="runtime-", suffix=".tmp", dir=state_dir)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, state_dir / "runtime.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read(state_dir, pid):
    try:
        data = json.loads((Path(state_dir) / "runtime.json").read_text())
        if (pid and isinstance(data, dict) and data.get("pid") == pid
                and isinstance(data.get("started_at"), (int, float))
                and 0 < data["started_at"] <= time.time()
                and all(isinstance(data.get(key), str) for key in ("version", "repo", "source_hash"))
                and (data.get("commit") is None or isinstance(data["commit"], str))):
            return data
    except (OSError, ValueError):
        pass
    return None


def matches(loaded, expected):
    return bool(loaded and expected.get("commit") and all(
        loaded.get(key) == expected[key] for key in ("repo", "version", "commit", "source_hash")))


def github_head(repo):
    # Use the public repository, never a potentially private configured remote.
    result = git(repo, "ls-remote", updates.PUBLIC_REPO + ".git", "refs/heads/main")
    return result.split()[0] if result else None


def status(repo, state_dir, pid, *, check_github=True):
    expected = identity(repo)
    loaded = read(state_dir, pid)
    active = matches(loaded, expected)
    short = lambda commit: (commit or "unknown")[:12]
    edits = " + local edits" if expected["modified"] else ""
    lines = [f"Checkout: {expected['version']} ({short(expected['commit'])}{edits})"]
    if loaded:
        edits = " + local edits" if loaded.get("modified") else ""
        lines.append(f"Running: {loaded.get('version', 'unknown')} ({short(loaded.get('commit'))}{edits}), PID {pid}")
    else:
        lines.append("Running version: unknown (no matching watcher startup record)")
    lines.append("Active: YES — running code matches this checkout" if active else
                 "Active: NO — run ./install.sh restart to load and verify this checkout")
    if check_github:
        try:
            remote = github_head(repo)
            if not remote:
                raise OSError("No main branch")
            relation = "matches checkout" if remote == expected["commit"] else "differs from checkout"
            lines.append(f"GitHub main: {short(remote)} ({relation})")
        except (OSError, subprocess.SubprocessError):
            lines.append("GitHub main: unknown (could not check; local activation check still applies)")
    return "\n".join(lines), active


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=updates.REPO)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--local", action="store_true", help="verify local activation without checking GitHub")
    args = parser.parse_args()
    try:
        output, active = status(args.repo, STATE_DIR, args.pid, check_github=not args.local)
        print(output)
        return 0 if active else 1
    except (OSError, ValueError, updates.UpdateError) as exc:
        print("Active: UNKNOWN — " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
