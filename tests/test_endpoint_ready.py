"""Exercise real HTTP and child-process deadlines without external services."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import signal
import sys
import threading
import time
import unittest
from unittest import mock

from immortal.core import ready


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.server.requests += 1
        if self.path == '/hang':
            self.server.release.wait(3)
        self.send_response(self.server.status)
        if self.server.status == 302:
            self.send_header('Location', '/login')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        with contextlib.suppress(BrokenPipeError):
            self.wfile.write(self.server.body)

    def log_message(self, *args):
        pass


class EndpointReadyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(ready, '_endpoints', {}))
        self.enterContext(mock.patch.object(ready, 'log'))
        self.addCleanup(ready.close_endpoints)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.status = 401
        self.server.requests = 0
        self.server.body = b'Success'
        self.server.release = threading.Event()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def settled(self, url):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = ready.check_endpoint(url)
            if result != 'unknown':
                return result
            time.sleep(.01)
        self.fail('Endpoint did not settle within the test deadline')

    def test_auth_and_method_errors_demonstrate_contact_without_credentials(self):
        for status in (200, 204, 400, 401, 404, 405):
            with self.subTest(status=status):
                self.server.status = status
                self.assertEqual(ready.probe_endpoint(self.url), ('reachable', f'http_{status}'))

    def test_overload_and_server_errors_are_not_ready(self):
        for status in (429, 500, 502, 503):
            with self.subTest(status=status):
                self.server.status = status
                self.assertEqual(ready.probe_endpoint(self.url), ('unreachable', f'http_{status}'))

    def test_redirect_is_not_followed_and_forbidden_is_unknown(self):
        for status in (302, 403):
            self.server.status = status
            before = self.server.requests
            self.assertEqual(ready.probe_endpoint(self.url), ('unknown', f'http_{status}'))
            self.assertEqual(self.server.requests, before + 1)

    def test_tls_failure_is_not_readiness(self):
        self.assertEqual(ready.probe_endpoint(self.url.replace('http:', 'https:'))[0], 'unreachable')

    def test_invalid_endpoints_never_make_requests(self):
        for url in (None, '', 'https://user:secret@example.test', 'https://example.test?key=secret',
                    'https://example.test:bad', 'http://example.test', 'https://example.test:\n'):
            with self.subTest(url=url):
                self.assertEqual(ready.check_endpoint(url), 'unknown')
        self.assertEqual(self.server.requests, 0)

    def test_child_result_is_cached_and_fresh_probe_detects_failure(self):
        self.assertEqual(self.settled(self.url), 'reachable')
        for _ in range(5):
            self.assertEqual(ready.check_endpoint(self.url), 'reachable')
        self.assertEqual(self.server.requests, 1)
        self.server.status = 503
        with mock.patch.object(ready, 'ENDPOINT_CACHE_SECS', .01):
            time.sleep(.02)
            self.assertEqual(ready.check_endpoint(self.url), 'unknown')
            self.assertEqual(self.settled(self.url), 'unreachable')

    def test_hanging_endpoint_does_not_block_another_and_is_killed(self):
        slow, fast = self.url + '/hang', self.url + '/fast'
        with mock.patch.object(ready, 'PROBE_TIMEOUT_SECS', .3):
            start = time.monotonic()
            self.assertEqual(ready.check_endpoint(slow), 'unknown')
            self.assertLess(time.monotonic() - start, .2)
            process = ready._endpoints[slow]['process']
            self.assertEqual(self.settled(fast), 'reachable')
            self.assertEqual(self.settled(slow), 'unreachable')
            self.assertIsNotNone(process.poll())

    def test_repeated_pending_checks_do_not_spawn_duplicate_children(self):
        url = self.url + '/hang'
        with mock.patch.object(ready.subprocess, 'Popen', wraps=subprocess.Popen) as spawn:
            for _ in range(5):
                self.assertEqual(ready.check_endpoint(url), 'unknown')
            self.assertEqual(spawn.call_count, 1)

    def test_maintenance_reaps_probe_when_no_thread_checks_it_again(self):
        url = self.url + '/hang'
        with mock.patch.object(ready, 'PROBE_TIMEOUT_SECS', .1):
            ready.check_endpoint(url)
            process = ready._endpoints[url]['process']
            time.sleep(.15)
            ready.maintain_endpoints()
            process.wait(timeout=1)
            ready.maintain_endpoints()
        self.assertIsNone(ready._endpoints[url]['process'])
        self.assertEqual(ready._endpoints[url]['status'], 'unreachable')

    def test_maintenance_result_survives_until_next_bb_scan(self):
        self.assertEqual(self.settled(self.url), 'reachable')
        now = time.monotonic()
        with mock.patch.object(ready.time, 'monotonic', return_value=now + 30):
            ready.maintain_endpoints()
            self.assertEqual(ready.check_endpoint(self.url), 'reachable')
        self.assertEqual(self.server.requests, 1)

    def test_dns_worker_expires_without_parent_polling(self):
        code = (
            'import socket,time,runpy,sys; '
            'socket.getaddrinfo=lambda *a,**k: time.sleep(60); '
            'sys.argv=["ready", "--endpoint", "https://endpoint.invalid"]; '
            'runpy.run_module("immortal.core.ready",run_name="__main__")'
        )
        start = time.monotonic()
        process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            self.assertEqual(process.wait(timeout=18), -signal.SIGALRM)
            self.assertLess(time.monotonic() - start, 18)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=1)

    def test_missing_python_returns_unknown_without_raising(self):
        with mock.patch.object(ready.subprocess, 'Popen', side_effect=OSError('unavailable')):
            self.assertEqual(ready.check_endpoint(self.url), 'unknown')
            self.assertEqual(ready.check_endpoint(self.url), 'unknown')

    def test_apple_success_and_portal_body_through_real_child(self):
        self.assertTrue(ready.check_internet(self.url)['online'])
        self.server.body = b'<html>Sign in to Wi-Fi</html>'
        self.assertFalse(ready.check_internet(self.url)['online'])

    def test_apple_dns_or_http_hang_has_total_process_deadline(self):
        run = subprocess.run
        processes = []

        def stalled_probe(command, **kwargs):
            # Substitute the stuck DNS worker, retaining the real timeout/kill.
            def record(*args, **options):
                process = popen(*args, **options)
                processes.append(process)
                return process
            popen = subprocess.Popen
            with mock.patch.object(subprocess, 'Popen', side_effect=record):
                return run([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)

        with mock.patch.object(ready.subprocess, 'run', side_effect=stalled_probe), \
                mock.patch.object(ready, 'INTERNET_TIMEOUT_SECS', .15):
            start = time.monotonic()
            self.assertFalse(ready.check_internet(self.url)['online'])
            self.assertLess(time.monotonic() - start, 1)
        self.assertTrue(processes)
        self.assertIsNotNone(processes[0].poll())


if __name__ == '__main__':
    unittest.main()
