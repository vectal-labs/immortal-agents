#!/bin/bash
# One-time restore timer (ADR 0037 precondition 3). Run it in a process that
# outlives the agent. After N minutes it turns Wi-Fi radio on and Tailscale up,
# once, then exits. It never turns anything off.
# usage: restore-timer.sh <minutes>
set -u
export PATH="/usr/sbin:/usr/bin:/bin:/opt/homebrew/bin:/Applications/Tailscale.app/Contents/MacOS:$PATH"
MINUTES="${1:?minutes required}"
STATE_DIR="${WATCHER_STATE_DIR:-$HOME/.immortal-agents}"
TIMER="$STATE_DIR/restore-timer.json"
WIFI_DEV="${WIFI_DEV:-en0}"
mkdir -p "$STATE_DIR"
fire_at="$(python3 -c "import datetime as d; print((d.datetime.now(d.timezone.utc)+d.timedelta(minutes=$MINUTES)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
printf '{"pid":%s,"fire_at":"%s","minutes":%s}\n' "$$" "$fire_at" "$MINUTES" > "$TIMER"
echo "restore timer armed: fires at $fire_at (pid $$)"
sleep "$((MINUTES * 60))"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) TIMER fired: airport on, tailscale up"
networksetup -setairportpower "$WIFI_DEV" on
command -v tailscale >/dev/null 2>&1 && tailscale up
rm -f "$TIMER"
echo "done: airport=$(networksetup -getairportpower "$WIFI_DEV")"
