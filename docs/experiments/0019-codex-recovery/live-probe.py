#!/usr/bin/env python3
"""Bounded live WebSocket control-frame checks. No inference or tool requests."""
import asyncio
import json
import logging
import os
from pathlib import Path
import statistics
import time
import uuid

from websockets.asyncio.client import ClientConnection, connect
from websockets.frames import Frame, Opcode

ROOT = Path(__file__).resolve().parent
ENDPOINT = "wss://chatgpt.com/backend-api/codex/responses"
logging.disable(logging.CRITICAL)


class ObservedConnection(ClientConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exact_pongs = {}
        self.server_pings = 0

    def process_event(self, event):
        if isinstance(event, Frame):
            if event.opcode == Opcode.PONG:
                pending = self.exact_pongs.get(bytes(event.data))
                if pending is not None and not pending.done():
                    pending.set_result(time.perf_counter())
            elif event.opcode == Opcode.PING:
                self.server_pings += 1
        super().process_event(event)


async def probe(ws, payload):
    # Match the actual received nonce, not the library's cumulative pong ack.
    pending = asyncio.get_running_loop().create_future()
    ws.exact_pongs[payload] = pending
    started = time.perf_counter()
    try:
        library_waiter = await ws.ping(payload)
        library_waiter.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        arrived = await asyncio.wait_for(pending, 10)
        return round((arrived - started) * 1000, 3)
    finally:
        ws.exact_pongs.pop(payload, None)


def headers_for(identity):
    # Credentials exist only in this process and the authenticated TLS request.
    auth = json.loads((Path.home() / ".codex/auth.json").read_text())
    tokens = auth["tokens"]
    return {
        "Authorization": "Bearer " + tokens["access_token"],
        "ChatGPT-Account-ID": tokens["account_id"],
        "OpenAI-Beta": "responses_websockets=2026-02-06",
        "originator": "codex_cli_rs",
        "version": "0.153.4",
        "session_id": identity,
        "thread_id": identity,
        "x-client-request-id": identity,
    }


def connection(identity=None):
    return connect(ENDPOINT, additional_headers=headers_for(identity or str(uuid.uuid4())),
                   user_agent_header="codex_cli_rs/0.153.4 (Mac OS; arm64) codex-recovery-transport-probe",
                   create_connection=ObservedConnection, compression=None, proxy=None,
                   open_timeout=15, close_timeout=2, ping_interval=None, max_queue=64)


async def once(payload, quiet=0):
    async with connection() as ws:
        if quiet:
            await asyncio.sleep(quiet)
        return [await probe(ws, payload)], {"quiet_seconds": quiet, "server_pings_received": ws.server_pings}


async def sequence(count, interval=0, quiet_after=0):
    async with connection() as ws:
        timings = []
        for index in range(count):
            timings.append(await probe(ws, os.urandom(16)))
            if interval and index != count - 1:
                await asyncio.sleep(interval)
        if quiet_after:
            await asyncio.sleep(quiet_after)
            timings.append(await probe(ws, os.urandom(16)))
        return timings, {"interval_seconds": interval, "quiet_after_seconds": quiet_after,
                         "server_pings_received": ws.server_pings}


async def burst():
    async with connection() as ws:
        timings = await asyncio.gather(*(probe(ws, os.urandom(16)) for _ in range(5)))
        return timings, {"outstanding_probes": 5, "server_pings_received": ws.server_pings}


async def concurrent_connections():
    results = await asyncio.gather(*(once(os.urandom(16)) for _ in range(2)))
    return [timing for timings, _ in results for timing in timings], {"connections": 2}


async def reconnect(abrupt=False, reused_identity=False):
    identity = str(uuid.uuid4())
    async with connection(identity) as ws:
        first = await probe(ws, os.urandom(16))
        if abrupt:
            # Only this probe socket is closed. No system network changes.
            ws.transport.abort()
    async with connection(identity if reused_identity else None) as ws:
        second = await probe(ws, os.urandom(16))
        return [first, second], {"abrupt_local_close": abrupt, "reused_session_identity": reused_identity}


async def unsolicited_pong():
    async with connection() as ws:
        await ws.pong(os.urandom(16))
        return [await probe(ws, os.urandom(16))], {"sent_unsolicited_pong": True}


async def main():
    cases = [
        ("01_fresh_connection_random_nonce", lambda: once(os.urandom(16))),
        ("02_ascii_nonce", lambda: once(b"codex-recovery-check")),
        ("03_binary_nonce", lambda: once(bytes(range(16)))),
        ("04_maximum_control_payload", lambda: once(os.urandom(125))),
        ("05_empty_control_payload", lambda: once(b"")),
        ("06_quiet_5_seconds", lambda: once(os.urandom(16), quiet=5)),
        ("07_quiet_15_seconds", lambda: once(os.urandom(16), quiet=15)),
        ("08_quiet_45_seconds", lambda: once(os.urandom(16), quiet=45)),
        ("09_five_sequential_probes", lambda: sequence(5)),
        ("10_five_outstanding_probes", burst),
        ("11_periodic_probes_then_quiet", lambda: sequence(3, interval=1, quiet_after=5)),
        ("12_two_concurrent_connections", concurrent_connections),
        ("13_clean_reconnect_fresh_identity", lambda: reconnect()),
        ("14_abrupt_reconnect_same_identity", lambda: reconnect(abrupt=True, reused_identity=True)),
        ("15_ping_after_unsolicited_pong", unsolicited_pong),
    ]
    results = []
    semaphore = asyncio.Semaphore(3)

    async def run_case(name, operation):
        async with semaphore:
            started = time.perf_counter()
            try:
                timings, details = await asyncio.wait_for(operation(), timeout=75)
                result = {"case": name, "passed": True, "exact_nonce_matches": len(timings),
                          "rtt_ms": timings, **details}
            except Exception as error:
                # Never serialize exception bodies, request objects or headers.
                result = {"case": name, "passed": False, "error_type": type(error).__name__}
                response = getattr(error, "response", None)
                if response is not None:
                    result["http_status"] = getattr(response, "status_code", None)
            result["duration_seconds"] = round(time.perf_counter() - started, 3)
            results.append(result)
            print(json.dumps(result), flush=True)

    await asyncio.gather(*(run_case(name, operation) for name, operation in cases))
    timings = [value for result in results for value in result.get("rtt_ms", [])]
    summary = {"endpoint": ENDPOINT, "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "case_count": len(results), "passed": sum(result["passed"] for result in results),
               "inference_requests": 0, "tool_requests": 0, "exact_pong_match_count": len(timings),
               "rtt_ms_min": min(timings) if timings else None,
               "rtt_ms_median": statistics.median(timings) if timings else None,
               "rtt_ms_max": max(timings) if timings else None,
               "limitations": ["Healthy-network transport compatibility only; no physical sleep or network loss.",
                               "Matching Pong proves the WebSocket peer responds, not model progress.",
                               "Fresh probes do not reveal whether the historical stalled connection would have replied."],
               "cases": sorted(results, key=lambda result: result["case"])}
    path = ROOT / "live-probe-results.json"
    path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}), flush=True)
    return 0 if summary["passed"] == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
