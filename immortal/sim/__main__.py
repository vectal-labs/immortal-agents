#!/usr/bin/env python3
"""Control the watcher's simulated-outage flag."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from immortal.core.common import STATE_DIR, now_iso

FLAG_PATH = STATE_DIR / "simulated_outage.json"
PROXY_DEAD_PATH = STATE_DIR / "proxy_dead.json"


def write_json(path: Path, value: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temp_path, path)


def turn_on(minutes: float) -> None:
    now = datetime.now(timezone.utc)
    expires_at = now_iso(now + timedelta(minutes=minutes))
    flag = {
        "offline": True,
        "since": now_iso(now),
        "expires_at": expires_at,
    }
    proxy_dead = {"since": now_iso(now), "expires_at": expires_at}
    # Proxies go dead before the watcher starts timing the simulated outage.
    write_json(PROXY_DEAD_PATH, proxy_dead)
    write_json(FLAG_PATH, flag)
    print(json.dumps(flag))


def turn_off() -> None:
    # Proxies revive before the watcher can observe simulated recovery.
    PROXY_DEAD_PATH.unlink(missing_ok=True)
    FLAG_PATH.unlink(missing_ok=True)
    print("off")


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return {"invalid": True}


def active_proxy_state():
    state = read_json(PROXY_DEAD_PATH)
    if not isinstance(state, dict) or "expires_at" not in state:
        return state
    try:
        expires_at = datetime.fromisoformat(state["expires_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return {"invalid": True}
    if expires_at <= datetime.now(timezone.utc):
        PROXY_DEAD_PATH.unlink(missing_ok=True)
        return None
    return state


def status() -> None:
    watcher = read_json(FLAG_PATH)
    proxies = active_proxy_state()
    print(
        json.dumps(
            {
                "watcher": watcher if watcher is not None else "off",
                "proxies": proxies if proxies is not None else "alive",
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    on_parser = commands.add_parser("on", help="simulate an outage")
    on_parser.add_argument("--minutes", type=float, default=10)
    commands.add_parser("off", help="end the simulated outage")
    commands.add_parser("status", help="print the current flag")
    args = parser.parse_args()

    if args.command == "on":
        if args.minutes <= 0:
            parser.error("--minutes must be greater than zero")
        turn_on(args.minutes)
    elif args.command == "off":
        turn_off()
    else:
        status()


if __name__ == "__main__":
    main()
