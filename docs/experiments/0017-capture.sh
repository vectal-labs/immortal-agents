#!/bin/bash
# Experiment 0017: Codex only in cmux, real Wi-Fi cut. Does Codex still give up
# (write task_complete) mid-outage on 0.153.2 direct-to-OpenAI, and does the
# watcher revive it? Every 20s snapshot the probe, watcher state/log, the three
# panes, and the newest rollout tails for the shared cwd.
# Usage: bash experiments/0017-capture.sh <minutes> <targetA> <targetB> <control>
MIN=${1:-50}; TA=${2:?target A surface}; TB=${3:?target B surface}; CTL=${4:?control surface}
OUT=experiments/0017-captures; mkdir -p "$OUT"; LOG="$OUT/snapshots.log"
STATE="$HOME/.immortal-agents"
C=/Applications/cmux.app/Contents/Resources/bin/cmux; export CMUX_QUIET=1
END=$(( $(date +%s) + MIN*60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  {
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) probe=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' http://captive.apple.com/hotspot-detect.html 2>/dev/null || echo 000)"
    echo "--- watcher: $(python3 -c "import json;d=json.load(open('$STATE/state.json'));print('online',d.get('online'),'loss',d.get('last_loss_at'),'recovery',d.get('last_recovery_at'),'revived',list((d.get('revived') or {}).keys())[-4:])" 2>&1)"
    echo "--- watcher log (non-probe tail 12)"; grep -v '"event": "probe"' "$STATE/watcher.log" | tail -12 | cut -c1-280
    for pair in "codex-A:$TA" "codex-B:$TB" "codex-control:$CTL"; do
      echo "--- ${pair%%:*} ${pair#*:} (tail 20)"; "$C" read-screen --surface "${pair#*:}" 2>&1 | grep -v '^\s*$' | tail -20
    done
    echo "--- rollouts (newest 3, last event_msg each)"
    for f in $(ls -t ~/.codex/sessions/$(date -u +%Y/%m/%d)/*.jsonl 2>/dev/null | head -3); do
      echo "$(basename "$f" | cut -c1-60): $(grep '"event_msg"' "$f" | tail -1 | cut -c1-200)"
    done
  } >> "$LOG" 2>&1
  sleep 20
done
echo "capture done $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"
