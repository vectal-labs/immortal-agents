#!/bin/bash
# immortal-agents installer (ADR 0040). One script, zero infrastructure.
# usage: ./install.sh [install|uninstall|status|logs|check] [--discord <webhook-url>] [--telemetry|--no-telemetry]
#
# Never uses `launchctl submit` (Incident 0001: it restarts the job forever).
# Netguard is installed by David only, by hand; this script never touches it.
set -euo pipefail

LABEL="com.immortal-agents.watcher"
STATE_DIR="$HOME/.immortal-agents"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
DOMAIN="gui/$(id -u)"

# Colors only on a real terminal; honor NO_COLOR (https://no-color.org).
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi
OK="${GREEN}✓${RESET}"; WARN="${YELLOW}!${RESET}"; BAD="${RED}✗${RESET}"; NOTE="${DIM}•${RESET}"
ok()      { echo "  $OK $*"; }
warn()    { echo "  $WARN $*"; }
bad()     { echo "  $BAD $*"; }
note()    { echo "  $NOTE $*"; }
heading() { echo "${BOLD}$*${RESET}"; }

ARGS=("$@")
VERB="install"
DISCORD_URL=""
TELEMETRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    install|uninstall|status|logs|check|update|notification-test) VERB="$1" ;;
    --discord)
      [ $# -ge 2 ] || { echo "error: --discord needs a webhook URL" >&2; exit 1; }
      DISCORD_URL="$2"; shift ;;
    --telemetry) TELEMETRY=on ;;
    --no-telemetry) TELEMETRY=off ;;
    -h|--help)
      sed -n '2,4p' "$0"
      echo "  update              install the latest public release and restart only the watcher"
      echo "  notification-test   request a test notification from the update LaunchAgent"
      exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; exit 1 ;;
  esac
  shift
done

loaded() { launchctl print "$DOMAIN/$1" >/dev/null 2>&1; }

# Piped install (curl ... | bash): there is no repo next to this script. Clone
# into the folder that already holds the most git repos (up to 3 levels under
# ~, hidden dirs and ~/Library skipped, ~/code when none), then hand off to the
# clone's install.sh so the plist points at a real checkout.
REPO_URL="https://github.com/vectal-labs/immortal-agents.git"
pick_code_dir() {
  local best="$HOME/code" n=0 count parent
  while read -r count parent; do
    [ "$count" -gt "$n" ] && { n=$count; best=$parent; }
  done < <(find "$HOME" -mindepth 2 -maxdepth 4 \( -path "$HOME/.*" -o -path "$HOME/Library/*" -o -name '.?*' ! -name .git \) -prune \
             -o -name .git -prune -print 2>/dev/null | sed 's|/[^/]*/\.git$||' | sort | uniq -c | sort -rn)
  echo "$best"
}
if [ ! -f "$REPO_DIR/watcher.py" ]; then
  [ "$VERB" = install ] || { echo "error: run '$VERB' from your clone: <clone>/install.sh $VERB" >&2; exit 1; }
  command -v git >/dev/null || { echo "error: git not found on PATH" >&2; exit 1; }
  DEST="$(pick_code_dir)/immortal-agents"
  if [ -f "$DEST/watcher.py" ]; then
    echo "Using existing clone at $DEST"
  else
    echo "Cloning into $DEST"
    git clone --depth 1 "$REPO_URL" "$DEST"
  fi
  exec bash "$DEST/install.sh" ${ARGS[@]+"${ARGS[@]}"}
fi

# Discover Node + bb so launchd can run the bb CLI (`#!/usr/bin/env node`).
BB_STATUS=""
_show_bb_result() {
  local result="$1" line
  line="$(printf '%s\n' "$result" | grep -E '^(ok|skip|warn|error):' | tail -n 1)"
  [ -n "$line" ] || line="error:${result:-bb runtime check failed}"
  case "$line" in
    ok:*) BB_STATUS=ok; ok "bb: ${line#ok:}" ;;
    skip:*) BB_STATUS=skip; note "bb: ${line#skip:}" ;;
    warn:*) BB_STATUS=warn; warn "bb: ${line#warn:}" ;;
    error:*) BB_STATUS=error; bad "bb: ${line#error:}" ;;
  esac
}

