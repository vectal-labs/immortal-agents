"""Nonblocking checks through the same system DNS resolver used by agents."""
from __future__ import annotations

import atexit
import socket
import subprocess
import sys
import time
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

if __name__ == '__main__':
    raise SystemExit(1 if unresolved() else 0)
