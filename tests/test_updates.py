import copy
import json
import os
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from immortal.core import updates, updater

ROOT = Path(__file__).resolve().parents[1]
RELEASE = {"version": "0.2.0", "published_at": "2026-09-05", "important": True,
           "summary": "Recover BB threads after daemon restarts.", "commit": "a" * 40,
           "notes_url": "https://github.com/vectal-labs/immortal-agents/releases/tag/v0.2.0"}
FEED = {"schema": 1, "releases": [RELEASE]}


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.tmp / "state"
        self.repo = self.tmp / "repo"
        (self.repo / "immortal").mkdir(parents=True)
        (self.repo / "immortal" / "__init__.py").write_text('__version__ = "0.1.0"\n')
        self.feed = self.enterContext(mock.patch.object(updates, "fetch_feed", return_value=copy.deepcopy(FEED)))
        self.public = self.enterContext(mock.patch.object(updates, "published", return_value=True))
        self.notice = self.enterContext(mock.patch.object(updates, "notify_update", return_value=True))

    def test_hourly_check_notifies_once_across_process_restarts(self):
        updates.check(self.repo, self.state, now=10000)
        updates.check(self.repo, self.state, now=10001)
        updates.check(self.repo, self.state, now=13600)
        self.notice.assert_called_once_with(RELEASE)
        self.assertEqual(self.feed.call_count, 2)
        self.public.assert_called_once_with(RELEASE)
        saved = updates.load_state(self.state)
        self.assertEqual(saved["notified_version"], "0.2.0")
        self.assertIn("0.2.0", updates.status(self.repo, self.state))

    def test_silent_release_still_shows_pending_update(self):
        self.feed.return_value["releases"][0]["important"] = False
        updates.check(self.repo, self.state, now=10000)
        self.notice.assert_not_called()
        self.assertIn("0.2.0", updates.status(self.repo, self.state))

    def test_installer_update_command_is_available(self):
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"],
                                capture_output=True, text=True, timeout=10)
        self.assertIn("update", result.stdout)


    def test_later_minor_release_does_not_hide_an_important_fix(self):
        latest = {**RELEASE, "version": "0.3.0", "important": False,
                  "notes_url": updates.PUBLIC_REPO + "/releases/tag/v0.3.0"}
        self.feed.return_value["releases"].append(latest)
        updates.check(self.repo, self.state, now=10000)
        self.notice.assert_called_once_with(RELEASE)
        self.assertIn("0.3.0", updates.status(self.repo, self.state))

    def test_newer_installed_version_never_notifies(self):
        (self.repo / "immortal" / "__init__.py").write_text('__version__ = "0.3.0"\n')
        updates.check(self.repo, self.state, now=10000)
        self.notice.assert_not_called()

    def test_offline_or_malformed_feed_keeps_cached_warning(self):
        updates.check(self.repo, self.state, now=10000)
        for failure in (OSError("offline"), ValueError("invalid JSON")):
            self.feed.side_effect = failure
            updates.check(self.repo, self.state, now=14000, force=True)
            self.assertIn("0.2.0", updates.status(self.repo, self.state))
            self.assertIn("unavailable", updates.status(self.repo, self.state))
        self.notice.assert_called_once()

    def test_unpublished_release_is_not_advertised(self):
        self.public.return_value = False
        updates.check(self.repo, self.state, now=10000)
        self.notice.assert_not_called()
        self.assertNotIn("available", updates.load_state(self.state))

    def test_notification_failure_is_visible_without_spam(self):
        self.notice.return_value = False
        updates.check(self.repo, self.state, now=10000)
        updates.check(self.repo, self.state, now=14000)
        self.notice.assert_called_once()
        self.assertIn("notification failed", updates.status(self.repo, self.state))

    def test_reserves_notification_before_sending(self):
        def notice(release):
            self.assertEqual(updates.load_state(self.state)["notified_version"], release["version"])
            raise SystemExit()
        self.notice.side_effect = notice
        with self.assertRaises(SystemExit):
            updates.check(self.repo, self.state, now=10000)
        self.notice.side_effect = None
        updates.check(self.repo, self.state, now=14000)
        self.notice.assert_called_once()

    def test_concurrent_check_is_rejected(self):
        with updates.locked(self.state):
            with self.assertRaises(updates.UpdateError):
                updates.check(self.repo, self.state, now=10000)
        self.notice.assert_not_called()

    def test_notification_probe_runs_even_when_feed_is_unavailable(self):
        updates.save_state(self.state, {"test_pending": True})
        self.feed.side_effect = OSError("offline")
        with mock.patch.object(updates, "desktop_notification", return_value=True) as notification:
            updates.check(self.repo, self.state, now=10000)
            updates.check(self.repo, self.state, now=14000)
        notification.assert_called_once()
        self.assertEqual(updates.load_state(self.state)["notification_test"], "submitted")

    def test_invalid_state_does_not_reset_notification_history(self):
        self.state.mkdir()
        (self.state / "updates.json").write_text("{broken")
        with self.assertRaises(updates.UpdateError):
            updates.check(self.repo, self.state)
        self.notice.assert_not_called()
        self.assertEqual((self.state / "updates.json").read_text(), "{broken")

    def test_state_and_status_do_not_touch_recovery_or_preferences(self):
        self.state.mkdir()
        for name in ("state.json", "discord_webhook", "telemetry"):
            (self.state / name).write_text("preserve")
        updates.check(self.repo, self.state, now=10000)
        before = (self.state / "updates.json").read_bytes()
        updates.status(self.repo, self.state)
        self.assertEqual((self.state / "updates.json").read_bytes(), before)
        for name in ("state.json", "discord_webhook", "telemetry"):
            self.assertEqual((self.state / name).read_text(), "preserve")


