"""Keep integration tests away from the installed watcher's files."""
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from immortal.core import logbook, native_recovery, notify


def isolate_state(test):
    root = Path(test.enterContext(tempfile.TemporaryDirectory()))
    test.enterContext(mock.patch.multiple(logbook, STATE_DIR=root,
        STATE_PATH=root / 'state.json', LOG_PATH=root / 'watcher.log'))
    test.enterContext(mock.patch.object(native_recovery, '_homes', return_value=[]))
    test.enterContext(mock.patch.object(notify, 'webhook_url', return_value=None))
    return root


def run_installer(root, *args, mocks='', env=None, answer=''):
    """Run the installer with shared, isolated launchd and host boundaries."""
    repo = Path(__file__).resolve().parents[1]
    (root / 'home').mkdir(exist_ok=True)
    (root / 'watcher.py').touch()
    (root / 'state').mkdir(exist_ok=True)
    (root / 'LaunchAgents').mkdir(exist_ok=True)
    (root / 'state/watcher.log').write_text('{"event": "probe", "online": true}\n')
    source, dispatch = (repo / 'install.sh').read_text().rsplit('\ncase "$VERB" in\n', 1)
    boundaries = r'''
STATE_DIR="$INSTALL_TEST_DIR/state"
PLIST="$INSTALL_TEST_DIR/LaunchAgents/watcher.plist"
REPO_DIR="$INSTALL_TEST_DIR"
uname() { echo Darwin; }
bootout_watcher() { :; }
launchctl() {
  case "$1" in
    bootstrap) return 0 ;;
    print) [ "$INSTALL_TEST_STATUS" = 0 ] || return 1; printf '\tpid = 123\n' ;;
    *) return 1 ;;
  esac
}
sleep() { :; }
setup_cmux() { :; }
setup_updates() { return "$INSTALL_TEST_UPDATE_STATUS"; }
run_updates() { :; }
runtime_status() { echo "runtime-pid:$1"; return "$INSTALL_TEST_RUNTIME_STATUS"; }
run_codex_recovery() {
  echo "codex-component:$1"
  if [ "$1" = install ]; then return "$INSTALL_TEST_CODEX_STATUS"; fi
  return 0
}
launch_hosts() { :; }
do_check() { probe_bb; }
'''
    script = root / 'install.sh'
    script.write_text(source + boundaries + mocks + '\ncase "$VERB" in\n' + dispatch)
    environment = dict(os.environ, HOME=str(root / 'home'), INSTALL_TEST_DIR=str(root),
        PYTHONPATH=str(repo), NO_COLOR='1', INSTALL_TEST_STATUS='0',
        INSTALL_TEST_UPDATE_STATUS='0', INSTALL_TEST_RUNTIME_STATUS='0',
        INSTALL_TEST_CODEX_STATUS='0', INSTALL_TEST_TERMINAL='0')
    environment.update(env or {})
    return subprocess.run(['/bin/bash', str(script), *args], input=answer,
        capture_output=True, text=True, timeout=15, env=environment, start_new_session=True)
