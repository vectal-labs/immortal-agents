#!/bin/bash
# Experiment 0007: offline-safe snapshotter. bb server is local, so this keeps
# capturing thread status + last events through the blackout.
# usage: 0007-capture.sh <total-secs> <thread-id>...
export PATH="/opt/homebrew/bin:$PATH"
BB="${BB_CLI:-/Applications/bb.app/Contents/Resources/app.asar.unpacked/node_modules/bb-app/host-daemon/dist/bb}"
OUT="$(cd "$(dirname "$0")" && pwd)/0007-captures"
TOTAL="${1:-1200}"; shift
t=0
while [ "$t" -le "$TOTAL" ]; do
  stamp="$(date -u +%H%M%S)"
  dir="$OUT/t${t}_${stamp}"; mkdir -p "$dir"
  for id in "$@"; do
    "$BB" thread show "$id" --json 2>/dev/null | jq -c '{id: .thread.id, p: .thread.providerId, status: .thread.status}' > "$dir/$id.status" 2>&1
    "$BB" thread log "$id" --json --all 2>/dev/null | jq -c '.[-3:][] | {t: (.createdAt/1000|todate|.[11:19]), type, st: .data.status, d: (.data.detail // "" | .[0:160]), retry: .data.willRetry}' > "$dir/$id.events" 2>&1
  done
  curl -sS -m 3 -o /dev/null -w "%{http_code}\n" http://captive.apple.com/hotspot-detect.html > "$dir/probe.txt" 2>&1 || echo 000 > "$dir/probe.txt"
  sleep 60; t=$((t + 60))
done