run_bb_runtime() {
  local verb="$1" python3_bin="${2:-$(find_python3)}" result rc=0
  if [ "$verb" = install ]; then
    result="$(cd "$REPO_DIR" && WATCHER_STATE_DIR="$STATE_DIR" IMMORTAL_PLIST="$PLIST" \
      "$python3_bin" -m immortal.core.bb_runtime "$verb" 2>&1)" || rc=$?
  else
    result="$(cd "$REPO_DIR" && WATCHER_STATE_DIR="$STATE_DIR" \
      "$python3_bin" -m immortal.core.bb_runtime "$verb" 2>&1)" || rc=$?
  fi
  _show_bb_result "$result"
  return $rc
}

setup_bb() { run_bb_runtime install "${1:-}"; }
probe_bb() { run_bb_runtime check "${1:-}"; }

run_updates() {
  (cd "$REPO_DIR" && WATCHER_STATE_DIR="$STATE_DIR" PYTHONDONTWRITEBYTECODE=1 \
    "$(find_python3)" -m immortal.core.updates "$@" --repo "$REPO_DIR")
}
setup_updates() { run_updates setup --python "$1"; }
remove_updates() { run_updates remove; }
do_update() {
  (cd "$REPO_DIR" && WATCHER_STATE_DIR="$STATE_DIR" PYTHONDONTWRITEBYTECODE=1 \
    "$(find_python3)" -m immortal.core.updater)
}

# The plist's python3 when installed, else PATH's. TCC grants attach to the
# binary that sends the Apple event, so the probe must use the watcher's python3.
find_python3() {
  local bin=""
  [ -f "$PLIST" ] && bin="$(plutil -extract ProgramArguments.0 raw -o - "$PLIST" 2>/dev/null || true)"
  [ -x "$bin" ] || bin="$(command -v python3 || true)"
  [ -n "$bin" ] || { echo "error: python3 not found on PATH" >&2; exit 1; }
  echo "$bin"
}

# One harmless JXA read per running host, the same osascript path the host
# adapters use (immortal/core/osa.py), so macOS raises its Automation prompt now instead
# of silently failing under launchd later. Never launches an app.
PROBE_PY='
import os, subprocess, sys
from immortal.core import osa
label, proc, script = sys.argv[1:4]
ok, warn, bad = (os.environ.get(k, "") for k in ("OK", "WARN", "BAD"))
fix = "Fix: System Settings > Privacy & Security > Automation > Python (python3) > enable " + label
if not osa.app_running(proc):
    print("  " + warn + " " + label + ": not running, skipped. Open it, then run ./install.sh check")
    sys.exit(2)
try:
    osa.run_jxa(script, timeout=90)
    print("  " + ok + " " + label + ": granted")
except subprocess.TimeoutExpired:
    print("  " + bad + " " + label + ": no answer in 90s. Answer the macOS prompt, then run ./install.sh check")
    sys.exit(1)
except osa.OsaError as exc:
    print("  " + bad + " " + label + ": denied (" + str(exc) + "). " + fix)
    sys.exit(1)
'

