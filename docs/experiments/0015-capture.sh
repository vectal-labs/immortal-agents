#!/bin/bash
# Experiment 0015: Cursor (acp) in bb, real Wi-Fi cut. Every 20s snapshot the
# probe, the watcher state, the watcher log tail, and the three bb threads'
# status plus last two events.
# Usage: bash experiments/0015-capture.sh <minutes> <target-id> <control-id> <reference-id>
MIN=${1:-40}; TGT=${2:?target thread}; CTL=${3:?control thread}; REF=${4:?reference thread}
OUT=experiments/0015-captures; mkdir -p "$OUT"; LOG="$OUT/snapshots.log"
STATE="$HOME/.immortal-agents"
END=$(( $(date +%s) + MIN*60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  {
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) probe=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' http://captive.apple.com/hotspot-detect.html 2>/dev/null || echo 000)"
    echo "--- watcher: $(python3 -c "import json;d=json.load(open('$STATE/state.json'));print('online',d.get('online'),'loss',d.get('last_loss_at'),'recovery',d.get('last_recovery_at'),'revived',list((d.get('revived') or {}).keys())[-2:])" 2>&1)"
    echo "--- watcher log (non-probe tail 4)"; grep -v '"event": "probe"' "$STATE/watcher.log" | tail -4 | cut -c1-240
    for pair in "target:$TGT" "control:$CTL" "reference:$REF"; do
      id="${pair#*:}"
      echo "--- ${pair%%:*} $id status=$(bb thread list --json 2>/dev/null | python3 -c "import sys,json;print(next((t['status'] for t in json.load(sys.stdin) if t['id']=='$id'),'?'))" 2>&1)"
      bb thread log "$id" --json 2>/dev/null | python3 -c "
import sys,json,datetime
evs=json.load(sys.stdin)[-2:]
for e in evs:
    ts=datetime.datetime.fromtimestamp(e['createdAt']/1000,tz=datetime.timezone.utc).strftime('%H:%M:%SZ')
    print(' ',ts,e['type'],json.dumps(e.get('data',{}))[:120])" 2>&1
    done
  } >> "$LOG" 2>&1
  sleep 20
done
echo "capture done $(date -u)" >> "$LOG"
