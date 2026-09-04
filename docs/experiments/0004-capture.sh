#!/bin/bash
# Experiment 0004: offline-safe snapshotter. cmux is local, so this keeps
# capturing pane text + rollout tails through the blackout. Args: total secs.
CMUX=/Applications/cmux.app/Contents/Resources/bin/cmux
export CMUX_QUIET=1
OUT="$(cd "$(dirname "$0")" && pwd)/0004-captures"
TOTAL="${1:-1800}"
t=0
while [ "$t" -le "$TOTAL" ]; do
  stamp="$(date -u +%H%M%S)"
  dir="$OUT/t${t}_${stamp}"; mkdir -p "$dir"
  for s in 1 2 3; do
    "$CMUX" read-screen --surface surface:$s > "$dir/pane_$s.txt" 2>&1
  done
  for f in $(ls -t ~/.codex/sessions/2026/08/26/*.jsonl 2>/dev/null | head -3); do
    tail -n 5 "$f" | cut -c1-400 > "$dir/$(basename "$f" .jsonl | cut -c1-40)_tail.txt"
  done
  curl -sS -m 3 -o /dev/null -w "%{http_code}\n" http://captive.apple.com/hotspot-detect.html > "$dir/probe.txt" 2>&1 || echo 000 > "$dir/probe.txt"
  sleep 60; t=$((t + 60))
done
