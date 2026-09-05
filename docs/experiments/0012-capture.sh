#!/bin/bash
# Experiment 0012: Cursor GUI. Wait for the new agent session to appear in
# state.vscdb, then snapshot it every 20s: status, last bubble, transcript
# tail, hooks log, watcher state, and (if Accessibility is granted) window names.
# Usage: bash docs/experiments/0012-capture.sh <since-epoch-ms> [minutes]
SINCE=${1:?since-ms}; MIN=${2:-45}
DB="$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
OUT=docs/experiments/0012-captures; mkdir -p "$OUT"; LOG="$OUT/snapshots.log"
find_cid() {
  sqlite3 -readonly "$DB" "SELECT value FROM cursorDiskKV WHERE key LIKE 'composerData:%' ORDER BY rowid DESC LIMIT 6;" | python3 -c "
import sys,json
since=$SINCE
for line in sys.stdin:
    try: d=json.loads(line)
    except: continue
    if (d.get('createdAt') or 0)>=since and len(d.get('fullConversationHeadersOnly') or [])>=1:
        print(d['composerId']); break"
}
CID=""; for i in $(seq 1 60); do CID=$(find_cid); [ -n "$CID" ] && break; sleep 10; done
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) composerId=${CID:-NOT_FOUND}" >> "$LOG"
[ -z "$CID" ] && exit 1
END=$(( $(date +%s) + MIN*60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  {
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) cid=$CID"
    echo "--- watcher: $(python3 -c "import json;d=json.load(open('$HOME/.offline-agent-restart/state.json'));print('online',d.get('online'),'loss',d.get('last_loss_at'))" 2>&1)"
    sqlite3 -readonly "$DB" "SELECT value FROM cursorDiskKV WHERE key = 'composerData:$CID';" | python3 -c "
import sys,json,datetime,subprocess
d=json.loads(sys.stdin.read())
hs=d.get('fullConversationHeadersOnly') or []
print('--- composer: status',d.get('status'),'| generating',len(d.get('generatingBubbleIds') or []),'| contInProgress',d.get('isContinuationInProgress'),'| msgs',len(hs),'| updated',datetime.datetime.fromtimestamp((d.get('lastUpdatedAt') or 0)/1000,tz=datetime.timezone.utc).strftime('%H:%M:%SZ'),'| name',(d.get('name') or '')[:40])
if hs:
    b=hs[-1]['bubbleId']
    out=subprocess.run(['sqlite3','-readonly','$DB',f\"SELECT value FROM cursorDiskKV WHERE key = 'bubbleId:$CID:{b}';\"],capture_output=True,text=True).stdout.strip()
    try:
        j=json.loads(out); t=(j.get('text') or '')
        keys=[k for k in j.keys() if 'rror' in k or 'abort' in k.lower() or 'status' in k.lower() or 'interrupt' in k.lower()]
        print('--- last bubble: type',j.get('type'),'| textlen',len(t),'| tail:',t[-140:].replace(chr(10),' '),'| flags:',{k:j.get(k) for k in keys})
    except Exception as e: print('--- last bubble: unreadable',e)
"
    T=$(ls ~/.cursor/projects/*/agent-transcripts/$CID/*.jsonl 2>/dev/null | head -1)
    echo "--- transcript: ${T:-none} lines=$( [ -n "$T" ] && wc -l < "$T" ) last=$( [ -n "$T" ] && tail -c 200 "$T" | tr -d '\n' | cut -c1-160)"
    echo "--- hooks: $(wc -l < ~/.label-agent-stops/cursor-hooks.jsonl) lines; last: $(tail -1 ~/.label-agent-stops/cursor-hooks.jsonl | cut -c1-150)"
    echo "--- windows: $(osascript -e 'tell application "System Events" to tell process "Cursor" to return name of every window' 2>&1 | cut -c1-160)"
  } >> "$LOG" 2>&1
  sleep 20
done
echo "capture done $(date -u)" >> "$LOG"
