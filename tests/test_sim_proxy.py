#!/usr/bin/env python3
"""Tests for the simulated-outage pass-through proxy."""

from __future__ import annotations

import http.client
import importlib.util
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("sim_proxy", ROOT / "sim" / "proxy.py")
assert SPEC and SPEC.loader
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class DummyUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    first_stream_chunk = threading.Event()
    release_stream = threading.Event()
    received: dict[str, object] = {}

    def log_message(self, format, *args):
        pass

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).received = {
            "path": self.path,
            "body": body,
            "header": self.headers.get("X-Test-Header"),
            "host": self.headers.get("Host"),
        }
        response = b"upstream:" + body
        self.send_response(201)
        self.send_header("Content-Length", str(len(response)))
        self.send_header("X-Upstream-Header", "present")
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"data: first\n\n")
            self.wfile.flush()
            type(self).first_stream_chunk.set()
            type(self).release_stream.wait(timeout=5)
            self.wfile.write(b"data: second\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        body = b"alive"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SimProxyTests(unittest.TestCase):
    def setUp(self):
        DummyUpstreamHandler.first_stream_chunk.clear()
        DummyUpstreamHandler.release_stream.clear()
        DummyUpstreamHandler.received = {}
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dead_path = Path(self.temp_dir.name) / "proxy_dead.json"
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), DummyUpstreamHandler)
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True
        )
        self.upstream_thread.start()

    def tearDown(self):
        DummyUpstreamHandler.release_stream.set()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=2)
        self.temp_dir.cleanup()

    @contextmanager
    def running_proxy(self, dead_mode="502"):
        upstream_url = f"http://127.0.0.1:{self.upstream.server_port}"
        server = proxy.create_server(0, upstream_url, dead_mode, self.dead_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            DummyUpstreamHandler.release_stream.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def set_dead(self, delta: timedelta = timedelta(minutes=2)):
        expires_at = datetime.now(timezone.utc) + delta
        self.dead_path.write_text(json.dumps({"expires_at": iso(expires_at)}))

    def test_forwards_method_path_headers_body_and_response(self):
        with self.running_proxy() as server:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request(
                "PATCH",
                "/v1/items?answer=42",
                body=b"request-body",
                headers={"X-Test-Header": "forwarded"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 201)
            self.assertEqual(response.getheader("X-Upstream-Header"), "present")
            self.assertEqual(response.read(), b"upstream:request-body")
            self.assertEqual(DummyUpstreamHandler.received["path"], "/v1/items?answer=42")
            self.assertEqual(DummyUpstreamHandler.received["body"], b"request-body")
            self.assertEqual(DummyUpstreamHandler.received["header"], "forwarded")
            self.assertEqual(
                DummyUpstreamHandler.received["host"],
                f"127.0.0.1:{self.upstream.server_port}",
            )
            connection.close()

    def test_streams_chunks_without_waiting_for_the_full_response(self):
        with self.running_proxy() as server:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=2
            )
            connection.request("GET", "/stream")
            response = connection.getresponse()
            first = b"data: first\n\n"
            self.assertTrue(DummyUpstreamHandler.first_stream_chunk.wait(timeout=1))
            self.assertEqual(response.read(len(first)), first)
            DummyUpstreamHandler.release_stream.set()
            self.assertEqual(response.read(), b"data: second\n\n")
            connection.close()

    def test_dead_502_returns_exact_codex_fingerprint(self):
        self.set_dead()
        with self.running_proxy("502") as server:
            url = f"http://127.0.0.1:{server.server_port}/v1/responses"
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(url, timeout=2)
            error = raised.exception
            try:
                self.assertEqual(error.code, 502)
                self.assertEqual(error.read(), proxy.DEAD_BODY)
            finally:
                error.close()

    def test_dead_drop_closes_without_a_response(self):
        self.set_dead()
        with self.running_proxy("drop") as server:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=2
            )
            connection.request("GET", "/")
            with self.assertRaises((http.client.RemoteDisconnected, ConnectionResetError)):
                connection.getresponse()
            connection.close()

    def test_expiry_restores_forwarding_and_removes_control_file(self):
        self.set_dead(timedelta(seconds=-1))
        with self.running_proxy() as server:
            url = f"http://127.0.0.1:{server.server_port}/"
            with urllib.request.urlopen(url, timeout=2) as response:
                self.assertEqual(response.read(), b"alive")
            self.assertFalse(self.dead_path.exists())

    def test_expiry_restores_even_if_control_file_cannot_be_deleted(self):
        self.set_dead(timedelta(seconds=-1))
        with mock.patch.object(Path, "unlink", side_effect=PermissionError):
            self.assertIsNone(proxy.read_dead_state(self.dead_path))

    def test_going_dead_closes_an_in_flight_stream(self):
        with self.running_proxy() as server:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=2
            )
            connection.request("GET", "/stream")
            response = connection.getresponse()
            first = b"data: first\n\n"
            self.assertEqual(response.read(len(first)), first)
            self.set_dead()
            deadline = time.monotonic() + 1
            remainder = None
            while time.monotonic() < deadline:
                try:
                    remainder = response.read(1)
                    break
                except TimeoutError:
                    continue
            self.assertEqual(remainder, b"")
            connection.close()


if __name__ == "__main__":
    unittest.main()
