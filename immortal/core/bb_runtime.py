"""Save a launchd-safe Node + bb pair and run every bb command through it."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from immortal.core.bb_node import DEFAULT_BB, discover_bb, is_exe, is_temp_path, node_candidates, stabilize
from immortal.core.common import STATE_DIR

WATCHER_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
# Concrete bind/connect failures only — not "connect"/"daemon" (bb lives in host-daemon/).
SERVER_HINTS = ("econnrefused", "econnreset", "ehostunreach")


class BbRuntimeError(Exception):
    pass


def state_dir():
    return Path(os.environ.get("WATCHER_STATE_DIR", str(STATE_DIR)))


def runtime_path():
    return state_dir() / "bb_runtime.json"


def load_runtime():
    try:
        data = json.loads(runtime_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    node, bb = data.get("node"), data.get("bb")
    if is_exe(bb) and is_exe(node) and not is_temp_path(node):
        return {"node": node, "bb": bb}
    return None


def save_runtime(runtime, update_plist=False):
    state_dir().mkdir(parents=True, exist_ok=True)
    runtime_path().write_text(json.dumps({"node": runtime["node"], "bb": runtime["bb"]}, indent=2) + "\n")
    plist = os.environ.get("IMMORTAL_PLIST") if update_plist else None
    if not (plist and os.path.isfile(plist)):
        return
    with open(plist, "rb") as fh:
        data = plistlib.load(fh)
    env = data.setdefault("EnvironmentVariables", {})
    env["IMMORTAL_NODE"] = runtime["node"]
    env["BB_BIN"] = runtime["bb"]
    with open(plist, "wb") as fh:
        plistlib.dump(data, fh, fmt=plistlib.FMT_XML)


def command_for(runtime, args):
    return [runtime["node"], runtime["bb"], *args]


def watcher_env(runtime=None):
    """The LaunchAgent contract: default launchd PATH plus the saved Node/bb pair."""
    runtime = runtime or load_runtime() or {}
    path = WATCHER_PATH
    if runtime.get("node"):
        path = os.path.dirname(runtime["node"]) + ":" + WATCHER_PATH
    env = {
        "PATH": path,
        "HOME": os.environ.get("HOME", str(Path.home())),
        "WATCHER_STATE_DIR": str(state_dir()),
        "CMUX_QUIET": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if runtime.get("node"):
        env["IMMORTAL_NODE"] = runtime["node"]
    if runtime.get("bb"):
        env["BB_BIN"] = runtime["bb"]
    for key in ("USER", "LOGNAME", "TMPDIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


bb_env = watcher_env


def server_down(proc):
    text = ((proc.stderr or "") + (proc.stdout or "")).lower()
    return any(hint in text for hint in SERVER_HINTS)


def repair(kind):
    return {
        "missing": "Install Node 22 or newer, then run ./install.sh",
        "incompatible": "Upgrade Node to a current release, then run ./install.sh",
        "stale": "Node moved after an upgrade. Run ./install.sh to rediscover it",
    }[kind]


def run_command(runtime, args, env=None, timeout=30):
    return subprocess.run(
        command_for(runtime, args), capture_output=True, text=True,
        timeout=timeout, env=env or watcher_env(runtime),
    )


def discover_runtime(env=None):
    env = env or os.environ
    bb = discover_bb(env)
    if not bb:
        raise BbRuntimeError("bb is not installed")
    saw_node, saw_incompat = False, False
    for cand in node_candidates(env):
        node = stabilize(cand, env)
        if not node:
            continue
        saw_node = True
        runtime = {"node": node, "bb": bb}
        try:
            proc = run_command(runtime, ["thread", "list", "--json"], env=watcher_env(runtime))
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0 or server_down(proc):
            return runtime
        err = (proc.stderr or "").lower()
        if "incompatible" in err or "syntaxerror" in err:
            saw_incompat = True
    if saw_incompat or saw_node:
        raise BbRuntimeError("Node is incompatible with bb. " + repair("incompatible"))
    raise BbRuntimeError("Node not found. " + repair("missing"))


def get_runtime(refresh=False, persist=False):
    if not refresh:
        saved = load_runtime()
        if saved:
            return saved
    runtime = discover_runtime()
    if persist:
        save_runtime(runtime)
    return runtime


def bb_cmd(args):
    return command_for(get_runtime(), args)


def run_bb(args, timeout=30, env=None, persist=True):
    """Run bb once. Rediscover only when the executable is missing (pre-start)."""
    saved = load_runtime()
    runtime = saved or get_runtime(persist=False)
    try:
        proc = run_command(runtime, args, env=env or watcher_env(runtime), timeout=timeout)
    except OSError:
        try:
            runtime = get_runtime(refresh=True, persist=False)
            proc = run_command(runtime, args, env=env or watcher_env(runtime), timeout=timeout)
        except (OSError, BbRuntimeError) as exc:
            raise BbRuntimeError("bb could not be started. " + repair("stale")) from exc
        if persist:
            save_runtime(runtime)
        return proc
    if persist and saved is None and runtime_path().is_file():
        save_runtime(runtime)
    return proc


def available():
    return bool(discover_bb())


def diagnose(save=False):
    if not discover_bb():
        return "skip", "not installed, skipped"
    try:
        runtime = get_runtime(refresh=save, persist=save)
        if save:
            save_runtime(runtime, update_plist=True)
    except BbRuntimeError as exc:
        return "error", str(exc)
    try:
        proc = run_bb(["thread", "list", "--json"], env=watcher_env(runtime), persist=save)
    except (OSError, subprocess.TimeoutExpired, BbRuntimeError) as exc:
        return "error", f"{exc}. {repair('stale')}"
    if proc.returncode == 0:
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return "error", "bb returned non-JSON. " + repair("incompatible")
        if not isinstance(data, list):
            return "error", "bb thread list had an unexpected shape"
        return "ok", f"reachable via {runtime['node']}"
    if server_down(proc):
        return "warn", "app is not running. Open bb, then run ./install.sh check"
    err = (proc.stderr or "").strip() or "bb failed"
    kind = "incompatible" if "incompatible" in err.lower() or "syntaxerror" in err.lower() else "stale"
    return "error", f"{err[-200:]}. {repair(kind)}"


def main(argv=None):
    verb = (argv or sys.argv[1:] or ["check"])[0]
    status, msg = diagnose(save=(verb == "install"))
    print(f"{status}:{msg}")
    return {"ok": 0, "skip": 0, "warn": 2, "error": 1}[status]


if __name__ == "__main__":
    raise SystemExit(main())
