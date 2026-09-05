"""Run the installer in a staged checkout with all host effects mocked."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parent.parent / "install.sh"
MOCKS = r'''
STATE_DIR="$INSTALL_TEST_DIR/state"
PLIST="$INSTALL_TEST_DIR/LaunchAgents/watcher.plist"
REPO_DIR="$INSTALL_TEST_DIR"
uname() { echo Darwin; }
python3() { :; }
mkdir() { command mkdir -p "$STATE_DIR" "$(dirname "$PLIST")"; }
bootout_watcher() { :; }
launchctl() {
  case "$1" in
    bootstrap) return 0 ;;
    print) [ "$INSTALL_TEST_STATUS" = 0 ] || return 1; printf '\tpid = 123\n' ;;
    *) return 1 ;;
  esac
}
sleep() { :; }
if [ "$VERB" != status ]; then
  do_status() { return "$INSTALL_TEST_STATUS"; }
fi
setup_cmux() { :; }
setup_bb() { :; }
setup_updates() { return "$INSTALL_TEST_UPDATE_STATUS"; }
run_updates() { :; }
probe_bb() { return 0; }
launch_hosts() { :; }
do_check() { return 0; }
# Only interactive cases fake the terminal check; read still consumes real stdin.
if [ "$INSTALL_TEST_TERMINAL" = 1 ]; then
  [() {
    if builtin [ "$1" = -t ]; then return 0; fi
    builtin [ "$@"
  }
fi
'''


class InstallTests(unittest.TestCase):
    def run_install(self, *, saved=None, args=(), status=0, answer="", terminal=False, update_status=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "watcher.py").touch()
            state = root / "state"
            state.mkdir()
            (state / "watcher.log").write_text('{"event": "probe", "online": true}\n')
            if saved is not None:
                (state / "telemetry").write_text(saved + "\n")
            # Keep argument parsing and the complete install flow, overriding
            # paths and host operations immediately before command dispatch.
            source, dispatch = INSTALLER.read_text().rsplit('\ncase "$VERB" in\n', 1)
            script = root / "install.sh"
            script.write_text(source + MOCKS + '\ncase "$VERB" in\n' + dispatch)
            env = dict(os.environ, INSTALL_TEST_DIR=tmp, INSTALL_TEST_STATUS=str(status),
                       INSTALL_TEST_TERMINAL=str(int(terminal)), INSTALL_TEST_UPDATE_STATUS=str(update_status))
            command = ["/bin/bash", str(script), *args]
            options = dict(env=env, capture_output=True, text=True, timeout=5,
                           start_new_session=True)
            result = subprocess.run(command, input=answer, **options)
            flag = state / "telemetry"
            return result, flag.read_text().strip() if flag.exists() else None

    def test_unattended_install_defaults_off(self):
        result, choice = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(choice, "off")
        self.assertIn("No interactive telemetry consent", result.stdout)

    def test_piped_yes_is_not_interactive_consent(self):
        result, choice = self.run_install(answer="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(choice, "off")

    def test_unattended_install_can_explicitly_enable_telemetry(self):
        for saved in (None, "on", "off"):
            with self.subTest(saved=saved):
                result, choice = self.run_install(saved=saved, args=("--telemetry",))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(choice, "on")
                self.assertIn("Optional telemetry: on", result.stdout)
                self.assertNotIn("Send optional usage", result.stderr)

    def test_status_reports_telemetry_without_changing_it(self):
        for saved in (None, "on", "off"):
            with self.subTest(saved=saved):
                result, choice = self.run_install(saved=saved, args=("status",))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"Optional telemetry: {saved or 'off'}", result.stdout)
                self.assertEqual(choice, saved)

    def test_failed_status_still_reports_telemetry(self):
        result, choice = self.run_install(saved="on", args=("status",), status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Optional telemetry: on", result.stdout)
        self.assertEqual(choice, "on")

    def test_unattended_reinstall_preserves_saved_choice(self):
        for saved in ("on", "off"):
            with self.subTest(saved=saved):
                result, choice = self.run_install(saved=saved)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(choice, saved)
                self.assertNotIn("Help improve", result.stderr)

    def test_no_telemetry_overrides_saved_consent(self):
        result, choice = self.run_install(saved="on", args=("--no-telemetry",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(choice, "off")

    def test_interactive_choices(self):
        for answer, expected in (("y\n", "on"), ("n\n", "off"), ("\n", "on")):
            with self.subTest(answer=answer):
                result, choice = self.run_install(answer=answer, terminal=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(choice, expected)

    def test_interactive_eof_is_not_consent(self):
        result, choice = self.run_install(terminal=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(choice, "off")

    def test_failed_update_checker_is_not_reported_as_all_set(self):
        result, choice = self.run_install(saved="on", update_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(choice, "on")
        self.assertIn("update alerts failed", result.stdout)
        self.assertNotIn("All set.", result.stdout)

    def test_failed_watcher_start_returns_failure(self):
        result, _ = self.run_install(args=("--no-telemetry",), status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Install failed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