# Install-time only. macOS raises the Automation prompt on the first Apple
# event to a *running* app, so a closed terminal app means no prompt and a
# skipped host. Open each installed-but-closed host in the background
# (open -g: no focus steal) so do_check can trigger the prompt right now.
# ./install.sh check stays read-only and never launches anything.
LAUNCHED_HOSTS=()
launch_hosts() {
  local label proc app pending=() i
  for spec in "Terminal.app:Terminal:/System/Applications/Utilities/Terminal.app" "Ghostty:ghostty:/Applications/Ghostty.app"; do
    IFS=: read -r label proc app <<<"$spec"
    [ -d "$app" ] || continue
    /usr/bin/pgrep -x "$proc" >/dev/null 2>&1 && continue
    if open -g -a "$app" 2>/dev/null; then
      LAUNCHED_HOSTS+=("$label"); pending+=("$proc")
    else
      warn "$label: could not open it. Open it by hand, then run ./install.sh check"
    fi
  done
  [ ${#LAUNCHED_HOSTS[@]} -gt 0 ] || return 0
  note "Opened ${LAUNCHED_HOSTS[*]} in the background so macOS can ask for permission now"
  for i in $(seq 1 20); do
    local left=0
    for proc in "${pending[@]}"; do /usr/bin/pgrep -x "$proc" >/dev/null 2>&1 || left=1; done
    [ $left -eq 0 ] && break
    sleep 0.5
  done
  sleep 1  # let the app finish launching before it gets its first Apple event
}

# Exit 0 = every running host granted, 2 = some host skipped, 1 = a host denied.
do_check() {
  local python3_bin rc=0
  python3_bin="$(find_python3)"
  heading "Automation permission ${DIM}(lets the watcher type into your terminal, via $python3_bin)${RESET}"
  export OK WARN BAD
  probe_host() {
    local r=0
    (cd "$REPO_DIR" && "$python3_bin" -c "$PROBE_PY" "$@") || r=$?
    case $r in 1) rc=1 ;; 2) [ $rc -eq 1 ] || rc=2 ;; esac
  }
  probe_host Terminal.app Terminal 'Application("Terminal").windows().length'
  probe_host Ghostty ghostty 'Application("Ghostty").windows().length'
  probe_cmux || { [ $? -eq 1 ] && rc=1; }
  probe_bb "$python3_bin" || {
    local bb_rc=$?
    [ $bb_rc -eq 1 ] && rc=1
    [ $bb_rc -eq 2 ] && [ $rc -ne 1 ] && rc=2
  }
  return $rc
}

# cmux has no macOS prompt; its own socket setting decides. The launchd watcher
# is not "started inside cmux", so anything but automation makes cmux refuse it
# with "Access denied" and the watcher sees zero cmux panes. immortal/core/cmux_config.py
# pins the mode in ~/.config/cmux/cmux.json, which overrides the Settings UI and
# survives cmux settings migrations.
CMUX_BIN=/Applications/cmux.app/Contents/Resources/bin/cmux
cmux_mode() { (cd "$REPO_DIR" && "$(find_python3)" -m immortal.core.cmux_config get 2>/dev/null); }

# Install-time fix. Exit 0 = automation (already or now), 1 = could not fix.
setup_cmux() {
  local result backup
  [ -x "$CMUX_BIN" ] || return 0
  result="$(cd "$REPO_DIR" && "$(find_python3)" -m immortal.core.cmux_config ensure 2>&1)" || result="error: $result"
  case "$result" in
    unchanged) ok "cmux: socket control mode already pinned to automation in ~/.config/cmux/cmux.json" ;;
    written)
      if ! "$CMUX_BIN" config check >/dev/null 2>&1; then
        backup="$(ls -t "$HOME/.config/cmux/cmux.json."*.bak 2>/dev/null | head -n 1)"
        [ -n "$backup" ] && cp "$backup" "$HOME/.config/cmux/cmux.json"
        bad "cmux: edit of ~/.config/cmux/cmux.json failed validation, restored the backup. Set socket control mode by hand: cmux Settings > Automation"
        return 1
      fi
      ok "cmux: pinned socket control mode to automation in ~/.config/cmux/cmux.json ${DIM}(old file backed up next to it)${RESET}"
      if /usr/bin/pgrep -x cmux >/dev/null 2>&1; then
        CMUX_QUIET=1 "$CMUX_BIN" reload-config >/dev/null 2>&1 || true
        note "cmux: config reloaded. If the watcher still cannot see cmux panes, quit and reopen cmux once"
      fi ;;
    *) bad "cmux: could not pin socket control mode ($result). Set it by hand: cmux Settings > Automation > socket control mode > automation"; return 1 ;;
  esac
}

# Read-only probe for ./install.sh check. Exit 0 = automation, 1 = anything else.
probe_cmux() {
  local mode
  [ -x "$CMUX_BIN" ] || { note "cmux: not installed, skipped"; return 0; }
  mode="$(cmux_mode)"
  if [ "$mode" = automation ]; then
    ok "cmux: socket control mode is automation"
  else
    bad "cmux: socket control mode is \"${mode:-unknown}\", the watcher cannot see cmux panes. Fix: run ./install.sh again, or set cmux Settings > Automation > socket control mode > automation"
    return 1
  fi
}

