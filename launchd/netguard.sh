#!/bin/bash
# netguard: safety net after Incident 0001. Runs every 2 minutes via launchd.
# 1. Any DISABLED network service is re-enabled. Nobody legitimately disables
#    Wi-Fi/USB/Tailscale services; a disabled one means a script broke.
# 2. Wi-Fi radio is turned back on only when an outage test left it off:
#    cut-armed.json exists and its deadline has passed. A radio the operator
#    turned off on purpose (plane) is never touched.
# NETGUARD_DRY_RUN=1 prints actions without doing them.
set -u
export PATH="/usr/sbin:/usr/bin:/bin:/opt/homebrew/bin:/Applications/Tailscale.app/Contents/MacOS:$PATH"
STATE_DIR="${WATCHER_STATE_DIR:-$HOME/.immortal-agents}"
ARMED="$STATE_DIR/cut-armed.json"
LOG="$STATE_DIR/netguard.log"
WIFI_DEV="${WIFI_DEV:-en0}"
DRY="${NETGUARD_DRY_RUN:-}"
mkdir -p "$STATE_DIR"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; [ -n "$DRY" ] && echo "$*"; }
run() { if [ -n "$DRY" ]; then echo "DRY: $*"; else "$@"; fi; }

# 1. Re-enable disabled services (listed with a leading asterisk).
networksetup -listallnetworkservices 2>/dev/null | tail -n +2 | while IFS= read -r line; do
  case "$line" in
    "*"*)
      svc="${line#\*}"
      log "FIX service '$svc' was Disabled -> on"
      run networksetup -setnetworkserviceenabled "$svc" on || log "WARN could not enable '$svc'"
      ;;
  esac
done

# 2. Radio: only after an armed cut whose deadline passed.
if [ -f "$ARMED" ]; then
  deadline="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("deadline",""))' "$ARMED" 2>/dev/null || true)"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ -n "$deadline" ] && [[ "$now" > "$deadline" ]]; then
    power="$(networksetup -getairportpower "$WIFI_DEV" 2>/dev/null | sed 's/.*: //')"
    log "STALE cut-armed deadline=$deadline airport=$power"
    if [ "$power" = "Off" ]; then
      log "FIX airport power -> on"
      run networksetup -setairportpower "$WIFI_DEV" on || log "WARN airport on failed"
    fi
    if command -v tailscale >/dev/null 2>&1; then
      log "FIX tailscale up"
      if [ -z "$DRY" ]; then tailscale up >/dev/null 2>&1 || log "WARN tailscale up failed"; fi
    fi
    run rm -f "$ARMED"
  fi
fi
