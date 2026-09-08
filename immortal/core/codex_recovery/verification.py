"""Explicit read-only host checks; never restart or message an agent."""

import ctypes
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

from . import package


def matches(candidate, root, expected_hash):
    try:
        target = root / "current/bin/codex"
        return (Path(candidate).is_absolute() and Path(candidate).resolve() == target.resolve()
                and package.digest(candidate) == expected_hash)
    except (OSError, ValueError):
        return False


def login_shell(home, root, expected_hash):
    environment = os.environ.copy()
    if home != Path.home().absolute():
        environment["ZDOTDIR"] = str(home)
    try:
        result = subprocess.run(["/bin/zsh", "-ilc", "command -v codex"], env=environment,
                                capture_output=True, text=True, timeout=20)
        lines = result.stdout.strip().splitlines()
        if result.returncode == 0 and lines and matches(lines[-1], root, expected_hash):
            return "Fresh zsh login shell: managed binary verified."
        return "Fresh zsh login shell: managed binary is not selected; inspect later PATH overrides."
    except (OSError, subprocess.SubprocessError):
        return "Fresh zsh login shell: could not verify within 20 seconds."


def bb_selection(home, root, expected_hash):
    executable = shutil.which("bb")
    try:
        host = (home / ".bb/host-id").read_text().strip()
        if not executable or not re.fullmatch(r"host_[A-Za-z0-9]+", host):
            return "BB new-launch selection: unavailable on this home."
        result = subprocess.run([executable, "machine", "provider-cli", "status", host, "--json"],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            return "BB new-launch selection: unavailable; BB may not be running."
        path = json.loads(result.stdout).get("codex", {}).get("executablePath")
        if path and matches(path, root, expected_hash):
            return "BB new-launch selection: managed executable and hash verified."
        return "BB new-launch selection: another executable is selected; allow PATH refresh, then check again."
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return "BB new-launch selection: could not verify."


def native_processes(root):
    if platform.system() != "Darwin":
        return "Running Codex processes: inspection requires macOS."
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib")
        library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        library.proc_pidpath.restype = ctypes.c_int
        result = subprocess.run(["/bin/ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=10)
        if result.returncode:
            return "Running Codex processes: inspection unavailable."
        selected = other = unknown = 0
        target = (root / "current/bin/codex").resolve()
        for line in result.stdout.splitlines():
            pieces = line.strip().split(None, 1)
            if len(pieces) != 2 or Path(pieces[1]).name != "codex":
                continue
            buffer = ctypes.create_string_buffer(4096)
            if library.proc_pidpath(int(pieces[0]), buffer, len(buffer)) <= 0:
                unknown += 1
            elif Path(os.fsdecode(buffer.value)).resolve() == target:
                selected += 1
            else:
                other += 1
        return f"Running native Codex processes: {selected} selected build, {other} other executable, {unknown} unreadable. Counts are processes, not threads; other executables may need a normal idle-session restart."
    except (OSError, ValueError, subprocess.SubprocessError):
        return "Running Codex processes: inspection unavailable."


def check(home, root, manifest):
    expected = manifest["files"]["bin/codex"]["sha256"]
    lines = [login_shell(home, root, expected), bb_selection(home, root, expected)]
    if home == Path.home().absolute():
        lines.append(native_processes(root))
    else:
        lines.append("Running Codex processes: omitted for the developer test home.")
    return "\n".join(lines)
