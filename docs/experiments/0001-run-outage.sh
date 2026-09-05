#!/bin/bash
# The operator runs this, in a foreground terminal (ADR 0020/0037). Agents never run it unattended.
# Incident 0001: this script must never leave the Mac offline. So it only
# flips reversible knobs: Wi-Fi radio power and Tailscale. It never touches
# `networksetup -setnetworkserviceenabled` (persistent, greys out Wi-Fi, survives
# reboot). Wired adapters must be unplugged by hand; the leak check enforces it.
set -u
export PATH="/usr/sbin:/usr/bin:/bin:/opt/homebrew/bin:/Applications/Tailscale.app/Contents/MacOS:$PATH"

OUT="$(cd "$(dirname "$0")" && pwd)/outage-runs"
mkdir -p "$OUT"
LOG="$OUT/outage.log"
OUTAGE_SECS="${OUTAGE_SECS:-180}"
PROBE_URL="http://captive.apple.com/hotspot-detect.html"
WIFI_DEV="${WIFI_DEV:-en0}"
STATE_DIR="${WATCHER_STATE_DIR:-$HOME/.offline-agent-restart}"
# The netguard watchdog reads this: once the deadline passes it forces Wi-Fi back on.
ARMED="$STATE_DIR/cut-armed.json"
TAILSCALE_BIN="$(command -v tailscale || true)"
TS_WAS_UP=0
RESTORED=0
WIFI_POWER_BEFORE=""

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"
}

abort() {
  log "ABORT $*"
  echo "ABORT: $*" >&2
  exit 1
}

probe_code() {
  local code
  code="$(curl -sS -m 3 -o /dev/null -w "%{http_code}" "$PROBE_URL" 2>/dev/null || true)"
  printf '%s\n' "${code:-000}"
}

# Hard guards (ADR 0037): never under launchd; netguard must be loaded; an
# independent one-time restore timer must already be armed and alive.
[ "$PPID" != 1 ] || abort "refusing to run under launchd"
case "$(ps -o comm= -p "$PPID" 2>/dev/null)" in
  *launchd*) abort "refusing to run under launchd" ;;
esac
launchctl list 2>/dev/null | grep -q "com.immortal-agents.netguard" \
  || abort "netguard LaunchAgent is not loaded (see docs/launchd.md)"
TIMER="$STATE_DIR/restore-timer.json"
timer_pid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid",""))' "$TIMER" 2>/dev/null || true)"
[ -n "$timer_pid" ] && kill -0 "$timer_pid" 2>/dev/null \
  || abort "no live restore timer; arm one first: launchd/restore-timer.sh <minutes>"

fail_leak() {
  log "LEAK: $*"
  echo "========================================" >&2
  echo "ABORT: internet still reachable. $*" >&2
  echo "Unplug wired adapters / phone; this script only cuts Wi-Fi + Tailscale." >&2
  echo "========================================" >&2
  exit 2
}

restore() {
  [ "$RESTORED" = 1 ] && return 0
  RESTORED=1
  log "RESTORE start"
  networksetup -setairportpower "$WIFI_DEV" on || log "WARN airport on failed"
  if [ -n "$TAILSCALE_BIN" ] && [ "$TS_WAS_UP" = 1 ]; then
    "$TAILSCALE_BIN" up || log "WARN tailscale up failed"
  fi
  rm -f "$ARMED"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/network_restored_at.txt"
  log "RESTORE done airport=$(networksetup -getairportpower "$WIFI_DEV" 2>/dev/null | tr '\n' ' ') probe=$(probe_code)"
}

on_signal() {
  log "ABORT signal received; restoring"
  exit 130
}

trap restore EXIT
trap on_signal INT TERM HUP

log "OUTAGE script start OUTAGE_SECS=$OUTAGE_SECS wifi=$WIFI_DEV (radio + tailscale only)"
WIFI_POWER_BEFORE="$(networksetup -getairportpower "$WIFI_DEV" 2>/dev/null | sed 's/.*: //')"
[ "$WIFI_POWER_BEFORE" = "On" ] || abort "Wi-Fi must be On before a cut (state=$WIFI_POWER_BEFORE)"

if [ -n "$TAILSCALE_BIN" ]; then
  ts_state="$("$TAILSCALE_BIN" status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null || true)"
  [ "$ts_state" = "Running" ] && TS_WAS_UP=1
fi

mkdir -p "$STATE_DIR"
deadline="$(python3 -c "import datetime as d; print((d.datetime.now(d.timezone.utc)+d.timedelta(seconds=$OUTAGE_SECS+120)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
printf '{"armed_at":"%s","deadline":"%s","pid":%s}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$deadline" "$$" > "$ARMED"

log "CUT start"
date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/network_cut_at.txt"
[ "$TS_WAS_UP" = 1 ] && { "$TAILSCALE_BIN" down || log "WARN tailscale down failed"; }
networksetup -setairportpower "$WIFI_DEV" off || log "WARN airport off failed"
sleep 3

code="$(probe_code)"
log "probe after cut http_code=$code"
[ "$code" = "000" ] || fail_leak "first probe after cut returned http_code=$code"

elapsed=0
while [ "$elapsed" -lt "$OUTAGE_SECS" ]; do
  sleep 15
  elapsed=$((elapsed + 15))
  code="$(probe_code)"
  log "probe t=${elapsed}s http_code=$code"
  [ "$code" = "000" ] || fail_leak "probe leaked at t=${elapsed}s http_code=$code"
done

log "OUTAGE window complete (${OUTAGE_SECS}s, probe stayed dead); trap will restore"
