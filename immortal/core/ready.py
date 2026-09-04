"""Wait until the agents' API hosts resolve through the Mac's own resolver.

Experiment 0009: after a real Wi-Fi cut the captive probe passed at once, but
macOS kept a negative DNS entry for api.anthropic.com for minutes (the agent
had hammered it while offline). A revive sent into that window dies again
with ENOTFOUND. So "internet is back" is gated on "the APIs resolve"."""

from __future__ import annotations

import socket
import time

from immortal.core.logbook import log

API_HOSTS = ("api.anthropic.com", "api.openai.com")
MAX_WAIT_SECS = 600
STEP_SECS = 10


def resolves(host):
    # getaddrinfo goes through mDNSResponder, the same path the agents use.
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def unresolved(hosts=API_HOSTS):
    return [h for h in hosts if not resolves(h)]


def wait_for_apis(max_wait=MAX_WAIT_SECS, step=STEP_SECS, sleep=time.sleep):
    """Block until every API host resolves, or the cap expires. True when ready."""
    started = time.monotonic()
    last_logged = None
    while True:
        missing = unresolved()
        waited = round(time.monotonic() - started)
        if not missing:
            log("apis_ready", waited_secs=waited)
            return True
        if waited >= max_wait:
            log("apis_not_ready", missing=missing, waited_secs=waited)
            return False
        if missing != last_logged:
            log("apis_waiting", missing=missing, waited_secs=waited)
            last_logged = missing
        sleep(step)
