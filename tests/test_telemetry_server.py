"""Local HTTP behavior for the bounded telemetry sink."""
import http.client
import contextlib
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest import mock

from ops.telemetry import server


class TelemetryServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.events = Path(temporary.name, "events.jsonl")
        patcher = mock.patch.object(server, "EVENTS", str(self.events))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        worker = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(worker.join, 2)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=2)
        try:
            connection.request(method, path, body)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def post(self, event):
        return self.request("POST", "/event", json.dumps(event).encode())

    def test_valid_fields_survive_and_events_are_counted(self):
        event = {"event": "revive_outcome", "result": "success", "harness": "codex", "count": 1}
        self.assertEqual(self.post(event), (200, {"ok": True}))
        self.assertEqual(json.loads(self.events.read_text()), event)
        self.assertEqual(self.request("GET", "/stats"), (200, {"revive_outcome": 1}))

    def test_legacy_crash_text_is_not_stored(self):
        event = {"event": "exception", "type": "ValueError", "message": "secret-token",
                 "traceback": "/Users/person/private-repo/secret.py"}
        self.assertEqual(self.post(event)[0], 200)
        self.assertEqual(json.loads(self.events.read_text()), {"event": "exception", "type": "ValueError"})

    def test_requests_and_http_errors_do_not_log_client_details(self):
        logs = io.StringIO()
        with contextlib.redirect_stderr(logs):
            self.assertEqual(self.post({"event": "heartbeat"})[0], 200)
            self.assertEqual(self.request("POST", "/event", b"private-bad-json")[0], 400)
            self.assertEqual(self.request("GET", "/private-path?token=secret")[0], 404)
            with socket.create_connection(self.httpd.server_address, timeout=2) as connection:
                connection.sendall(b"PRIVATE_METHOD /private-path HTTP/1.0\r\nX-Forwarded-For: 192.0.2.1\r\n\r\n")
                while connection.recv(4096):
                    pass
            self.assertEqual(self.request("GET", "/stats")[0], 200)
        self.assertEqual(logs.getvalue(), "")

    def test_unhandled_error_logs_no_ip_or_raw_exception(self):
        logs = io.StringIO()
        with contextlib.redirect_stderr(logs):
            with mock.patch.object(server, "event_counts", side_effect=RuntimeError("private-error")):
                with self.assertRaises(http.client.RemoteDisconnected):
                    self.request("GET", "/stats")
            self.assertEqual(self.request("GET", "/stats"), (200, {}))
        self.assertEqual(logs.getvalue(), "Telemetry request failed\n")

    def test_invalid_events_do_not_poison_stats(self):
        self.post({"event": "heartbeat"})
        for event in ({"event": []}, {"event": {}}, {"event": None}, {"event": ""},
                      {"event": "  "}, {"event": "x" * 129}, {}, [], None):
            with self.subTest(event=event):
                self.assertEqual(self.post(event)[0], 400)
        self.assertEqual(self.request("GET", "/stats"), (200, {"heartbeat": 1}))
        self.assertEqual(len(self.events.read_text().splitlines()), 1)

    def test_corrupt_stored_lines_are_skipped(self):
        self.events.write_bytes(b'{"event":"install"}\nnot json\n{"event":[]}\n[]\n'
                                b'{"other":1}\n\xff\n{"event":"heartbeat"}\n')
        self.assertEqual(self.request("GET", "/stats"), (200, {"install": 1, "heartbeat": 1}))

    def test_oversized_and_deep_stored_lines_are_skipped(self):
        self.events.write_bytes(b'x' * (40 * 1024) + b'\n' + b'[' * 2000 + b']' * 2000
                                + b'\n{"event":"heartbeat"}\n')
        self.assertEqual(self.request("GET", "/stats"), (200, {"heartbeat": 1}))

    def test_append_after_partial_line_preserves_new_event(self):
        self.events.write_bytes(b'{"event":')
        self.assertEqual(self.post({"event": "heartbeat"})[0], 200)
        self.assertEqual(self.request("GET", "/stats"), (200, {"heartbeat": 1}))

    def test_oversized_request_is_rejected_before_reading_body(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=1)
        try:
            connection.putrequest("POST", "/event")
            connection.putheader("Content-Length", str(16 * 1024 + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()
        self.assertFalse(self.events.exists())

    def test_bad_json_is_rejected(self):
        self.assertEqual(self.request("POST", "/event", b'{"event":')[0], 400)
        self.assertFalse(self.events.exists())

    def test_request_framing_is_checked(self):
        cases = [([], 411), ([("Content-Length", "-1")], 400),
                 ([("Content-Length", "invalid")], 400),
                 ([("Content-Length", "0"), ("Content-Length", "1")], 400),
                 ([("Transfer-Encoding", "chunked")], 400)]
        for headers, status in cases:
            with self.subTest(headers=headers):
                connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=2)
                try:
                    connection.putrequest("POST", "/event")
                    for name, value in headers:
                        connection.putheader(name, value)
                    connection.endheaders()
                    response = connection.getresponse()
                    self.assertEqual(response.status, status)
                    response.read()
                finally:
                    connection.close()
        self.assertFalse(self.events.exists())

    def test_truncated_body_is_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=2)
        try:
            connection.request("POST", "/event", b'{"event":"heartbeat"}', {"Content-Length": "100"})
            connection.sock.shutdown(socket.SHUT_WR)
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            connection.close()
        self.assertFalse(self.events.exists())

    def test_stalled_request_does_not_hold_the_server_forever(self):
        with mock.patch.object(server.Handler, "timeout", 0.05):
            with socket.create_connection(self.httpd.server_address, timeout=2) as connection:
                connection.sendall(b"POST /event HTTP/1.0\r\nContent-Length: 20\r\n\r\n{")
                self.assertEqual(connection.recv(1024), b"")
            self.assertEqual(self.request("GET", "/stats"), (200, {}))

    def test_paths_and_methods_are_checked(self):
        self.assertEqual(self.request("GET", "/stats"), (200, {}))
        self.assertEqual(self.request("GET", "/missing")[0], 404)
        self.assertEqual(self.request("POST", "/missing", b"{}")[0], 404)
        self.assertEqual(self.request("GET", "/event")[0], 405)
        self.assertEqual(self.request("POST", "/stats", b"{}")[0], 405)

    def test_storage_rotates_one_backup_and_stats_cover_retained_events(self):
        with mock.patch.object(server, "MAX_LOG_BYTES", 100):
            for index in range(30):
                self.assertEqual(self.post({"event": f"event_{index}"})[0], 200)
                self.assertLessEqual(self.events.stat().st_size, 100)
                backup = Path(str(self.events) + ".1")
                if backup.exists():
                    self.assertLessEqual(backup.stat().st_size, 100)
            files = list(self.events.parent.iterdir())
            self.assertEqual({path.name for path in files}, {"events.jsonl", "events.jsonl.1"})
            retained = [json.loads(line)["event"] for path in files for line in path.read_text().splitlines()]
            self.assertEqual(self.request("GET", "/stats"), (200, {event: 1 for event in retained}))
            self.assertNotIn("event_0", retained)
            self.assertIn("event_29", retained)

    def test_preexisting_oversized_log_is_bounded_on_next_write(self):
        self.events.write_bytes(b'{"event":"install"}\n' * 20)
        with mock.patch.object(server, "MAX_LOG_BYTES", 100):
            self.assertEqual(self.post({"event": "heartbeat"})[0], 200)
            self.assertLessEqual(self.events.stat().st_size, 100)
            self.assertLessEqual(Path(str(self.events) + ".1").stat().st_size, 100)
            status, counts = self.request("GET", "/stats")
            self.assertEqual(status, 200)
            self.assertEqual(counts["heartbeat"], 1)
            self.assertGreater(counts["install"], 0)

    def test_serialized_record_must_fit_storage_limit(self):
        with mock.patch.object(server, "MAX_LOG_BYTES", 100):
            self.assertEqual(self.post({"event": "heartbeat", "data": "x" * 100})[0], 413)
            self.assertFalse(self.events.exists())

    def test_storage_errors_return_server_error(self):
        self.events.mkdir()
        self.assertEqual(self.post({"event": "heartbeat"})[0], 500)
        self.assertEqual(self.request("GET", "/stats")[0], 500)


if __name__ == "__main__":
    unittest.main()
