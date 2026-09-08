"""Nonblocking checks through the same system DNS resolver used by agents."""
from __future__ import annotations

import atexit
import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from immortal.core.logbook import log

API_HOSTS = ('api.anthropic.com', 'api.openai.com')
PROBE_TIMEOUT_SECS = 15
PROBE_CACHE_SECS = 10
PROBE_CLEANUP_TIMEOUT_SECS = 0.1
_process = None
_started = 0.0
_checked = None
_result = False
_discard_result = False

# BB checks only the endpoint belonging to the failed thread. Legacy terminal
# recovery retains check() until those adapters can identify their endpoints.
_endpoints = {}
MAX_ENDPOINT_PROBES = 8
INTERNET_TIMEOUT_SECS = 6
# A result collected by a 10-second maintenance tick must still be available
# at the next 30-second BB scan, or every scan would only start another probe.
ENDPOINT_CACHE_SECS = 45


def _internet_probe(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read(256).decode('utf-8', 'replace')
            return dict(online=response.status == 200 and 'Success' in body,
                        http_status=response.status, body=body[:80])
    except (OSError, urllib.error.URLError):
        return dict(online=False, reason='connection_failed')


def check_internet(url):
    """Bound Apple's DNS and HTTP together so they cannot stall BB recovery."""
    try:
        result = subprocess.run([sys.executable, '-m', 'immortal.core.ready', '--internet', url],
            cwd=Path(__file__).resolve().parents[2], capture_output=True,
            timeout=INTERNET_TIMEOUT_SECS, check=True)
        data = json.loads(result.stdout)
        if isinstance(data, dict) and type(data.get('online')) is bool:
            return data
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return dict(online=False, reason='probe_failed_or_timed_out')


def safe_endpoint(value):
    """Reject credentials/queries; permit HTTP only for explicit loopback APIs."""
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value):
        return None
    try:
        url = urllib.parse.urlsplit(value)
        if (not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment
                or url.scheme not in ('https', 'http') or url.netloc.endswith(':')):
            return None
        url.port
        if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
            return None
        return urllib.parse.urlunsplit((url.scheme, url.netloc, url.path or '/', '', ''))
    except ValueError:
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_endpoint(endpoint):
    """Connectivity evidence only: never authenticate or request model output."""
    endpoint = safe_endpoint(endpoint)
    if not endpoint:
        return 'unknown', 'invalid_endpoint'
    try:
        # The parent imposes a total deadline, including the system DNS lookup.
        opener = urllib.request.build_opener(_NoRedirect)
        request = urllib.request.Request(endpoint, method='HEAD')
        with opener.open(request, timeout=5) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
    except (OSError, urllib.error.URLError):
        return 'unreachable', 'connection_failed'
    # These unauthenticated responses establish contact. A 403 may be an edge
    # challenge and a redirect may be a login portal; neither establishes it.
    if 200 <= code < 300 or code in (400, 401, 404, 405):
        return 'reachable', f'http_{code}'
    if code == 429 or code >= 500:
        return 'unreachable', f'http_{code}'
    return 'unknown', f'http_{code}'


def _collect_endpoints(now):
    for endpoint, entry in list(_endpoints.items()):
        process = entry.get('process')
        if process is not None:
            code = process.poll()
            if code is None and now - entry['started'] >= PROBE_TIMEOUT_SECS:
                entry['expired'] = True
                try:
                    process.kill()
                except OSError:
                    pass
                code = process.poll()
            if code is None:
                continue
            result = process.stdout.read(256).decode('ascii', 'replace').strip().split(':', 1)
            process.stdout.close()
            status, reason = result if len(result) == 2 else ('unknown', 'probe_failed')
            expired = entry.get('expired') or code == -signal.SIGALRM
            if expired:
                status, reason = 'unreachable', 'probe_timeout'
            if status not in ('reachable', 'unreachable', 'unknown') or code != 0 and not expired:
                status, reason = 'unknown', 'probe_failed'
            entry.update(process=None, checked=now, status=status)
            log('endpoint_probe', host=urllib.parse.urlsplit(endpoint).hostname,
                status=status, reason=reason)
        if now - entry.get('used', now) > 300 and entry.get('process') is None:
            del _endpoints[endpoint]


