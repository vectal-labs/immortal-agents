"""Locate Node and the bb CLI without depending on a login-shell PATH."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

DEFAULT_BB = (
    "/Applications/bb.app/Contents/Resources/app.asar.unpacked/"
    "node_modules/bb-app/host-daemon/dist/bb"
)
KNOWN_NODES = ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node")
TEMP_MARKERS = ("fnm_multishells",)


def is_temp_path(path):
    text = (path or "").replace("\\", "/")
    return any(marker in text for marker in TEMP_MARKERS)


def prefer_stable(path):
    real = os.path.realpath(path)
    parts = Path(path).parts
    if "Cellar" in parts:
        i = parts.index("Cellar")
        prefix = Path(*parts[:i])
        keg = parts[i + 1] if i + 1 < len(parts) else "node"
        for opt in (prefix / "opt" / keg / "bin" / "node", prefix / "bin" / "node"):
            if opt.is_file() and os.path.realpath(opt) == real:
                return str(opt)
    for opt in KNOWN_NODES:
        if os.path.isfile(opt) and os.path.realpath(opt) == real:
            return opt
    return path


def is_exe(path):
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def discover_bb(env=None):
    env = env or os.environ
    for key in ("BB_BIN", "BB_CLI"):
        if env.get(key):
            return env[key] if is_exe(env[key]) else None
    which = shutil.which("bb", path=env.get("PATH", ""))
    if is_exe(which):
        return which
    return DEFAULT_BB if is_exe(DEFAULT_BB) else None


def exec_path(node, env):
    try:
        proc = subprocess.run(
            [node, "-p", "process.execPath"],
            capture_output=True, text=True, timeout=10, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = proc.stdout.strip()
    return path if proc.returncode == 0 and path else None


def version_key(name):
    parts = []
    for chunk in (name or "").lstrip("v").split("."):
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    return tuple(parts)


def nvm_node(nvm):
    versions, alias = Path(nvm) / "versions" / "node", Path(nvm) / "alias" / "default"
    if alias.is_file():
        name = alias.read_text().strip()
        if name.startswith("lts"):
            linked = Path(nvm) / "alias" / name
            if linked.is_file():
                name = linked.read_text().strip()
        for cand in (name, "v" + name.lstrip("v")):
            node = versions / cand / "bin" / "node"
            if node.is_file():
                return str(node)
        prefix = version_key(name)
        if versions.is_dir() and prefix:
            hits = []
            for path in versions.iterdir():
                key = version_key(path.name)
                if key[:len(prefix)] == prefix and (path / "bin" / "node").is_file():
                    hits.append((key, path))
            if hits:
                return str(max(hits)[1] / "bin" / "node")
    current = Path(nvm) / "current" / "bin" / "node"
    return str(current) if current.is_file() else None


def manager_which(exe, args, env):
    bin_path = shutil.which(exe, path=env.get("PATH"))
    if not bin_path:
        return None
    try:
        proc = subprocess.run([bin_path, *args], capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    return line if proc.returncode == 0 and is_exe(line) else None


def node_candidates(env=None):
    env = env or os.environ
    home = env.get("HOME") or str(Path.home())
    found = []

    def add(path):
        if is_exe(path) and path not in found:
            found.append(path)

    add(env.get("IMMORTAL_NODE") or env.get("NODE_BIN"))
    add(shutil.which("node", path=env.get("PATH", "")))
    hints = env["IMMORTAL_NODE_HINTS"].split(":") if "IMMORTAL_NODE_HINTS" in env else KNOWN_NODES
    for hint in hints:
        add(hint)
    add(nvm_node(env.get("NVM_DIR") or os.path.join(home, ".nvm")))
    for root in (env.get("FNM_DIR"), os.path.join(home, ".fnm"), os.path.join(home, ".local", "share", "fnm")):
        if root:
            add(os.path.join(root, "aliases", "default", "bin", "node"))
    volta = env.get("VOLTA_HOME") or os.path.join(home, ".volta")
    add(os.path.join(volta, "bin", "node"))
    add(manager_which("volta", ["which", "node"], env))
    add(manager_which("asdf", ["which", "node"], env))
    add(manager_which("mise", ["which", "node"], env))
    add(os.path.join(home, ".asdf", "shims", "node"))
    add(os.path.join(home, ".local", "share", "mise", "shims", "node"))
    add(os.path.join(home, ".nodenv", "shims", "node"))
    return found


def stabilize(node, env):
    resolved = exec_path(node, env) or node
    if is_temp_path(resolved) or not is_exe(resolved):
        return None
    stable = prefer_stable(resolved)
    return stable if is_exe(stable) else None
