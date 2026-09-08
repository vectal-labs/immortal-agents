#!/usr/bin/env python3
"""Isolated native exec recovery/logging checks; loopback peer, no credentials."""

import argparse
import concurrent.futures
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace


FIXTURE = Path(__file__).parent / "0019-codex-recovery/recovery_e2e.py"
TARGET = "codex_core::recovery_reporting"


def deliver(home, directory, expected):
    """Replay actual native records through two watcher processes and local HTTP."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            payload = json.dumps({"id": str(1000 + len(received))}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    state_dir = directory / "watcher"
    state_dir.mkdir()
    # Fixture replay starts before the captured events; live first scans baseline
    # the current maximum instead. No source database is modified.
    (state_dir / "state.json").write_text(json.dumps({"native_codex": {str(home.resolve()): {"cursor": 0}}}))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = dict(os.environ, WATCHER_STATE_DIR=str(state_dir),
               DISCORD_WEBHOOK_URL=f"http://127.0.0.1:{server.server_port}/webhooks/test",
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    code = """
import sys, time
from immortal.core import native_recovery, discord_outbox, logbook
state = logbook.load_state()
native_recovery.tick(state, homes=[sys.argv[1]])
for _ in range(500):
    discord_outbox.tick(state)
    if not state.get('discord_outbox'):
        break
    time.sleep(.01)
else:
    raise AssertionError('local webhook delivery did not complete')
assert not any(source.get('error') for source in state.get('native_codex', {}).values())
"""
    try:
        for _ in range(2):
            subprocess.run([sys.executable, "-c", code, str(home)], env=env,
                           check=True, capture_output=True, text=True, timeout=15)
    finally:
        server.shutdown()
        server.server_close()
    state = json.loads((state_dir / "state.json").read_text())
    return {"webhook_count_after_restart": len(received) == int(expected),
            "saved_receipts": len(state.get("discord_delivered", {})) == int(expected),
            "webhook_acknowledged": all("wait=true" in path and "Recovery confirmed" in body["content"]
                                        for path, body in received)}


def run(binary, root, name, ephemeral, baseline):
    # Reuse the proven loopback harness; only persistence differs from its default.
    source = FIXTURE.read_text()
    assert source.count('"--ephemeral", ') == 1
    if not ephemeral:
        source = source.replace('"--ephemeral", ', '', 1)
    if name == "cancel":
        # Keep both original and retried streams silent, then cancel after retry starts.
        source = source.replace("is_initial = number == failure_num", "is_initial = number == failure_num or case.name == \"cancel\"")
        source = source.replace('case.log("cancel_sent")', 'case.log("cancel_sent", request_count=case.requests)')
        source = source.replace('checks["no_retry_after_cancel"] = case.requests == 1',
                                'checks["no_retry_after_cancel"] = case.requests == next(e["request_count"] for e in case.events if e["event"] == "cancel_sent")')
    mode = "ephemeral" if ephemeral else "persistent"
    directory = root / f"{mode}-{name}"
    directory.mkdir()
    module_path = directory / "fixture.py"
    module_path.write_text(source)
    spec = importlib.util.spec_from_file_location(f"fixture_{mode}_{name}", module_path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    environment = fixture.isolated_environment

    def isolated_environment(home):
        env = environment(home)
        env["RUST_LOG"] = "off"
        return env

    fixture.isolated_environment = isolated_environment
    args = SimpleNamespace(codex=binary, binary_sha256=fixture.binary_sha256(binary),
                           idle_ms=45000, quiet_seconds=18, pong_delay=2, cancel_after=17,
                           suspend_after=6, suspend_seconds=20, case_timeout=75,
                           recovery_limit=17, expect="patched")
    transport = fixture.run_case(name, args, directory)
    home = directory / name / "home"
    log_path = home / "logs_2.sqlite"
    events = []
    if log_path.exists():
        with closing(sqlite3.connect(f"file:{log_path}?mode=rw", uri=True)) as db:
            events = db.execute("select thread_id, feedback_log_body from logs where target=? order by id", (TARGET,)).fetchall()
    started = [(thread, message) for thread, message in events if "recovery_started" in message]
    confirmed = [(thread, message) for thread, message in events if "recovery_confirmed" in message]
    unconfirmed = [(thread, message) for thread, message in events if "recovery_unconfirmed" in message]
    expected = name not in {"quiet", "cancel"} and not baseline
    expected_started = name != "quiet" and not baseline
    checks = {"transport_passed": transport["passed"],
              "event_count": len(started) == (1 if expected_started else 0) and len(confirmed) == (1 if expected else 0),
              "terminal_count": len(unconfirmed) == (1 if name == "cancel" and not baseline else 0),
              "ephemeral_has_no_rollout": not ephemeral or not list(home.glob("sessions/**/*.jsonl"))}
    if expected and started and confirmed:
        fields = lambda message: dict(re.findall(r'(turn_id|recovery_id)="?([A-Za-z0-9-]+)', message))
        a, b = fields(started[0][1]), fields(confirmed[0][1])
        checks["same_thread_turn_episode"] = bool(started[0][0] and started[0][0] == confirmed[0][0]
                                                   and a == b and set(a) == {"turn_id", "recovery_id"})
        checks["sanitized"] = all("RECOVERY_OK" not in message and "Follow the synthetic" not in message
                                   for _, message in events)
        if not ephemeral:
            rollouts = list(home.glob("sessions/**/*.jsonl"))
            rows = [json.loads(line) for path in rollouts for line in path.read_text().splitlines()]
            checks["matching_rollout_turn"] = any(row.get("type") == "turn_context"
                and row.get("payload", {}).get("turn_id") == a.get("turn_id") for row in rows)
    if not baseline:
        checks.update(deliver(home, directory, expected))
    return {"case": f"{mode}-{name}", "checks": checks, "passed": all(checks.values()),
            "started": len(started), "confirmed": len(confirmed), "unconfirmed": len(unconfirmed), "rust_log": "off"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    root = args.output_dir or Path(tempfile.mkdtemp(prefix="codex-exec-reporting-"))
    root.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        cases = [("silent", False), ("silent", True), ("quiet", False), ("close", True), ("cancel", True)]
        futures = [pool.submit(run, args.codex.resolve(), root, name, ephemeral, args.baseline)
                   for name, ephemeral in cases]
        results = [future.result() for future in futures]
    report = {"passed": all(result["passed"] for result in results), "cases": results,
              "baseline": args.baseline, "real_model_requests": 0}
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
