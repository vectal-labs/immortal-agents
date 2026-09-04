#!/usr/bin/env python3
"""Exercise optional diagnostics through a local HTTP receiver."""
import json, tempfile, threading, unittest, urllib.request
from pathlib import Path
from unittest import mock

from immortal.core import telemetry
from ops.telemetry import server
import revive


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.events = self.root / "events.jsonl"
        patches = (
            mock.patch.object(server, "EVENTS", str(self.events)),
            mock.patch.object(telemetry, "FLAG", self.root / "telemetry"),
            mock.patch.object(telemetry, "MACHINE_ID", self.root / "machine_id"),
            mock.patch.object(telemetry, "STAMP", self.root / "heartbeat"),
            mock.patch.object(telemetry, "TESTING", False),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        worker = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(worker.join, 2)
        self.addCleanup(self.httpd.shutdown)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        patcher = mock.patch.object(telemetry, "TELEMETRY_URL", f"{self.url}/event")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(telemetry, "_post", wraps=telemetry._post)
        self.post = patcher.start()
        self.addCleanup(patcher.stop)
        telemetry.FLAG.write_text("on\n")

    def received(self):
        rows = [json.loads(line) for line in self.events.read_text().splitlines()]
        # Check the outgoing payload too: server filtering must not hide a client leak.
        self.assertEqual(rows, [call.args[0] for call in self.post.call_args_list])
        return rows

    def test_flag_off_sends_nothing(self):
        telemetry.FLAG.unlink()
        with mock.patch.object(telemetry, "_post") as post:
            self.assertIsNone(telemetry.send("install"))
            telemetry.FLAG.write_text("off\n")
            self.assertIsNone(telemetry.send("install"))
            telemetry.heartbeat()
            self.assertIsNone(telemetry.exception(ValueError("private")))
        post.assert_not_called()
        self.assertFalse(self.events.exists())
        self.assertFalse(telemetry.MACHINE_ID.exists())
        self.assertFalse(telemetry.STAMP.exists())

    def test_crash_sends_only_type_and_project_location(self):
        private = "private-repo /Users/person/customer.py /tmp/client-key prompt-secret"
        try:
            revive._offline_duration({}, (None, None), private)
        except ValueError as exc:
            telemetry.exception(exc).join(2)
        body = self.received()[0]
        self.assertEqual(set(body), {"event", "machine_id", "macos", "python", "commit", "ts",
                                     "type", "file", "line"})
        self.assertEqual((body["event"], body["type"], body["file"]),
                         ("exception", "ValueError", "revive.py"))
        self.assertGreater(body["line"], 0)
        self.assertNotIn(str(telemetry.SOURCE_ROOT), json.dumps(body))
        for secret in private.split():
            self.assertNotIn(secret, json.dumps(body))

    def test_external_paths_and_chained_errors_are_omitted(self):
        namespace = {}
        exec(compile("def fail():\n    raise ValueError('secret-token')\n",
                     "/private/customer-repo/private-file.py", "exec"), namespace)
        try:
            try:
                namespace["fail"]()
            except ValueError as cause:
                raise RuntimeError("private-prompt") from cause
        except RuntimeError as exc:
            telemetry.exception(exc).join(2)
        body = self.received()[0]
        self.assertEqual((body["type"], body["file"], body["line"]), ("RuntimeError", None, None))
        for secret in ("secret-token", "private-prompt", "customer-repo", "private-file"):
            self.assertNotIn(secret, json.dumps(body))

    def test_external_error_keeps_only_the_project_call_site(self):
        class PrivateValue:
            def timestamp(self):
                return self

            def __add__(self, _other):
                raise ValueError("private-customer-path")

        from immortal.core.common import iso_after
        try:
            iso_after(PrivateValue(), 1)
        except ValueError as exc:
            telemetry.exception(exc).join(2)
        body = self.received()[0]
        self.assertEqual(body["file"], "immortal/core/common.py")
        self.assertGreater(body["line"], 0)
        self.assertNotIn("private-customer-path", json.dumps(body))

    def test_exception_without_traceback_does_not_format_the_message(self):
        class PrivateError(Exception):
            def __str__(self):
                raise AssertionError("must not read error text")

        telemetry.exception(PrivateError()).join(2)
        body = self.received()[0]
        self.assertEqual((body["type"], body["file"], body["line"]), ("PrivateError", None, None))

    def test_metadata_is_limited_and_machine_id_is_reused(self):
        for event in ("install", "heartbeat"):
            telemetry.send(event).join(2)
        first, second = self.received()
        self.assertEqual(set(first), {"event", "machine_id", "macos", "python", "commit", "ts"})
        self.assertRegex(first["machine_id"], r"^[a-f0-9]{32}$")
        self.assertEqual(first["machine_id"], second["machine_id"])

    def test_server_appends_and_counts(self):
        for event in ("install", "install", "heartbeat"):
            telemetry.send(event).join(2)
        self.assertEqual(len(self.received()), 3)
        with urllib.request.urlopen(f"{self.url}/stats") as response:
            self.assertEqual(json.load(response), {"install": 2, "heartbeat": 1})


if __name__ == "__main__":
    unittest.main()