bootout_watcher() {
  if loaded "$LABEL"; then
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    loaded "$LABEL" || ok "Stopped $LABEL"
  fi
}

# Exit 0 = loaded and running, 1 = not.
do_status() {
  local pid="" last rc=0
  if [ "$VERB" = status ]; then
    local telemetry=off
    if [ -f "$STATE_DIR/telemetry" ] && [ "$(cat "$STATE_DIR/telemetry")" = on ]; then
      telemetry=on
    fi
    note "Optional telemetry: $telemetry"
  fi
  loaded "$LABEL" && pid="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk '/^\tpid = /{print $3}')"
  if [ -n "$pid" ]; then
    ok "Watcher loaded and running ${DIM}(pid $pid)${RESET}"
  elif loaded "$LABEL"; then
    bad "Watcher loaded but not running. See $STATE_DIR/stderr.log"; rc=1
  else
    bad "Watcher not installed. Run ./install.sh"; rc=1
  fi
  if [ -s "$STATE_DIR/watcher.log" ]; then
    last="$(grep '"event": "probe"' "$STATE_DIR/watcher.log" | tail -n 1)"
    case "$last" in
      *'"online": true'*)  ok "Internet probe: online" ;;
      *'"online": false'*) warn "Internet probe: offline" ;;
      *) note "No probe in the log yet" ;;
    esac
    [ "$VERB" = status ] && echo "  ${DIM}last log line: $(tail -n 1 "$STATE_DIR/watcher.log")${RESET}"
  else
    note "No watcher.log yet at $STATE_DIR/watcher.log"
  fi
  probe_bb || true
  if [ "$VERB" = status ]; then
    run_updates status || true
    loaded "com.immortal-agents.updates" || warn "Update checker is not loaded. Run ./install.sh"
  fi
  return $rc
}

print_help_lines() {
  echo "  ${DIM}./install.sh status${RESET}   is it running?"
  echo "  ${DIM}./install.sh logs${RESET}     follow the watcher log"
  echo "  ${DIM}./install.sh check${RESET}    re-run the permission and bb probes"
  echo "  ${DIM}./install.sh update${RESET}   install the latest public release"
  echo "  ${DIM}./install.sh notification-test${RESET}   test Mac update alerts"
}

