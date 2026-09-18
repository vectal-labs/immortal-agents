"""Run the installer in a staged checkout with all host effects mocked."""

from pathlib import Path
import tempfile
import unittest

from support import run_installer

MOCKS = r'''
python3() { :; }
mkdir() { command mkdir -p "$STATE_DIR" "$(dirname "$PLIST")"; }
if [ "$VERB" != status ]; then
  do_status() { return "$INSTALL_TEST_STATUS"; }
fi
setup_bb() { :; }
probe_bb() { return 0; }
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
    def run_install(self, *, saved=None, args=(), status=0, answer="", terminal=False, update_status=0, codex_status=0, runtime_status=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flag = root / "state/telemetry"
            if saved is not None:
                flag.parent.mkdir()
                flag.write_text(saved + "\n")
            result = run_installer(root, *args, mocks=MOCKS, answer=answer, env={
                "INSTALL_TEST_STATUS": str(status), "INSTALL_TEST_TERMINAL": str(int(terminal)),
                "INSTALL_TEST_UPDATE_STATUS": str(update_status), "INSTALL_TEST_CODEX_STATUS": str(codex_status),
                "INSTALL_TEST_RUNTIME_STATUS": str(runtime_status)})
            return result, flag.read_text().strip() if flag.exists() else None

    def test_codex_requires_explicit_opt_in(self):
        result, _ = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex-component:update", result.stdout)
        self.assertNotIn("codex-component:install", result.stdout)
        result, _ = self.run_install(args=("--codex-recovery",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex-component:install", result.stdout)

    def test_component_commands_do_not_reinstall_the_watcher(self):
        for verb, component in (("rollback-codex", "rollback"), ("check-codex", "check")):
            with self.subTest(verb=verb):
                result, _ = self.run_install(args=(verb,))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"codex-component:{component}", result.stdout)
                self.assertNotIn("codex-component:install", result.stdout)

    def test_codex_opt_in_rejects_other_verbs(self):
        result, _ = self.run_install(args=("update", "--codex-recovery"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only valid with install", result.stderr)

    def test_unattended_install_requires_explicit_consent(self):
        for answer in ("", "y\n"):
            with self.subTest(answer=answer):
                result, choice = self.run_install(answer=answer)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(choice, "off")
                self.assertIn("No interactive telemetry consent", result.stdout)

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

    def test_failed_status_preserves_consent_and_reports_the_problem(self):
        for failure, message in (({"status": 1}, "Optional telemetry: on"),
                                 ({"runtime_status": 1}, "runtime-pid:123")):
            with self.subTest(failure=failure):
                result, choice = self.run_install(saved="on", args=("status",), **failure)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stdout)
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
        for answer, expected in (("y\n", "on"), ("n\n", "off"), ("\n", "on"), ("", "off")):
            with self.subTest(answer=answer):
                result, choice = self.run_install(answer=answer, terminal=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(choice, expected)

    def test_install_failures_never_claim_success(self):
        cases = [
            ({"args": ("--codex-recovery",), "codex_status": 1}, None),
            ({"runtime_status": 1}, "Running code was not verified"),
            ({"saved": "on", "update_status": 1}, "update alerts failed"),
            ({"args": ("--no-telemetry",), "status": 1}, "Install failed."),
        ]
        for options, message in cases:
            with self.subTest(options=options):
                result, choice = self.run_install(**options)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("All set.", result.stdout)
                if message:
                    self.assertIn(message, result.stdout)
                if options.get("saved"):
                    self.assertEqual(choice, options["saved"])


if __name__ == "__main__":
    unittest.main()
