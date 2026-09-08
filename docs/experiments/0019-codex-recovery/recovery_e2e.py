#!/usr/bin/env python3
"""Bounded, credential-free actual-Codex tests against a stdlib loopback peer.

No machine network changes or existing sessions/config are touched. A silent
peer deliberately keeps its TCP socket open while dropping application/control
responses. It models a half-open path, not macOS system sleep.
"""

import argparse
import base64
import concurrent.futures
import hashlib
import http.server
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import threading
import time
import uuid


def binary_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as binary:
        for chunk in iter(lambda: binary.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recv_exact(sock, size):
    result = bytearray()
    while len(result) < size:
        part = sock.recv(size - len(result))
        if not part:
            raise EOFError
        result.extend(part)
    return bytes(result)


def recv_frame(sock):
    first, second = recv_exact(sock, 2)
    if not first & 128:
        raise ValueError("fixture does not support fragmented messages")
    size = second & 127
    if size == 126:
        size = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", recv_exact(sock, 8))[0]
    if size > 4_000_000:
        raise ValueError("fixture frame too large")
    if not second & 128:
        raise ValueError("client frames must be masked")
    mask = recv_exact(sock, 4)
    payload = recv_exact(sock, size)
    return first & 15, bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def send_frame(sock, opcode, payload):
    size = len(payload)
    if size < 126:
        head = bytes([128 | opcode, size])
    elif size < 65536:
        head = bytes([128 | opcode, 126]) + struct.pack("!H", size)
    else:
        head = bytes([128 | opcode, 127]) + struct.pack("!Q", size)
    sock.sendall(head + payload)


def completed_events(response_id, warmup=False, tool=None):
    output = []
    if tool:
        output = [tool]
    elif not warmup:
        output = [{"id": "msg_" + response_id, "type": "message", "role": "assistant",
                   "status": "completed", "content": [{"type": "output_text",
                   "text": "RECOVERY_OK", "annotations": []}]}]
    for item in output:
        yield {"type": "response.output_item.added", "output_index": 0, "item": item}
        if item["type"] == "message":
            yield {"type": "response.output_text.delta", "item_id": item["id"],
                   "output_index": 0, "content_index": 0, "delta": "RECOVERY_OK"}
        yield {"type": "response.output_item.done", "output_index": 0, "item": item}
    yield {"type": "response.completed", "response": {
        "id": response_id, "object": "response", "status": "completed",
        "model": "gpt-6-astra", "output": output,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}}}}


class Case:
    def __init__(self, name, args):
        self.name, self.args = name, args
        self.start = time.monotonic()
        self.events = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.request_started = threading.Event()
        self.requests = 0
        self.connections = 0
        self.tool_output_seen = False
        self.tool_issued = False
        self.tool_specs = []

    def log(self, event, **fields):
        with self.lock:
            self.events.append({"t": round(time.monotonic() - self.start, 4),
                                "event": event, **fields})

    def new_connection(self):
        with self.lock:
            self.connections += 1
            return self.connections

    def new_request(self, request, conn, size):
        with self.lock:
            self.requests += 1
            num = self.requests
        inputs = request.get("input", [])
        results = [i for i in inputs if i.get("type") in {"function_call_output", "custom_tool_call_output"}]
        self.tool_output_seen |= any(i.get("call_id") == "call_once" for i in results)
        self.log("request", number=num, connection=conn, bytes=size,
                 function_outputs=[i.get("call_id") for i in results],
                 fixture_tool_outputs=[str(i.get("output"))[:1500] for i in results if i.get("call_id") == "call_once"],
                 tool_names=[t.get("name", t.get("type")) for t in request.get("tools", [])])
        self.request_started.set()
        return num


