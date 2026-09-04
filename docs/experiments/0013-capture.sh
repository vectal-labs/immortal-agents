#!/bin/bash
# Experiment 0013: Pi in cmux, real Wi-Fi cut. Snapshot the three panes, the
# watcher state, and the newest Pi session JSONL for the workdir every 20s.
# Usage: bash docs/experiments/0013-capture.sh <minutes> <pi-a-surface> <pi-control-surface> <claude-surface>
MIN=${1:-40}; PIA=${2:?pi-a surface}; PIC=${3:?pi-control surface}; CLA=${4:?claude surface}
OUT=docs/experiments/0013-captures; mkdir -p "$OUT"; LOG="$OUT/snapshots.log"
C=/Applications/cmux.app/Contents/Resources/bin/cmux; export CMUX_QUIET=1
WORKDIR="$(cd "$OUT/workdir" && pwd)"
SESS="$HOME/.pi/agent/sessions/--$(echo "${WORKDIR#/}" | tr / -)--"
END=$(( $(date +%s) + MIN*60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  {
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) probe=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' http://captive.apple.com/hotspot-detect.html 2>/dev/null || echo 000)"
    echo "--- watcher: $(python3 -c "import json;d=json.load(open('$HOME/.offline-agent-restart/state.json'));print('online',d.get('online'),'loss',d.get('last_loss_at'),'recovery',d.get('last_recovery_at'),'resumed',list((d.get('resumed') or {}).keys())[-3:])" 2>&1)"
    echo "--- watcher log (non-probe tail 4)"; grep -v '"event": "probe"' "$HOME/.offline-agent-restart/watcher.log" | tail -4 | cut -c1-220
    for pair in "pi-a:$PIA" "pi-control:$PIC" "claude-a:$CLA"; do
      echo "--- ${pair%%:*} ${pair#*:} (tail 12)"; $C read-screen --surface "${pair#*:}" 2>&1 | grep -v '^\s*$' | tail -12
    done
    echo "--- pi sessions in $SESS"
    for f in $(ls -t "$SESS"/*.jsonl 2>/dev/null | head -2); do
      echo "$(basename "$f"): $(tail -1 "$f" | python3 -c "import sys,json;e=json.loads(sys.stdin.read());m=e.get('message',{});print(e.get('timestamp'),e.get('type'),m.get('role',''),m.get('stopReason',''),(m.get('errorMessage') or '')[:60])" 2>&1)"
    done
  } >> "$LOG" 2>&1
  sleep 20
done
echo "capture done $(date -u)" >> "$LOG"
