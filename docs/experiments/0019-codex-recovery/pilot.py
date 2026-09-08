#!/usr/bin/env python3
"""Start one isolated BB stack, run one fixture turn, collect evidence, stop it."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
APP = Path("/Applications/bb.app/Contents/Resources/app.asar.unpacked/node_modules/bb-app")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-binary", type=Path)
    parser.add_argument("--codex-args", default='["app-server"]', help="JSON array; only used with --codex-binary")
    parser.add_argument("--model", default="bb-pilot-mock")
    parser.add_argument("--prompt", default="Reply BB_PILOT_WIRING_OK. Do not call any tools.")
    parser.add_argument("--expected-output", default="BB_PILOT_WIRING_OK")
    parser.add_argument("--turn-timeout", type=int, default=60)
    parser.add_argument("--require-reconnect", action="store_true", help="Require first-retry feedback in BB's timeline")
    parser.add_argument("--mock-retry", action="store_true", help="Emit a retry notification from the offline fixture")
    parser.add_argument("--observer-diagnostics", action="store_true", help="Capture native observer registration from this pilot only")
    args = parser.parse_args()
    run = ROOT / "runs" / time.strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True)
    workspace = run / "workspace"
    workspace.mkdir()
    (run / "codex-home").mkdir()
    (run / "bin").mkdir()
    # The instance may inspect local CLIs. This path shadows the real codex binary.
    target = args.codex_binary.resolve(strict=True) if args.codex_binary else ROOT / "mock_codex.py"
    shim = run / "bin" / "codex"
    custom_args = json.loads(args.codex_args)
    if not isinstance(custom_args, list) or any(not isinstance(value, str) for value in custom_args):
        parser.error("--codex-args must be a JSON array of strings")
    # BB strips inherited BB_* variables before starting the provider bridge.
    # A PATH shim therefore pins the binary even when launcher overrides vanish.
    shim.write_text("#!/usr/bin/python3\nimport os,sys\n"
        + f"os.environ['CODEX_HOME'] = {str(run / 'codex-home')!r}\n"
        + f"os.environ['BB_PILOT_RPC_LOG'] = {str(run / 'mock-rpc.jsonl')!r}\n"
        + f"os.environ['BB_PILOT_SIMULATE_RETRY'] = {str(args.mock_retry)!r}\n"
        + ("os.environ['RUST_LOG'] = 'warn,codex_http_client::network_changes=debug,codex_api::endpoint::responses_websocket_stream=debug'\n" if args.observer_diagnostics else "")
        + f"target = {str(target)!r}\n"
        + f"arguments = {custom_args!r} if sys.argv[1:2] == ['app-server'] else sys.argv[1:]\n"
        + "os.execv(target, [target, *arguments])\n")
    shim.chmod(0o755)
    # The app's native SQLite dependency targets Electron's ABI, not system Node.
    node = "/Applications/bb.app/Contents/MacOS/bb"
    ports = [free_port(), free_port()]
    while ports[0] == ports[1]:
        ports[1] = free_port()
    server_url = f"http://127.0.0.1:{ports[0]}"
    # Start from an allowlist: never inherit the current thread, auth, proxy or BB context.
    env = {key: os.environ[key] for key in ("USER", "LOGNAME", "LANG", "TMPDIR") if key in os.environ}
    env.update({
        "PATH": str(run / "bin") + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
        "SHELL": "/bin/sh",
        "ELECTRON_RUN_AS_NODE": "1",
        "BB_DATA_DIR": str(run / "data"),
        "BB_SERVER_BIND_HOST": "127.0.0.1",
        "BB_SERVER_PORT": str(ports[0]),
        "BB_SERVER_URL": server_url,
        "BB_HOST_DAEMON_PORT": str(ports[1]),
        "BB_TELEMETRY": "false",
        "BB_CODEX_BRIDGE_APP_SERVER_COMMAND": str(shim),
        "BB_CODEX_BRIDGE_APP_SERVER_ARGS": args.codex_args if args.codex_binary else "[]",
        "BB_PILOT_RPC_LOG": str(run / "mock-rpc.jsonl"),
        "CODEX_HOME": str(run / "codex-home"),
    })
    subprocess.run(["git", "init", "-q", str(workspace)], env=env, check=True)
    report = {"bb_version": json.loads((APP / "package.json").read_text())["version"],
              "run_dir": str(run), "server_url": server_url, "daemon_port": ports[1],
              "codex_command": str(target), "mode": "custom-binary" if args.codex_binary else "mock"}
    command = [node, str(APP / "dist/bb-app.js"), "--data-dir", str(run / "data"),
               "--server-bind-host", "127.0.0.1", "--server-port", str(ports[0]),
               "--host-daemon-port", str(ports[1])]
    process = None

    def cli(*argv):
        result = subprocess.run([node, str(APP / "dist/bb.js"), *argv], env=env,
                                cwd=workspace, capture_output=True, text=True, timeout=args.turn_timeout + 15)
        if result.returncode:
            raise RuntimeError(f"BB command {argv[:2]} failed: {result.stderr[-2000:]}")
        return json.loads(result.stdout)

    try:
        with (run / "launcher.log").open("w") as log:
            process = subprocess.Popen(command, env=env, cwd=workspace, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            report["launcher_pid"] = process.pid
            print(json.dumps({"run_dir": str(run), "launcher_pid": process.pid}), flush=True)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Isolated BB exited before it became ready; inspect launcher.log")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{ports[1]}/status", timeout=1) as response:
                        status = json.load(response)
                    if status.get("connected") and status.get("serverUrl", "").rstrip("/") == server_url:
                        report["daemon_status"] = {key: status.get(key) for key in ("connected", "serverUrl", "hostId", "dataDir")}
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(.2)
            else:
                raise RuntimeError("Isolated BB did not become ready within 45 seconds")
            report["status"] = cli("status", "--json")
            if report["status"].get("dataDir") != str(run / "data"):
                raise RuntimeError("The CLI did not target the isolated data directory")
            report["keep_awake"] = cli("keep-awake", "status", "--json")
            if report["keep_awake"].get("enabled"):
                raise RuntimeError("Isolated Keep Awake is unexpectedly enabled")
            project = cli("project", "create", "--name", "Codex recovery pilot", "--root", str(workspace), "--json")
            report["project"] = project
            project_id = project.get("id") or project.get("project", {}).get("id")
            report["provider_models"] = cli("provider", "models", "codex", "--json")
            thread = cli("thread", "spawn", "--project", project_id, "--provider", "codex", "--model", args.model,
                         "--reasoning-level", "medium", "--permission-mode", "accept-edits",
                         "--title", "Isolated recovery pilot", "--prompt", args.prompt, "--json")
            report["thread"] = thread
            thread_id = thread.get("id") or thread.get("thread", {}).get("id")
            report["wait"] = cli("thread", "wait", thread_id, "--status", "idle", "--timeout", str(args.turn_timeout), "--json")
            report["output"] = cli("thread", "output", thread_id, "--json")
            if args.expected_output not in report["output"].get("output", ""):
                raise RuntimeError("The isolated turn did not produce its expected final output")
            timeline = cli("thread", "log", thread_id, "--all", "--json")
            def feedback_nodes(value):
                if isinstance(value, dict):
                    if value.get("willRetry") is True and re.search(r"Reconnecting\.\.\. 1/\d+", str(value.get("detail", "")) + str(value.get("message", ""))):
                        yield {key: value[key] for key in ("message", "detail", "willRetry", "type", "kind") if key in value}
                    for child in value.values():
                        yield from feedback_nodes(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from feedback_nodes(child)
            report["first_retry_feedback"] = list(feedback_nodes(timeline))
            if args.require_reconnect and not report["first_retry_feedback"]:
                raise RuntimeError("BB did not persist the first retry's reconnect feedback")
            report["stop"] = cli("thread", "stop", thread_id, "--json")
            if args.observer_diagnostics:
                observer_lines = []
                log_database = run / "codex-home/logs_2.sqlite"
                if log_database.exists():
                    with sqlite3.connect(log_database.as_uri() + "?mode=ro", uri=True) as database:
                        observer_lines.extend(row[0] for row in database.execute(
                            "SELECT feedback_log_body FROM logs WHERE target LIKE ? AND feedback_log_body LIKE ?",
                            ("codex_http_client::network_changes%", "%observer registered%")))
                observer_lines.extend(line for line in (run / "launcher.log").read_text(errors="replace").splitlines()
                                      if "connection recovery observer registered" in line)
                report["native_observer_registration"] = {
                    "observed": bool(observer_lines),
                    "wake": any(re.search(r'wake(?:\\?"\s*:|=)\s*true', line) for line in observer_lines),
                    "network": any(re.search(r'network(?:\\?"\s*:|=)\s*true', line) for line in observer_lines),
                    "physical_wake_tested": False,
                }
                if not all(report["native_observer_registration"][key] for key in ("observed", "wake", "network")):
                    raise RuntimeError("The pilot did not confirm both native observer registrations")
            report["success"] = True
    except Exception as error:
        report["success"] = False
        report["error"] = str(error)
    finally:
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        report["launcher_exit_code"] = None if process is None else process.poll()
        report["ports_closed"] = []
        for port in ports:
            with socket.socket() as sock:
                sock.settimeout(.25)
                report["ports_closed"].append(sock.connect_ex(("127.0.0.1", port)) != 0)
        (run / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({key: report.get(key) for key in ("success", "error", "run_dir", "ports_closed")}), flush=True)
    return 0 if report.get("success") and all(report["ports_closed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