class Peer(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_json(self, obj):
        send_frame(self.connection, 1, json.dumps(obj, separators=(",", ":")).encode())

    def do_GET(self):
        case = self.server.case
        if self.headers.get("Upgrade", "").lower() != "websocket":
            body = b'{"models":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        conn = case.new_connection()
        accept = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"] +
                "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        self.connection.settimeout(3)
        case.log("connected", connection=conn)
        try:
            while not case.stop.is_set():
                if not select.select([self.connection], [], [], 0.1)[0]:
                    continue
                opcode, data = recv_frame(self.connection)
                if opcode == 8:
                    case.log("client_close", connection=conn)
                    return
                if opcode == 9:
                    send_frame(self.connection, 10, data)
                    case.log("client_ping_outside_response", connection=conn)
                    continue
                if opcode != 1:
                    continue
                request = json.loads(data)
                case.log("request_shape", keys=sorted(request), type=request.get("type"),
                         other_types={k:type(v).__name__ for k,v in request.items()})
                if request.get("type") != "response.create":
                    continue
                tool_specs = request.get("tools", [])
                for item in request.get("input", []):
                    if item.get("type") == "additional_tools":
                        tool_specs = item.get("tools", [])
                if tool_specs:
                    case.tool_specs = tool_specs
                    case.log("tool_specs", names=[t.get("name") for t in tool_specs])
                warmup = request.get("generate") is False
                number = 0 if warmup else case.new_request(request, conn, len(data))
                response_id = f"resp_{conn}_{number}"
                self.send_json({"type": "response.created", "response": {
                    "id": response_id, "status": "in_progress", "output": [],
                    "model": "gpt-6-astra"}})
                case.log("created", number=number, connection=conn, warmup=warmup)
                if warmup:
                    for event in completed_events(response_id, warmup=True):
                        self.send_json(event)
                    continue
                if case.name == "tools" and not case.tool_issued:
                    functions = next((t for t in case.tool_specs if t.get("name") == "functions"), {})
                    names = {t.get("name") for t in functions.get("tools", case.tool_specs)}
                    if "exec" in names:
                        tool_name = "exec"
                        arguments = {"cmd": "printf 'once\\n' >> completed-tool.txt",
                                     "workdir": str(case.work), "login": False}
                        tool = {"type": "custom_tool_call", "id": "ctc_once", "call_id": "call_once",
                                "namespace": "functions", "name": "exec",
                                "input": "text(await tools.exec_command(" + json.dumps(arguments) + "));"}
                    else:
                        tool_name = "exec_command" if "exec_command" in names else "shell_command"
                        arguments = {"cmd" if tool_name == "exec_command" else "command":
                                     "printf 'once\\n' >> completed-tool.txt", "workdir": str(case.work)}
                        tool = {"type": "function_call", "id": "fc_once", "call_id": "call_once",
                                "name": tool_name, "arguments": json.dumps(arguments)}
                    for event in completed_events(response_id, tool=tool):
                        self.send_json(event)
                    case.tool_issued = True
                    case.log("tool_issued", name=tool_name)
                    continue
                failure_num = 2 if case.name == "tools" else 1
                is_initial = number == failure_num
                broken = is_initial and case.name in {"silent", "baseline_real", "wrong_pong", "tools", "cancel", "suspend", "late_pong", "server_ping_no_pong"}
                delay = case.args.quiet_seconds if case.name in {"quiet", "server_ping", "delayed_pong", "application_idle", "json_keepalive"} else 0
                if case.name == "suspend_queued_pong":
                    delay = max(case.args.quiet_seconds,
                                case.args.suspend_after + case.args.suspend_seconds + 12)
                if broken:
                    delay = case.args.case_timeout
                if case.name == "close" and is_initial:
                    case.log("server_close", connection=conn)
                    send_frame(self.connection, 8, struct.pack("!H", 1011) + b"synthetic failure")
                    return
                if not self.wait_for_response(case, conn, broken, delay):
                    return
                for event in completed_events(response_id):
                    self.send_json(event)
                case.log("completed", number=number, connection=conn)
        except (EOFError, OSError, ValueError) as error:
            case.log("connection_end", connection=conn, error=type(error).__name__)

    def wait_for_response(self, case, conn, broken, delay):
        start = time.monotonic()
        next_server_ping = start
        next_keepalive = start
        delayed = []
        while time.monotonic() - start < delay and not case.stop.is_set():
            now = time.monotonic()
            if case.name in {"server_ping", "application_idle", "server_ping_no_pong"} and now >= next_server_ping:
                send_frame(self.connection, 9, b"server-probe")
                case.log("server_ping", connection=conn)
                next_server_ping = now + 0.5
            if case.name == "json_keepalive" and now >= next_keepalive:
                self.send_json({"type": "keepalive"})
                case.log("json_keepalive", connection=conn)
                next_keepalive = now + 0.5
            for due, payload in list(delayed):
                if now >= due:
                    if case.name == "suspend_queued_pong":
                        # Exercise FIFO draining of a control frame before the
                        # queued matching Pong after only the client was paused.
                        send_frame(self.connection, 9, b"server-probe")
                        case.log("server_ping_before_delayed_pong", connection=conn)
                    send_frame(self.connection, 10, payload)
                    case.log("pong_sent", connection=conn, matched=True, delayed=True)
                    delayed.remove((due, payload))
            if not select.select([self.connection], [], [], 0.05)[0]:
                continue
            opcode, payload = recv_frame(self.connection)
            if opcode == 8:
                case.log("client_close", connection=conn)
                return False
            if opcode == 9:
                case.log("client_ping", connection=conn, payload_hex=payload.hex())
                if case.name == "wrong_pong" and broken:
                    send_frame(self.connection, 10, b"wrong-payload")
                    case.log("pong_sent", connection=conn, matched=False)
                elif case.name in {"delayed_pong", "late_pong", "suspend_queued_pong"}:
                    delayed.append((now + (12 if case.name == "late_pong" else case.args.pong_delay), payload))
                elif not broken:
                    send_frame(self.connection, 10, payload)
                    case.log("pong_sent", connection=conn, matched=True)
            elif opcode == 10:
                case.log("client_pong", connection=conn, matched=payload == b"server-probe")
            else:
                case.log("unexpected_frame", opcode=opcode, connection=conn)
        return not case.stop.is_set()

    def do_POST(self):
        case = self.server.case
        size = int(self.headers.get("Content-Length", "0"))
        if size > 4_000_000:
            self.send_error(413)
            return
        self.rfile.read(size)
        case.log("http_fallback")
        body = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n"
                        for event in completed_events("resp_http"))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def isolated_environment(home):
    allowed = {"PATH", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME", "SHELL", "TERM"}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    env.update(CODEX_HOME=str(home), NO_PROXY="127.0.0.1,localhost,::1", HOME=os.environ["HOME"])
    return env


def run_case(name, args, run_dir):
    case = Case(name, args)
    directory = run_dir / name
    home, work = directory / "home", directory / "work"
    home.mkdir(parents=True)
    work.mkdir()
    case.work = work
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    server.daemon_threads = True
    server.case = case
    threading.Thread(target=server.serve_forever, daemon=True).start()
    idle_ms = 300_000 if name == "baseline_real" else args.idle_ms
    if name in {"application_idle", "json_keepalive"}:
        idle_ms = 1500
    retries = 0 if name in {"baseline_real", "application_idle", "json_keepalive"} else 1
    config = f'''model = "gpt-6-astra"
model_provider = "loopback_recovery"
approval_policy = "never"
web_search = "disabled"
[features]
memories = false
plugins = false
shell_snapshot = false
code_mode = false
[analytics]
enabled = false
[feedback]
enabled = false
[memories]
use_memories = false
generate_memories = false
[model_providers.loopback_recovery]
name = "Local recovery fixture"
base_url = "http://127.0.0.1:{server.server_address[1]}/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = true
stream_idle_timeout_ms = {idle_ms}
stream_max_retries = {retries}
request_max_retries = 0
'''
    (home / "config.toml").write_text(config)
    command = [str(args.codex), "exec", "--json", "--ephemeral", "--skip-git-repo-check",
               "--ignore-rules", "--color", "never", "--sandbox", "workspace-write",
               "--cd", str(work), "Follow the synthetic fixture. Reply RECOVERY_OK when done."]
    process = subprocess.Popen(command, env=isolated_environment(home), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    timed_out = False
    try:
        if name in {"suspend", "suspend_queued_pong"}:
            if not case.request_started.wait(15):
                raise RuntimeError("no initial request before suspension")
            time.sleep(args.suspend_after)
            case.log("process_suspended")
            process.send_signal(signal.SIGSTOP)
            try:
                time.sleep(args.suspend_seconds)
            finally:
                process.send_signal(signal.SIGCONT)
                case.log("process_resumed")
        if name == "cancel":
            if not case.request_started.wait(15):
                raise RuntimeError("no initial request before cancellation")
            time.sleep(args.cancel_after)
            case.log("cancel_sent")
            process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=args.case_timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
    finally:
        if process.poll() is None:
            # Cleanup also covers fixture exceptions while its own child is paused.
            process.send_signal(signal.SIGCONT)
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        case.stop.set()
        server.shutdown()
        server.server_close()
    combined = stdout + "\n" + stderr
    client_events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    # Only structured stdout events count as visible; a Rust stderr log does not.
    retry_messages = [event["message"] for event in client_events
                      if event.get("type") == "error"
                      and event.get("message", "").startswith("Reconnecting")]
    completed_commands = [event["item"] for event in client_events
                          if event.get("type") == "item.completed"
                          and event.get("item", {}).get("type") == "command_execution"]
    (directory / "stdout.jsonl").write_text(stdout)
    (directory / "stderr.log").write_text(stderr)
    events = case.events
    normal = [e for e in events if e["event"] == "created" and not e["warmup"]]
    recovered = [e for e in events if e["event"] in {"http_fallback", "request"}
                 and (e["event"] == "http_fallback" or e["number"] > (2 if name == "tools" else 1))]
    first = normal[1 if name == "tools" and len(normal) > 1 else 0] if normal else None
    recovery = round(recovered[0]["t"] - first["t"], 4) if recovered and first else None
    resumed = next((e for e in events if e["event"] == "process_resumed"), None)
    resume_recovery = round(recovered[0]["t"] - resumed["t"], 4) if recovered and resumed else None
    marker = work / "completed-tool.txt"
    marker_lines = marker.read_text().splitlines() if marker.exists() else []
    result = {"case": name, "codex": str(args.codex), "binary_sha256": args.binary_sha256,
              "exit_code": process.returncode,
              "timed_out": timed_out, "elapsed_seconds": round(time.monotonic() - case.start, 3),
              "application_idle_ms": idle_ms, "recovery_seconds": recovery,
              "resume_recovery_seconds": resume_recovery,
              "client_ping_count": sum(e["event"] == "client_ping" for e in events),
              "client_pong_count": sum(e["event"] == "client_pong" for e in events),
              "request_count": case.requests, "connections": case.connections,
              "http_fallback": any(e["event"] == "http_fallback" for e in events),
              "idle_error": "idle timeout waiting for websocket" in combined,
              "reconnecting_visible": bool(retry_messages),
              "visible_retry_messages": retry_messages,
              "final_ok": "RECOVERY_OK" in stdout, "tool_output_seen": case.tool_output_seen,
              "completed_tool_lines": marker_lines, "events": events}
    checks = {"no_timeout": not timed_out}
    if name == "cancel":
        checks["no_retry_after_cancel"] = case.requests == 1
    else:
        checks["final_answer"] = result["final_ok"] and process.returncode == 0
    if args.expect == "patched":
        if name in {"silent", "wrong_pong", "tools", "late_pong", "server_ping_no_pong"}:
            checks["fast_recovery"] = recovery is not None and recovery < args.recovery_limit
            checks["heartbeat_used"] = result["client_ping_count"] > 0
            checks["first_retry_visible"] = len(retry_messages) == 1 and "1/1" in retry_messages[0]
            checks["exactly_one_ws_retry"] = case.requests == (3 if name == "tools" else 2) and not result["http_fallback"]
        if name == "suspend":
            checks["fast_resume_recovery"] = resume_recovery is not None and 0 <= resume_recovery < args.recovery_limit
            checks["heartbeat_used"] = result["client_ping_count"] > 0
            checks["first_retry_visible"] = len(retry_messages) == 1 and "1/1" in retry_messages[0]
            checks["exactly_one_ws_retry"] = case.requests == 2 and not result["http_fallback"]
        if name in {"quiet", "server_ping", "delayed_pong"}:
            checks["healthy_not_retried"] = case.requests == 1 and not result["http_fallback"]
            checks["heartbeat_used"] = result["client_ping_count"] > 0
        if name == "suspend_queued_pong":
            checks["healthy_suspended_connection_preserved"] = case.requests == 1 and case.connections == 1 and not result["http_fallback"]
            checks["no_reconnect_notification"] = not retry_messages
            paused = next((e for e in events if e["event"] == "process_suspended"), None)
            pending_frames = [e for e in events if paused and resumed and paused["t"] < e["t"] < resumed["t"]]
            checks["ping_then_matching_pong_queued_while_paused"] = any(
                first_frame["event"] == "server_ping_before_delayed_pong"
                and next_frame["event"] == "pong_sent" and next_frame.get("matched") is True
                for first_frame, next_frame in zip(pending_frames, pending_frames[1:]))
            checks["client_replied_to_queued_server_ping"] = result["client_pong_count"] > 0
    if name == "tools":
        checks["completed_tool_preserved_once"] = marker_lines == ["once"] and case.tool_output_seen
        checks["one_successful_command_event"] = len(completed_commands) == 1 and completed_commands[0].get("exit_code") == 0
        checks["tool_result_on_retry"] = bool(recovered) and recovered[0].get("function_outputs", []).count("call_once") == 1
    if name == "baseline_real":
        checks["default_idle_reproduced"] = recovery is not None and 299 <= recovery < 310 and result["idle_error"]
    if name == "application_idle":
        checks["separate_application_deadline"] = result["idle_error"] and result["http_fallback"] and result["client_pong_count"] > 0
    if name == "json_keepalive":
        checks["application_keepalive_control"] = not result["idle_error"] and not result["http_fallback"]
    if name == "close":
        checks["close_retries_promptly"] = recovery is not None and recovery < 3
        if args.expect == "patched":
            checks["first_retry_visible"] = len(retry_messages) == 1 and "1/1" in retry_messages[0]
    result["checks"], result["passed"] = checks, all(checks.values())
    (directory / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "events"}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--cases", default="suspend_queued_pong,silent,quiet,wrong_pong,server_ping,delayed_pong,late_pong,server_ping_no_pong,close,cancel,suspend,tools,application_idle,json_keepalive")
    parser.add_argument("--expect", choices=["baseline", "patched"], default="baseline")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "runs")
    parser.add_argument("--idle-ms", type=int, default=45000)
    parser.add_argument("--quiet-seconds", type=float, default=18)
    parser.add_argument("--pong-delay", type=float, default=2)
    parser.add_argument("--cancel-after", type=float, default=7)
    parser.add_argument("--suspend-after", type=float, default=6)
    parser.add_argument("--suspend-seconds", type=float, default=20)
    parser.add_argument("--case-timeout", type=float, default=75)
    parser.add_argument("--recovery-limit", type=float, default=17)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    known_cases = {"silent", "quiet", "wrong_pong", "server_ping", "delayed_pong",
                   "late_pong", "server_ping_no_pong", "close", "cancel", "suspend",
                   "tools", "application_idle", "json_keepalive", "baseline_real", "suspend_queued_pong"}
    if set(args.cases.split(",")) - known_cases:
        parser.error("unknown fixture case")
    args.codex = args.codex.expanduser().absolute()
    # SIGSTOP must target the native Codex child, not its npm launcher.
    resolved = args.codex.resolve()
    if resolved.name == "codex.js":
        native = resolved.parents[2] / "codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
        if native.is_file():
            args.codex = native
        elif {"suspend", "suspend_queued_pong"} & set(args.cases.split(",")):
            parser.error("suspend requires the native Codex executable, not a launcher")
    args.binary_sha256 = binary_sha256(args.codex)
    run_dir = args.output_dir / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    run_dir.mkdir(parents=True)
    print(json.dumps({"run_directory": str(run_dir), "real_model_requests": 0}), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = [executor.submit(run_case, name, args, run_dir) for name in args.cases.split(",")]
        results = [future.result() for future in futures]
    unchanged_binary = binary_sha256(args.codex) == args.binary_sha256
    report = {"cases": results, "passed": all(r["passed"] for r in results) and unchanged_binary,
              "real_model_requests": 0, "binary_sha256": args.binary_sha256,
              "binary_unchanged_during_suite": unchanged_binary}
    (run_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"report": str(run_dir / "report.json"), "passed": report["passed"]}), flush=True)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