def maintain_endpoints():
    """Reap probes even after their failed thread has disappeared or resumed."""
    _collect_endpoints(time.monotonic())


def check_endpoint(endpoint):
    """Nonblocking, cached result for one endpoint; unknown includes pending."""
    endpoint = safe_endpoint(endpoint)
    if not endpoint:
        return 'unknown'
    now = time.monotonic()
    _collect_endpoints(now)
    entry = _endpoints.get(endpoint)
    if entry:
        entry['used'] = now
        if entry.get('process') is not None:
            return 'unknown'
        if now - entry['checked'] < ENDPOINT_CACHE_SECS:
            return entry['status']
    if sum(e.get('process') is not None for e in _endpoints.values()) >= MAX_ENDPOINT_PROBES:
        return 'unknown'
    try:
        process = subprocess.Popen(
            [sys.executable, '-m', 'immortal.core.ready', '--endpoint', endpoint],
            cwd=Path(__file__).resolve().parents[2], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        _endpoints[endpoint] = dict(used=now, checked=now, status='unknown')
        return 'unknown'
    _endpoints[endpoint] = dict(process=process, started=now, used=now)
    return 'unknown'


def close_endpoints():
    for entry in _endpoints.values():
        process = entry.get('process')
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=PROBE_CLEANUP_TIMEOUT_SECS)
            except (OSError, subprocess.TimeoutExpired):
                pass
            process.stdout.close()
    _endpoints.clear()


def resolves(host):
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def unresolved(hosts=API_HOSTS):
    return [host for host in hosts if not resolves(host)]


def _cancel_probe(wait=False):
    global _process
    if _process is None:
        return
    try:
        if _process.poll() is None:
            _process.kill()
        if wait:
            _process.wait(timeout=PROBE_CLEANUP_TIMEOUT_SECS)
        if _process.poll() is not None:
            _process = None
    except (OSError, subprocess.TimeoutExpired):
        # Keep ownership until reaped; do not accumulate children after a timeout.
        pass


def invalidate():
    global _checked, _result, _discard_result
    _checked = None
    _result = False
    _discard_result = True
    _cancel_probe(wait=True)


def check():
    """True only for a recent successful probe; pending and failures are False.

    A subprocess bounds getaddrinfo, which has no Python timeout. A stuck
    resolver cannot block the watcher or accumulate abandoned worker threads.
    """
    global _process, _started, _checked, _result, _discard_result
    now = time.monotonic()
    if _process is not None:
        if _discard_result:
            _cancel_probe()
            if _process is not None:
                return False
        else:
            code = _process.poll()
            if code is None and now - _started < PROBE_TIMEOUT_SECS:
                return False
            if code is None:
                _discard_result = True
                _cancel_probe()
            else:
                _process = None
            _checked, _result = now, code == 0
            log('apis_ready' if _result else 'apis_not_ready', timed_out=code is None)
            return _result
    if _checked is not None and now - _checked < PROBE_CACHE_SECS:
        return _result
    # Launch with the current interpreter, never a PATH-dependent Python.
    try:
        _process = subprocess.Popen([sys.executable, '-m', 'immortal.core.ready', '--probe'],
            cwd=Path(__file__).resolve().parents[2], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except OSError:
        _checked, _result = now, False
        log('apis_not_ready', probe_start_failed=True)
        return False
    _started = now
    _discard_result = False
    return False


atexit.register(invalidate)
atexit.register(close_endpoints)

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--internet':
        print(json.dumps(_internet_probe(sys.argv[2])))
        raise SystemExit(0)
    if len(sys.argv) == 3 and sys.argv[1] == '--endpoint':
        # DNS can ignore socket timeouts. The child must expire even if the
        # watcher is busy or no longer has a failed thread to check.
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.setitimer(signal.ITIMER_REAL, PROBE_TIMEOUT_SECS)
        print(':'.join(probe_endpoint(sys.argv[2])))
        raise SystemExit(0)
    raise SystemExit(1 if unresolved() else 0)
