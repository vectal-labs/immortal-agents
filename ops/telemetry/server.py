#!/usr/bin/env python3
"""Bounded JSONL telemetry sink. Stdlib only; stats cover retained events."""
import collections
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

EVENTS = os.path.join(os.environ.get("DATA_DIR", "."), "events.jsonl")
MAX_REQUEST_BYTES = 16 * 1024
MAX_EVENT_CHARS = 128
MAX_LOG_BYTES = 10 * 1024 * 1024


def valid_event(event):
    return (isinstance(event, dict) and isinstance(event.get("event"), str)
            and bool(event["event"].strip()) and len(event["event"]) <= MAX_EVENT_CHARS)


def append_event(line):
    try:
        size = os.path.getsize(EVENTS)
    except FileNotFoundError:
        size = 0
    separator = b""
    if size:
        with open(EVENTS, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                separator = b"\n"
    if size + len(separator) + len(line) > MAX_LOG_BYTES:
        if size <= MAX_LOG_BYTES:
            os.replace(EVENTS, f"{EVENTS}.1")
        else:
            # Drop the first partial line when bounding an old, oversized log.
            with open(EVENTS, "rb") as fh:
                fh.seek(-MAX_LOG_BYTES, os.SEEK_END)
                tail = fh.read(MAX_LOG_BYTES).partition(b"\n")[2]
            with open(f"{EVENTS}.1", "wb") as fh:
                fh.write(tail)
            with open(EVENTS, "wb"):
                pass
        separator = b""
    with open(EVENTS, "ab") as fh:
        fh.write(separator + line)


def event_counts():
    counts = collections.Counter()
    for path in (f"{EVENTS}.1", EVENTS):
        try:
            fh = open(path, "rb")
        except FileNotFoundError:
            continue
        with fh:
            while True:
                line = fh.readline(MAX_REQUEST_BYTES + 2)
                if not line:
                    break
                if len(line) > MAX_REQUEST_BYTES + 1:
                    # Drain oversized legacy/corrupt records without loading them.
                    while line and not line.endswith(b"\n"):
                        line = fh.readline(MAX_REQUEST_BYTES + 2)
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if valid_event(event):
                    counts[event["event"]] += 1
    return counts


class Handler(BaseHTTPRequestHandler):
    timeout = 5

    def log_message(self, *_args):
        # BaseHTTPRequestHandler logs client IPs and request paths by default.
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if code == 405:
            self.send_header("Allow", "GET" if self.path == "/stats" else "POST")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/event":
            self.reply(405 if self.path == "/stats" else 404, {})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) > 1:
            self.reply(400, {})
            return
        if not lengths:
            self.reply(411, {})
            return
        if not lengths[0].isascii() or not lengths[0].isdigit():
            self.reply(400, {})
            return
        try:
            length = int(lengths[0])
        except ValueError:
            self.reply(400, {})
            return
        if length > MAX_REQUEST_BYTES:
            self.reply(413, {})
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self.reply(400, {})
                return
            event = json.loads(body)
            if not valid_event(event):
                self.reply(400, {})
                return
            # Older clients include private error text. Do not retain it.
            event.pop("message", None)
            event.pop("traceback", None)
            line = json.dumps(event, separators=(",", ":")).encode() + b"\n"
        except (ValueError, UnicodeError, RecursionError):
            self.reply(400, {})
            return
        if len(line) > min(MAX_REQUEST_BYTES + 1, MAX_LOG_BYTES):
            self.reply(413, {})
            return
        try:
            append_event(line)
        except OSError:
            self.reply(500, {})
            return
        self.reply(200, {"ok": True})

    def do_GET(self):
        if self.path != "/stats":
            self.reply(405 if self.path == "/event" else 404, {})
            return
        try:
            counts = event_counts()
        except OSError:
            self.reply(500, {})
            return
        self.reply(200, counts)


class Server(HTTPServer):
    def handle_error(self, request, client_address):
        # The default handler prints the client's address and a raw traceback.
        print("Telemetry request failed", file=sys.stderr)


if __name__ == "__main__":
    Server(("", int(os.environ.get("PORT", 8000))), Handler).serve_forever()