do_install() {
  [ "$(uname -s)" = "Darwin" ] || { echo "error: immortal-agents runs on macOS only" >&2; exit 1; }
  local python3_bin
  python3_bin="$(command -v python3 || true)"
  [ -n "$python3_bin" ] || { echo "error: python3 not found on PATH" >&2; exit 1; }
  [ -f "$REPO_DIR/watcher.py" ] || { echo "error: watcher.py not found in $REPO_DIR" >&2; exit 1; }

  echo
  heading "immortal-agents installer"
  ok "macOS, python3 at $python3_bin"
  mkdir -p "$STATE_DIR" "$HOME/Library/LaunchAgents"
  if [ -n "$DISCORD_URL" ]; then
    printf '%s\n' "$DISCORD_URL" > "$STATE_DIR/discord_webhook"
    chmod 600 "$STATE_DIR/discord_webhook"
    ok "Wrote Discord webhook to $STATE_DIR/discord_webhook"
  fi

  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProgramArguments</key>
  <array>
    <string>$python3_bin</string>
    <string>$REPO_DIR/watcher.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CMUX_QUIET</key>
    <string>1</string>
    <key>PYTHONDONTWRITEBYTECODE</key>
    <string>1</string>
    <key>WATCHER_STATE_DIR</key>
    <string>$STATE_DIR</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$STATE_DIR/stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$STATE_DIR/stderr.log</string>
</dict>
</plist>
PLIST
  ok "Wrote $PLIST"
  setup_bb "$python3_bin" || true

  bootout_watcher
  launchctl bootstrap "$DOMAIN" "$PLIST"
  # The watcher probes on start; give the first log line a moment to land.
  local i; for i in 1 2 3 4 5; do [ -s "$STATE_DIR/watcher.log" ] && break; sleep 1; done
  local status_rc=0 check_rc=0
  do_status || status_rc=$?
  echo
  setup_cmux || true
  launch_hosts
  do_check || check_rc=$?
  echo
  if [ -z "$TELEMETRY" ] && [ -f "$STATE_DIR/telemetry" ]; then
    case "$(cat "$STATE_DIR/telemetry")" in on) TELEMETRY=on ;; off) TELEMETRY=off ;; esac
  fi
  if [ -z "$TELEMETRY" ]; then
    local reply=""
    TELEMETRY=off
    if [ -t 0 ] && read -r -p "  Send optional usage and crash diagnostics? See README: What we collect. [Y/n] " reply; then
      case "$reply" in [nN]*) TELEMETRY=off ;; *) TELEMETRY=on ;; esac
    else
      note "No interactive telemetry consent; telemetry stays off"
    fi
  fi
  printf '%s\n' "$TELEMETRY" > "$STATE_DIR/telemetry"
  note "Optional telemetry: $TELEMETRY. Disable any time: echo off > $STATE_DIR/telemetry"
  (cd "$REPO_DIR" && "$python3_bin" -c 'from immortal.core import telemetry; t = telemetry.send("install"); t and t.join()') || true
  echo
  local updates_rc=0
  setup_updates "$python3_bin" || updates_rc=$?
  if [ $status_rc -ne 0 ]; then
    heading "${RED}Install failed.${RESET} The watcher is not running. See $STATE_DIR/stderr.log"
  elif [ $updates_rc -ne 0 ]; then
    heading "${YELLOW}Watcher installed, but update alerts failed.${RESET} Fix the error above and run ./install.sh again"
  elif [ "$BB_STATUS" = error ]; then
    heading "${YELLOW}Installed and running, but bb is not ready.${RESET} Fix the bb line above, then run ./install.sh"
  elif [ "$BB_STATUS" = warn ]; then
    heading "${YELLOW}Installed and running.${RESET} Open bb, then run ./install.sh check"
  elif [ $check_rc -eq 1 ]; then
    heading "${YELLOW}Installed, but a permission was denied.${RESET} Fix it with the Settings path above, then run ./install.sh check"
  elif [ $check_rc -eq 2 ]; then
    heading "${GREEN}Installed and running.${RESET} One step left: open the terminal app(s) marked ${WARN} above, run ./install.sh check, and click Allow"
  else
    heading "${GREEN}All set.${RESET} immortal-agents is running in the background"
  fi
  [ ${#LAUNCHED_HOSTS[@]} -gt 0 ] && echo "  Left ${LAUNCHED_HOSTS[*]} open. You can close them."
  echo "  When your internet drops for 2+ minutes and comes back, the AI coding sessions"
  echo "  that died in that window get a \"keep going\" automatically. Survives reboots."
  echo
  print_help_lines
  echo
  [ "$status_rc" -eq 0 ] || return "$status_rc"
  return "$updates_rc"
}

do_uninstall() {
  echo
  remove_updates
  bootout_watcher
  if loaded "$LABEL"; then
    bad "$LABEL is still loaded. Try: launchctl bootout $DOMAIN/$LABEL"
    return 1
  fi
  rm -f "$PLIST"
  ok "Removed $PLIST"
  echo
  heading "${GREEN}Uninstalled.${RESET} Nothing is running and nothing starts on reboot"
  echo "  Kept $STATE_DIR (logs, state, Discord webhook). Delete it with: rm -rf $STATE_DIR"
  echo "  The macOS Automation grant for python3 stays. Remove it in System Settings > Privacy & Security > Automation"
  echo
}

do_logs() {
  mkdir -p "$STATE_DIR"
  touch "$STATE_DIR/watcher.log"
  exec tail -f "$STATE_DIR/watcher.log"
}

case "$VERB" in
  install) do_install ;;
  uninstall) do_uninstall ;;
  status) do_status ;;
  logs) do_logs ;;
  check) do_check ;;
  update) do_update ;;
  notification-test) run_updates notification-test ;;
esac