class ReleaseContractTests(unittest.TestCase):
    def test_bad_metadata_is_rejected(self):
        for key, value in (("version", "1.0.0;touch /tmp/no"), ("version", "01.0.0"),
                           ("version", "1.0.0-beta"), ("important", "yes"), ("commit", "main"),
                           ("notes_url", "https://example.com/update"), ("published_at", "yesterday"),
                           ("summary", "two\nlines"), ("summary", "x" * 201)):
            with self.subTest(key=key, value=value), self.assertRaises(updates.UpdateError):
                updates.validate_feed({"schema": 1, "releases": [{**RELEASE, key: value}]})
        for feed in ({}, {"schema": 2, "releases": []}, {"schema": True, "releases": []},
                     {"schema": 1, "releases": [RELEASE, RELEASE]}):
            with self.assertRaises(updates.UpdateError):
                updates.validate_feed(feed)

    def test_numeric_version_ordering(self):
        self.assertGreater(updates.version("1.10.0"), updates.version("1.9.0"))
        self.assertEqual(updates.validate_feed({"schema": 1, "releases": []}), [])

    def test_version_reader_never_executes_downloaded_code(self):
        self.assertEqual(updates.source_version('raise RuntimeError()\n__version__ = "1.0.0"'), "1.0.0")
        with self.assertRaises(updates.UpdateError):
            updates.source_version('__version__ = str(__import__("os").system("false"))')

    def test_download_is_bounded_and_uses_timeouts(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"x" * (updates.MAX_BYTES + 1)
        with mock.patch.object(updates.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(updates.UpdateError):
                updates.fetch_feed()
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 10)
            response.read.assert_called_once_with(updates.MAX_BYTES + 1)

    def test_redirect_is_not_followed(self):
        with self.assertRaises(updates.UpdateError):
            updates.NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.com")

    def test_desktop_text_is_escaped_not_executed(self):
        with mock.patch.object(updates.osa, "run_jxa", return_value="") as send:
            self.assertTrue(updates.desktop_notification('title"', 'text"; doShellScript("bad")'))
        self.assertIn('text\\"; doShellScript(\\"bad\\")', send.call_args.args[0])
        self.assertEqual(send.call_args.kwargs["timeout"], 10)

    def test_notification_denial_or_timeout_is_not_success(self):
        for error in (updates.osa.OsaError("denied"), subprocess.TimeoutExpired("osascript", 10)):
            with mock.patch.object(updates.osa, "run_jxa", side_effect=error):
                self.assertFalse(updates.desktop_notification("title", "message"))

    def test_announcement_requires_a_public_release_and_does_not_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release.json"
            path.write_text(json.dumps(FEED))
            with mock.patch.object(updates, "published", return_value=False), self.assertRaises(updates.UpdateError):
                updates.announcement(path)
            with mock.patch.object(updates, "published", return_value=True):
                text = updates.announcement(path)
            self.assertIn("./install.sh update", text)
            self.assertIn("git pull --ff-only", text)
            self.assertIn(RELEASE["notes_url"], text)


class LaunchAgentTests(unittest.TestCase):
    def test_job_runs_hourly_without_keepalive_or_recovery_changes(self):
        config = updates.job_config(Path("/tmp/repo with spaces"), Path("/tmp/state"), sys.executable)
        self.assertTrue(config["RunAtLoad"])
        self.assertEqual(config["StartInterval"], 3600)
        self.assertNotIn("KeepAlive", config)
        self.assertEqual(config["ProgramArguments"], [sys.executable, "-m", "immortal.core.updates", "check"])
        self.assertEqual(plistlib.loads(plistlib.dumps(config)), config)

    def test_setup_preserves_state_and_only_loads_update_job(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(updates, "launchctl") as command:
            home = Path(tmp)
            state = home / "state"
            updates.save_state(state, {"notified_version": "0.2.0"})
            command.return_value = mock.Mock(returncode=1)
            updates.install_job(ROOT, state, sys.executable, home)
            self.assertTrue(updates.job_path(home).is_file())
            self.assertEqual(updates.load_state(state)["notified_version"], "0.2.0")
            self.assertTrue(updates.load_state(state)["test_pending"])
            self.assertIn("bootstrap", str(command.call_args_list))
            self.assertNotIn("watcher", str(command.call_args_list))
            updates.remove_job(home)
            self.assertFalse(updates.job_path(home).exists())
            self.assertTrue((state / "updates.json").exists())

    def test_notification_test_is_requested_from_launchd_not_inline(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(updates, "launchctl") as command, \
                mock.patch.object(updates, "desktop_notification") as notification:
            updates.test_notification(Path(tmp))
            self.assertTrue(updates.load_state(tmp)["test_pending"])
            command.assert_called_once_with("kickstart", f"gui/{os.getuid()}/{updates.LABEL}")
            notification.assert_not_called()


class UpdateCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = self.tmp / "repo with spaces"
        self.repo.mkdir()
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
                        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
        self.git("init", "-b", "main")
        self.git("remote", "add", "origin", updates.PUBLIC_REPO + ".git")
        (self.repo / "immortal" / "core").mkdir(parents=True)
        (self.repo / "immortal" / "__init__.py").write_text('__version__ = "0.1.0"\n')
        (self.repo / "watcher.py").write_text("pass\n")
        (self.repo / "install.sh").write_text("true\n")
        (self.repo / "immortal" / "core" / "updates.py").write_text("pass\n")
        (self.repo / "immortal" / "core" / "updater.py").write_text("pass\n")
        (self.repo / "immortal/core/install_migrations.py").write_text(
            "from pathlib import Path\nimport sys\n"
            "home = Path(sys.argv[sys.argv.index('--home') + 1])\n"
            "if (home / 'fail-migration').exists(): raise SystemExit('fixture migration failed')\n"
            "(home / 'migration-completed').write_text('yes')\n"
            "print('fixture components ready')\n")
        self.commit("initial")
        self.before = self.git("rev-parse", "HEAD")
        self.home = self.tmp / "home"
        plist = self.home / "Library" / "LaunchAgents" / (updater.WATCHER_LABEL + ".plist")
        plist.parent.mkdir(parents=True)
        plist.write_bytes(plistlib.dumps({"Label": updater.WATCHER_LABEL,
            "ProgramArguments": [sys.executable, str(self.repo / "watcher.py")],
            "WorkingDirectory": str(self.repo), "EnvironmentVariables": {"WATCHER_STATE_DIR": str(self.state)}}))
        for name in ("state.json", "telemetry", "discord_webhook"):
            (self.state / name).write_text("preserve")
        self.real_restart = updater.restart_watcher
        self.restart = self.enterContext(mock.patch.object(updater, "restart_watcher"))
        self.feed = self.enterContext(mock.patch.object(updates, "fetch_feed", return_value=copy.deepcopy(FEED)))
        self.public = self.enterContext(mock.patch.object(updates, "published", return_value=True))

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)

    def release(self):
        self.git("switch", "-c", "release-candidate")
        (self.repo / "immortal" / "__init__.py").write_text('__version__ = "0.2.0"\n')
        self.commit("release")
        target = self.git("rev-parse", "HEAD")
        self.git("tag", "v0.2.0")
        self.git("switch", "main")
        self.feed.return_value["releases"][0]["commit"] = target
        self.git("fetch", "--no-tags", str(self.repo), "refs/tags/v0.2.0")
        return target

    def apply(self):
        real_git = updater.git
        def local_git(repo, *args):
            if args[0] == "fetch":
                self.assertEqual(args, ("fetch", "--no-tags", "origin", "refs/tags/v0.2.0"))
                return ""
            return real_git(repo, *args)
        with mock.patch.object(updater, "git", side_effect=local_git):
            return updater.apply(self.repo, self.state, self.home)

    def test_tagged_release_fast_forwards_without_touching_preferences(self):
        target = self.release()
        self.assertIn("restart confirmed", self.apply())
        self.assertEqual(self.git("rev-parse", "HEAD"), target)
        self.restart.assert_called_once_with(self.state)
        for name in ("state.json", "telemetry", "discord_webhook"):
            self.assertEqual((self.state / name).read_text(), "preserve")
        self.assertFalse(updates.load_state(self.state)["restart_required"])

    def test_component_migration_runs_after_checkout_and_before_restart(self):
        target = self.release()
        def restart(state_dir):
            self.assertEqual(self.git("rev-parse", "HEAD"), target)
            self.assertTrue((self.home / "migration-completed").exists())
        self.restart.side_effect = restart
        self.assertIn("fixture components ready", self.apply())
        self.assertFalse(updates.load_state(self.state)["migration_required"])

    def test_component_failure_is_retryable_at_same_repo_version(self):
        target = self.release()
        (self.home / "fail-migration").touch()
        with self.assertRaisesRegex(updates.UpdateError, "Component migration failed"):
            self.apply()
        self.assertEqual(self.git("rev-parse", "HEAD"), target)
        self.restart.assert_not_called()
        self.assertTrue(updates.load_state(self.state)["migration_required"])
        (self.home / "fail-migration").unlink()
        self.assertIn("confirmed", self.apply())
        self.assertFalse(updates.load_state(self.state)["migration_required"])

    def test_same_version_update_runs_component_migration(self):
        self.release()
        self.apply()
        (self.home / "migration-completed").unlink()
        self.restart.reset_mock()
        self.assertIn("fixture components ready", self.apply())
        self.assertTrue((self.home / "migration-completed").exists())
        self.restart.assert_not_called()

    def test_user_edit_during_migration_is_preserved_without_restart(self):
        target = self.release()
        real_run = subprocess.run
        def edit_during_migration(command, **kwargs):
            result = real_run(command, **kwargs)
            if "immortal.core.install_migrations" in command:
                (self.repo / "watcher.py").write_text("# concurrent user edit\n")
            return result
        with mock.patch.object(updater.subprocess, "run", side_effect=edit_during_migration):
            with self.assertRaisesRegex(updates.UpdateError, "Checkout changed during update"):
                self.apply()
        self.assertEqual((self.repo / "watcher.py").read_text(), "# concurrent user edit\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), target)
        self.restart.assert_not_called()
        state = updates.load_state(self.state)
        self.assertTrue(state["migration_required"])
        self.assertTrue(state["restart_required"])
        self.assertNotIn("installed_commit", state)

    def test_same_version_concurrent_commit_cannot_be_marked_migrated(self):
        target = self.release()
        self.apply()
        self.restart.reset_mock()
        real_run = subprocess.run
        def commit_during_migration(command, **kwargs):
            result = real_run(command, **kwargs)
            if "immortal.core.install_migrations" in command:
                (self.repo / "user-work.txt").write_text("preserve this commit")
                self.commit("concurrent user work")
            return result
        with mock.patch.object(updater.subprocess, "run", side_effect=commit_during_migration):
            with self.assertRaisesRegex(updates.UpdateError, "commit changed"):
                self.apply()
        self.assertNotEqual(self.git("rev-parse", "HEAD"), target)
        self.assertEqual((self.repo / "user-work.txt").read_text(), "preserve this commit")
        self.assertTrue(updates.load_state(self.state)["migration_required"])
        self.assertEqual(updates.load_state(self.state)["migration_commit"], target)
        self.restart.assert_not_called()

    def test_component_timeout_remains_retryable_without_claiming_success(self):
        target = self.release()
        real_run = subprocess.run
        def timeout_migration(command, **kwargs):
            if "immortal.core.install_migrations" in command:
                raise subprocess.TimeoutExpired(command, 900)
            return real_run(command, **kwargs)
        with mock.patch.object(updater.subprocess, "run", side_effect=timeout_migration):
            with self.assertRaisesRegex(updates.UpdateError, "migration timed out"):
                self.apply()
        state = updates.load_state(self.state)
        self.assertTrue(state["migration_required"])
        self.assertTrue(state["restart_required"])
        self.assertEqual(self.git("rev-parse", "HEAD"), target)
        self.restart.assert_not_called()
        self.assertIn("confirmed", self.apply())
        self.assertFalse(updates.load_state(self.state)["migration_required"])

    def test_missing_new_release_migration_is_reported(self):
        self.git("switch", "-c", "release-candidate")
        (self.repo / "immortal/__init__.py").write_text('__version__ = "0.2.0"\n')
        (self.repo / "immortal/core/install_migrations.py").unlink()
        self.commit("invalid release without migration")
        self.feed.return_value["releases"][0]["commit"] = self.git("rev-parse", "HEAD")
        self.git("tag", "v0.2.0")
        self.git("switch", "main")
        self.git("fetch", "--no-tags", str(self.repo), "refs/tags/v0.2.0")
        with self.assertRaisesRegex(updates.UpdateError, "missing component migrations"):
            self.apply()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before)
        self.restart.assert_not_called()

    def test_dirty_checkout_or_unknown_remote_is_refused_before_fetch(self):
        (self.repo / "untracked").write_text("mine")
        with self.assertRaises(updates.UpdateError):
            self.apply()
        self.feed.assert_not_called()
        (self.repo / "untracked").unlink()
        self.git("remote", "set-url", "origin", "https://example.com/wrong.git")
        with self.assertRaises(updates.UpdateError):
            self.apply()
        self.feed.assert_not_called()
        self.restart.assert_not_called()

    def test_other_branches_and_linked_worktrees_are_refused(self):
        self.git("switch", "-c", "work")
        with self.assertRaises(updates.UpdateError):
            self.apply()
        self.git("switch", "main")
        linked = self.tmp / "linked"
        self.git("worktree", "add", str(linked), "work")
        with self.assertRaises(updates.UpdateError):
            updater.check_checkout(linked)

    def test_wrong_installed_checkout_is_refused(self):
        with self.assertRaises(updates.UpdateError):
            updater.watcher_config(self.tmp, self.state, self.home)
        self.restart.assert_not_called()

    def test_shallow_installer_clone_can_fetch_and_apply_a_new_release(self):
        target = self.release()
        source = self.repo
        clone = self.tmp / "shallow"
        self.git("clone", "--depth", "1", "--branch", "main", source.as_uri(), str(clone))
        self.repo = clone
        self.assertEqual(self.git("rev-parse", "--is-shallow-repository"), "true")
        self.git("remote", "set-url", "origin", updates.PUBLIC_REPO + ".git")
        plist = self.home / "Library" / "LaunchAgents" / (updater.WATCHER_LABEL + ".plist")
        config = plistlib.loads(plist.read_bytes())
        config["WorkingDirectory"] = str(clone)
        config["ProgramArguments"][1] = str(clone / "watcher.py")
        plist.write_bytes(plistlib.dumps(config))
        real_git = updater.git
        def local_git(repo, *args):
            if args[0] == "fetch":
                return real_git(repo, "fetch", "--no-tags", source.as_uri(), "refs/tags/v0.2.0")
            return real_git(repo, *args)
        with mock.patch.object(updater, "git", side_effect=local_git):
            self.assertIn("confirmed", updater.apply(clone, self.state, self.home))
        self.assertEqual(self.git("rev-parse", "HEAD"), target)

    def test_release_cannot_overwrite_an_ignored_local_file(self):
        (self.repo / ".gitignore").write_text("settings.txt\n")
        self.commit("ignore local settings")
        self.git("switch", "-c", "release-candidate")
        (self.repo / "immortal" / "__init__.py").write_text('__version__ = "0.2.0"\n')
        (self.repo / "settings.txt").write_text("release defaults")
        self.git("add", "-f", "settings.txt")
        self.commit("release")
        target = self.git("rev-parse", "HEAD")
        self.git("tag", "v0.2.0")
        self.git("switch", "main")
        (self.repo / "settings.txt").write_text("local preferences")
        self.git("fetch", "--no-tags", str(self.repo), "refs/tags/v0.2.0")
        self.feed.return_value["releases"][0]["commit"] = target
        with self.assertRaises(updates.UpdateError):
            self.apply()
        self.assertEqual((self.repo / "settings.txt").read_text(), "local preferences")
        self.restart.assert_not_called()

    def test_offline_update_leaves_code_and_watcher_untouched(self):
        self.feed.side_effect = OSError("offline")
        with self.assertRaises(OSError):
            self.apply()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before)
        self.restart.assert_not_called()

    def test_stale_pid_log_cannot_confirm_a_restart(self):
        log = self.state / "watcher.log"
        log.write_text(json.dumps({"event": "start", "pid": 123, "ts": "2026-09-05T10:00:00Z"}) + "\n")
        since = int(updates.time.time())
        self.assertFalse(updater.started(self.state, 123, since))
        from datetime import datetime, timezone
        log.write_text(json.dumps({"event": "start", "pid": 123,
                                  "ts": datetime.fromtimestamp(since, timezone.utc).isoformat()}) + "\n")
        self.assertTrue(updater.started(self.state, 123, since))
        self.assertFalse(updater.started(self.state, 456, since))

    def test_commit_mismatch_never_changes_checkout(self):
        self.release()
        self.feed.return_value["releases"][0]["commit"] = "b" * 40
        with self.assertRaises(updates.UpdateError):
            self.apply()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before)
        self.restart.assert_not_called()

    def test_local_commits_are_not_overwritten_or_merged(self):
        self.release()
        (self.repo / "local").write_text("mine")
        self.commit("local work")
        head = self.git("rev-parse", "HEAD")
        with self.assertRaises(updates.UpdateError):
            self.apply()
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.restart.assert_not_called()

    def test_unpublished_release_is_refused(self):
        self.release()
        self.public.return_value = False
        with self.assertRaises(updates.UpdateError):
            self.apply()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before)

    def test_restart_failure_is_reported_and_can_be_retried(self):
        target = self.release()
        self.restart.side_effect = updates.UpdateError("startup failed")
        with self.assertRaises(updates.UpdateError):
            self.apply()
        self.assertEqual(self.git("rev-parse", "HEAD"), target)
        self.assertTrue(updates.load_state(self.state)["restart_required"])
        self.restart.side_effect = None
        self.assertIn("confirmed", self.apply())
        self.assertFalse(updates.load_state(self.state)["restart_required"])

    def test_restart_does_not_claim_success_without_new_process_and_start_log(self):
        with mock.patch.object(updates, "launchctl") as command, \
                mock.patch.object(updater, "process_id", return_value=123), \
                mock.patch.object(updater.time, "monotonic", side_effect=[0, 11]):
            with self.assertRaises(updates.UpdateError):
                self.real_restart(self.state)
        command.assert_called_once_with("kickstart", "-k", f"gui/{os.getuid()}/{updater.WATCHER_LABEL}")


if __name__ == "__main__":
    unittest.main()
