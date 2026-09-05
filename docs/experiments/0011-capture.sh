#!/bin/bash
# Experiment 0011: snapshot three Cursor CLI sessions (cmux, Terminal.app, bb ACP)
# every 20s so we can see exactly how each one dies and what is left on disk.
# Usage: bash docs/experiments/0011-capture.sh <minutes> <bb-thread-id> <cmux-surface> <terminal-tty>
MIN=${1:-45}; THR=${2:?bb-thread-id}; SURF=${3:?cmux-surface}; TTY=${4:?terminal-tty}
OUT=docs/experiments/0011-captures; mkdir -p "$OUT"
C=/Applications/cmux.app/Contents/Resources/bin/cmux; export CMUX_QUIET=1
END=$(( $(date +%s) + MIN*60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  TS=$(date -u +%H%M%S)
  {
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "--- watcher: $(python3 -c "import json;d=json.load(open('$HOME/.offline-agent-restart/state.json'));print('online',d.get('online'),'loss',d.get('last_loss_at'),'recovery',d.get('last_recovery_at'))" 2>&1)"
    echo "--- cmux $SURF (tail 14)"; $C read-screen --surface "$SURF" 2>&1 | grep -v '^\s*$' | tail -14
    echo "--- terminal $TTY (tail 14)"
    osascript -e "tell application \"Terminal\"
      repeat with wi from 1 to count of windows
        repeat with ti from 1 to count of tabs of window wi
          if tty of tab ti of window wi is \"$TTY\" then return contents of tab ti of window wi
        end repeat
      end repeat
      return \"(tab not found)\"
    end tell" 2>&1 | grep -v '^\s*$' | tail -14
    echo "--- bb $THR"; bb thread show "$THR" 2>&1 | sed -n '2p'
    bb thread log "$THR" --all --json 2>/dev/null | python3 -c "
import json,sys,datetime,collections
evs=json.load(sys.stdin); c=collections.Counter(e.get('type') for e in evs[-40:])
last=evs[-1] if evs else {}
print('last', datetime.datetime.fromtimestamp(last.get('createdAt',0)/1000,tz=datetime.timezone.utc).strftime('%H:%M:%S'), last.get('type'), json.dumps(last.get('data') or {})[:160])
print('recent types', dict(c))" 2>&1
    echo "--- cursor stores (mtime)"; ls -lt ~/.cursor/chats/*/*/store.db-wal ~/.cursor/acp-sessions/*/store.db-wal 2>/dev/null | head -4 | awk '{print $6,$7,$8,$9}'
    echo "--- hooks: $(wc -l < ~/.label-agent-stops/cursor-hooks.jsonl) lines, last: $(tail -1 ~/.label-agent-stops/cursor-hooks.jsonl | cut -c1-120)"
    echo "--- procs: $(ps -axo pid,etime,command | grep -c '[c]ursor-agent.*index.js')" cursor-agent processes
  } >> "$OUT/snapshots.log" 2>&1
  sleep 20
done
echo "capture done $(date -u)" >> "$OUT/snapshots.log"
