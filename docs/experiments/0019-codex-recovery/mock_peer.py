#!/usr/bin/env python3
"""Expose the reviewed loopback fixture to an independently isolated BB pilot."""
import argparse
import http.server
import json
from pathlib import Path
import threading

from recovery_e2e import Case, Peer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=["silent", "quiet", "wrong_pong", "server_ping", "delayed_pong", "close"], default="silent")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.quiet_seconds = 18
    args.pong_delay = 2
    args.case_timeout = args.seconds
    case = Case(args.case, args)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    server.daemon_threads = True
    server.case = case
    config_args = []
    for key, value in {
        "model_provider": "loopback_recovery",
        "model_providers.loopback_recovery.name": "Local recovery fixture",
        "model_providers.loopback_recovery.base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
        "model_providers.loopback_recovery.wire_api": "responses",
        "model_providers.loopback_recovery.requires_openai_auth": False,
        "model_providers.loopback_recovery.supports_websockets": True,
        "model_providers.loopback_recovery.stream_idle_timeout_ms": 45000,
        "model_providers.loopback_recovery.stream_max_retries": 1,
        "model_providers.loopback_recovery.request_max_retries": 0,
    }.items():
        config_args.extend(["-c", key + "=" + json.dumps(value)])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    record = {"endpoint": f"http://127.0.0.1:{server.server_address[1]}/v1",
              "codex_args": config_args, "expected_output": "RECOVERY_OK"}
    args.output.write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        case.stop.wait(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        case.stop.set()
        server.shutdown()
        server.server_close()
        record["events"] = case.events
        args.output.write_text(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
