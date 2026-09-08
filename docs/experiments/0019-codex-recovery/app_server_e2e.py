#!/usr/bin/env python3
"""Exercise public app-server cancellation and session resume on local fixtures."""
import argparse
import http.server
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

from recovery_e2e import Case, Peer, binary_sha256, isolated_environment


class Client:
    def __init__(self, binary, home, output, case):
        self.case = case
        self.messages = []
        self.pending = queue.Queue()
        self.next_id = 1
        self.error_file = (output / "app-server-stderr.log").open("a")
        self.process = subprocess.Popen([str(binary), "app-server", "--stdio"],
            env=isolated_environment(home), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.error_file, text=True, start_new_session=True)
        threading.Thread(target=self.read, daemon=True).start()
        try:
            self.request("initialize", {"clientInfo": {"name": "local_recovery_e2e",
                "version": "1.0"}, "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def read(self):
        for line in self.process.stdout:
            message = json.loads(line)
            self.messages.append(message)
            if message.get("method") == "error":
                params = message.get("params", {})
                self.case.log("app_server_error", message=params.get("error", {}).get("message"),
                              will_retry=params.get("willRetry"), thread_id=params.get("threadId"),
                              turn_id=params.get("turnId"))
            self.pending.put(message)
        self.pending.put({"fixture_eof": True})

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def wait(self, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.pending.get(timeout=max(0.01, deadline - time.monotonic()))
            if message.get("fixture_eof"):
                raise RuntimeError("app-server exited unexpectedly")
            if predicate(message):
                return message
            if "id" in message and "method" in message:
                raise RuntimeError("unexpected server request: " + message["method"])
        raise TimeoutError("app-server response deadline")

    def request(self, method, params, timeout=30):
        number = self.next_id
        self.next_id += 1
        self.send({"id": number, "method": method, "params": params})
        response = self.wait(lambda m: m.get("id") == number, timeout)
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response["result"]

    def start_turn(self, thread_id):
        return self.request("turn/start", {"threadId": thread_id,
            "input": [{"type": "text", "text": "Follow the local fixture and finish."}]})["turn"]["id"]

    def completion(self, turn_id, timeout=45):
        # turn/completed may arrive before the start/interrupt response.
        def matches(m):
            return m.get("method") == "turn/completed" and m.get("params", {}).get("turn", {}).get("id") == turn_id
        existing = next((m for m in self.messages if matches(m)), None)
        return (existing or self.wait(matches, timeout))["params"]["turn"]

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        self.error_file.close()


def run(args):
    original_sha256 = binary_sha256(args.codex)
    directory = args.output_dir / (time.strftime("%Y%m%d-%H%M%S") + "-rpc-" + uuid.uuid4().hex[:6])
    home, work = directory / "home", directory / "work"
    home.mkdir(parents=True)
    work.mkdir()
    case = Case("cancel" if args.case == "cancel" else "tools", args)
    case.work = work
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    server.daemon_threads = True
    server.case = case
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (home / "config.toml").write_text(f'''model = "gpt-6-astra"
model_provider = "loopback_recovery"
approval_policy = "never"
web_search = "disabled"
[features]
plugins = false
shell_snapshot = false
memories = false
[analytics]
enabled = false
[feedback]
enabled = false
[model_providers.loopback_recovery]
name = "Local recovery fixture"
base_url = "http://127.0.0.1:{server.server_address[1]}/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = true
stream_idle_timeout_ms = {args.idle_ms}
stream_max_retries = 1
request_max_retries = 0
''')
    clients = []
    checks = {}
    try:
        client = Client(args.codex, home, directory, case)
        clients.append(client)
        thread = client.request("thread/start", {"model": "gpt-6-astra", "cwd": str(work),
            "approvalPolicy": "never", "sandbox": "workspace-write", "ephemeral": False})["thread"]["id"]
        turn = client.start_turn(thread)
        if args.case == "cancel":
            if not case.request_started.wait(15):
                raise RuntimeError("missing request")
            time.sleep(args.cancel_after)
            case.log("rpc_interrupt_requested")
            started = time.monotonic()
            client.request("turn/interrupt", {"threadId": thread, "turnId": turn})
            completed = client.completion(turn)
            elapsed = time.monotonic() - started
            checks["interrupted_status"] = completed["status"] == "interrupted"
            checks["cancellation_under_3s"] = elapsed < 3
            time.sleep(2)
            checks["no_retry_after_cancel"] = case.requests == 1
            # Same app-server and conversation accept a new turn after cancellation.
            continued = client.start_turn(thread)
            checks["next_turn_completed"] = client.completion(continued)["status"] == "completed"
            case.log("rpc_cancellation_complete", elapsed=round(elapsed, 4))
        else:
            checks["first_turn_completed"] = client.completion(turn, args.case_timeout)["status"] == "completed"
            client.close()
            case.log("app_server_restarted")
            client = Client(args.codex, home, directory, case)
            clients.append(client)
            resumed = client.request("thread/resume", {"threadId": thread, "cwd": str(work),
                "approvalPolicy": "never", "sandbox": "workspace-write"})
            checks["same_thread_resumed"] = resumed["thread"]["id"] == thread
            resumed_turn = client.start_turn(thread)
            checks["resumed_turn_completed"] = client.completion(resumed_turn, args.case_timeout)["status"] == "completed"
            marker = work / "completed-tool.txt"
            checks["completed_command_once"] = marker.exists() and marker.read_text().splitlines() == ["once"]
            requests = [e for e in case.events if e["event"] == "request"]
            checks["tool_output_restored_on_resume"] = "call_once" in requests[-1]["function_outputs"]
            checks["tool_output_preserved_on_retry"] = len(requests) == 4 and requests[2]["function_outputs"].count("call_once") == 1
            completed_commands = [message["params"]["item"] for app_client in clients
                for message in app_client.messages if message.get("method") == "item/completed"
                and message.get("params", {}).get("item", {}).get("type") == "commandExecution"]
            checks["exactly_one_successful_command_notification"] = len(completed_commands) == 1 and completed_commands[0].get("exitCode") == 0
            if args.expect == "patched":
                retry_errors = [e for e in case.events if e["event"] == "app_server_error"
                    and (e.get("message") or "").startswith("Reconnecting")]
                checks["first_retry_notification_visible"] = (len(retry_errors) == 1
                    and "1/1" in retry_errors[0]["message"] and retry_errors[0]["will_retry"] is True
                    and retry_errors[0]["thread_id"] == thread and retry_errors[0]["turn_id"] == turn
                    and retry_errors[0]["t"] < requests[2]["t"])
                failed_request = next(e for e in case.events if e["event"] == "created" and e["number"] == 2)
                checks["fast_transport_retry"] = requests[2]["t"] - failed_request["t"] < 17
        checks["binary_unchanged"] = binary_sha256(args.codex) == original_sha256
        report = {"case": args.case, "checks": checks, "passed": all(checks.values()),
            "events": case.events, "real_model_requests": 0, "binary_sha256": original_sha256,
            "app_server_messages": [client.messages for client in clients]}
        (directory / "result.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({"report": str(directory / "result.json"), "checks": checks,
                          "passed": report["passed"]}), flush=True)
        return report["passed"]
    finally:
        for client in clients:
            client.close()
        case.stop.set()
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--case", choices=["cancel", "tools_resume"], required=True)
    parser.add_argument("--expect", choices=["baseline", "patched"], default="baseline")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "runs")
    parser.add_argument("--idle-ms", type=int, default=45000)
    parser.add_argument("--case-timeout", type=float, default=65)
    parser.add_argument("--cancel-after", type=float, default=7)
    args = parser.parse_args()
    args.quiet_seconds, args.pong_delay = 18, 2
    raise SystemExit(0 if run(args) else 1)


if __name__ == "__main__":
    main()
