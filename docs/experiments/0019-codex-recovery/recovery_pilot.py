#!/usr/bin/env python3
"""Run the real BB bridge and patched Codex against one bounded loopback outage."""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "mock_peer.py"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-binary", type=Path, required=True)
    parser.add_argument("--label", default="final", choices=["preflight", "final"])
    args = parser.parse_args()
    binary = args.codex_binary.resolve(strict=True)
    run = ROOT / "recovery-runs" / time.strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True)
    manifest = run / "fixture.json"
    fixture = None
    result = {"label": args.label, "binary": str(binary), "case": "silent", "fixture_manifest": str(manifest),
              "binary_sha256_before": sha256(binary)}
    try:
        with (run / "fixture.log").open("w") as log:
            fixture = subprocess.Popen([sys.executable, "-B", str(FIXTURE), "--case", "silent", "--seconds", "120", "--output", str(manifest)], stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 5
            while not manifest.exists():
                if time.monotonic() >= deadline or fixture.poll() is not None:
                    raise RuntimeError("Loopback fixture did not start")
                time.sleep(.05)
            config = json.loads(manifest.read_text())
            command = [sys.executable, str(ROOT / "pilot.py"), "--codex-binary", str(binary),
                       "--codex-args", json.dumps(["app-server", *config["codex_args"]]),
                       "--model", "gpt-6-astra", "--expected-output", config["expected_output"],
                       "--turn-timeout", "90", "--require-reconnect", "--observer-diagnostics"]
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = child.communicate(timeout=115)
            except subprocess.TimeoutExpired:
                # Interrupt the driver so its finally block shuts down the exact BB instance.
                child.send_signal(signal.SIGINT)
                stdout, stderr = child.communicate(timeout=25)
                result["driver_timed_out"] = True
            (run / "pilot.log").write_text(stdout + stderr)
            print(stdout, end="", flush=True)
            result["pilot_exit_code"] = child.returncode
            result["success"] = child.returncode == 0 and not result.get("driver_timed_out")
            for line in stdout.splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("run_dir"):
                    report_path = Path(entry["run_dir"]) / "report.json"
                    if report_path.exists():
                        report = json.loads(report_path.read_text())
                        result["bb_report"] = str(report_path)
                        result["bb_evidence"] = {key: report.get(key) for key in (
                            "first_retry_feedback", "output", "native_observer_registration",
                            "launcher_exit_code", "ports_closed", "keep_awake",
                        )}
    except Exception as error:
        result["success"] = False
        result["error"] = str(error)
    finally:
        if fixture is not None and fixture.poll() is None:
            fixture.send_signal(signal.SIGINT)
            try:
                fixture.wait(timeout=5)
            except subprocess.TimeoutExpired:
                fixture.kill()
                fixture.wait(timeout=3)
        if manifest.exists():
            state = json.loads(manifest.read_text())
            port = urlparse(state["endpoint"]).port
            with socket.socket() as sock:
                sock.settimeout(.25)
                result["fixture_port_closed"] = sock.connect_ex(("127.0.0.1", port)) != 0
            result["fixture_events"] = state.get("events", [])
            requests = [event for event in result["fixture_events"] if event["event"] == "request"]
            completed = [event for event in result["fixture_events"] if event["event"] == "completed"]
            if requests and completed:
                result["recovery_seconds_from_first_request"] = round(completed[-1]["t"] - requests[0]["t"], 4)
        result["fixture_exit_code"] = fixture.poll() if fixture is not None else None
        result["binary_sha256_after"] = sha256(binary)
        result["binary_unchanged"] = result["binary_sha256_before"] == result["binary_sha256_after"]
        result["success"] = result["success"] and result["binary_unchanged"]
        (run / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"recovery_run": str(run), "success": result["success"], "fixture_port_closed": result.get("fixture_port_closed")}), flush=True)
    return 0 if result["success"] and result.get("fixture_port_closed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
