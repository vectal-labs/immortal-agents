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
launchctl() { [ "$1" = bootstrap ]; }
sleep() { :; }
do_status() { return "$INSTALL_TEST_STATUS"; }
setup_cmux() { :; }
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
    def run_install(self, *, saved=None, args=(), status=0, answer="", terminal=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "watcher.py").touch()
            state = root / "state"
            state.mkdir()
            (state / "watcher.log").write_text("started\n")
            if saved is not None:
                (state / "telemetry").write_text(saved + "\n")
            # Keep argument parsing and the complete install flow, overriding
            # paths and host operations immediately before command dispatch.
            source, dispatch = INSTALLER.read_text().rsplit('\ncase "$VERB" in\n', 1)
            script = root / "install.sh"
            script.write_text(source + MOCKS + '\ncase "$VERB" in\n' + dispatch)
            env = dict(os.environ, INSTALL_TEST_DIR=tmp, INSTALL_TEST_STATUS=str(status),
                       INSTALL_TEST_TERMINAL=str(int(terminal)))
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

    def test_failed_watcher_start_returns_failure(self):
        result, _ = self.run_install(args=("--no-telemetry",), status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Install failed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
