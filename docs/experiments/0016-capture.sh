#!/bin/bash
# Experiment 0016: Claude Code, Codex, and Pi in cmux, real Wi-Fi cut.
# Every 20s snapshot the probe, watcher state/log, and all four panes.
# Usage: bash experiments/0016-capture.sh <minutes> <claude> <codex> <pi> <control>
MIN=${1:-40}; CLA=${2:?claude surface}; COD=${3:?codex surface}; PI=${4:?pi surface}; CTL=${5:?control surface}
OUT=experiments/0016-captures; mkdir -p "$OUT"; LOG="$OUT/snapshots.log"
STATE="$HOME/.immortal-agents"
C=/Applications/cmux.app/Contents/Resources/bin/cmux; export CMUX_QUIET=1
END=$(( $(date +%s) + MIN*60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  {
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) probe=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' http://captive.apple.com/hotspot-detect.html 2>/dev/null || echo 000)"
    echo "--- watcher: $(python3 -c "import json;d=json.load(open('$STATE/state.json'));print('online',d.get('online'),'loss',d.get('last_loss_at'),'recovery',d.get('last_recovery_at'),'revived',list((d.get('revived') or {}).keys())[-4:])" 2>&1)"
    echo "--- watcher log (non-probe tail 12)"; grep -v '"event": "probe"' "$STATE/watcher.log" | tail -12 | cut -c1-280
    for pair in "claude-target:$CLA" "codex-target:$COD" "pi-target:$PI" "claude-control:$CTL"; do
      echo "--- ${pair%%:*} ${pair#*:} (tail 20)"; "$C" read-screen --surface "${pair#*:}" 2>&1 | grep -v '^\s*$' | tail -20
    done
  } >> "$LOG" 2>&1
  sleep 20
done
echo "capture done $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"
