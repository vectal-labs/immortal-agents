import copy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from immortal.core import codex_recovery as recovery
from immortal.core.codex_recovery import activation, package, verification


class ManagedCodexTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.root = recovery.location(self.home)
        self.platform = patch.object(package, "check_platform")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.guard = patch.object(activation, "check_no_downgrade")
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.fixture()

    def fixture(self, revision=1, upstream="0.153.4", entries=None):
        executable = (f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "codex-cli {upstream}"; else printf "command worked\\n"; fi\n').encode()
        contents = {"bin/codex": (executable, 0o755), "LICENSE": (b"license\n", 0o644), "EMPTY": (b"", 0o644)}
        archive = self.base / f"package-{revision}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for name, (data, mode) in contents.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), mode
                bundle.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo("codex")
            link.type, link.linkname = tarfile.SYMTYPE, "bin/codex"
            bundle.addfile(link)
            for info, data in entries or []:
                bundle.addfile(info, io.BytesIO(data))
        source = self.base / "source.tar.gz"
        source.write_bytes(b"source archive fixture")
        self.manifest = {
            "schema_version": 1, "version": f"{upstream}+wake.{revision}", "upstream_version": upstream,
            "upstream_commit": "a" * 40, "platform": "darwin-arm64", "min_macos": "15.0", "entrypoint": "bin/codex",
            "artifact": {"url": archive.as_uri(), "sha256": package.digest(archive), "size": archive.stat().st_size},
            "source": {"url": source.as_uri(), "sha256": package.digest(source), "size": source.stat().st_size},
            "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "mode": mode} for name, (data, mode) in contents.items()},
            "symlinks": {"codex": "bin/codex"}, "patches": [],
            "release": {"channel": "local", "notarized": False, "signing_team_id": None, "validation": {"physical_wake": False, "clean_mac": False}},
        }
        self.path = self.base / f"manifest-{revision}.json"
        self.save()
        return self.path

    def save(self):
        self.path.write_text(json.dumps(self.manifest))

    def install(self):
        return recovery.install(self.repo, self.home, self.path)

    def test_install_real_executable_status_and_uninstall_preserve_user_files(self):
        profile = self.home / ".zshrc"
        profile.write_text("# existing settings\nexport ORIGINAL=1\n")
        credentials = self.home / ".codex/auth.json"
        credentials.parent.mkdir()
        credentials.write_text("private fixture")
        original = self.home / ".bun/bin/codex"
        original.parent.mkdir(parents=True)
        original.symlink_to("untouched-package-manager")
        self.assertIn("Developer-only", self.install())
        self.assertIn("package verified", recovery.status(self.home))
        result = package.subprocess.run([str(self.root / "bin/codex"), "exec"], capture_output=True, text=True)
        self.assertEqual(result.stdout, "command worked\n")
        profile.write_text(profile.read_text() + "# later user addition\n")
        self.assertIn("removed", recovery.rollback(self.home))
        self.assertEqual(profile.read_text(), "# existing settings\nexport ORIGINAL=1\n# later user addition\n")
        self.assertFalse((self.home / ".zprofile").exists())
        self.assertEqual(os.readlink(original), "untouched-package-manager")
        self.assertEqual(credentials.read_text(), "private fixture")
        self.assertFalse(recovery.load_state(self.root)["enabled"])

    def test_idempotent_install_then_update_and_rollback(self):
        self.install()
        self.install()
        self.assertEqual((self.home / ".zprofile").read_text().count(activation.BEGIN), 1)
        self.fixture(2)
        self.install()
        self.assertEqual(recovery.load_state(self.root)["version"], "0.153.4+wake.2")
        self.assertIn("wake.1", recovery.rollback(self.home))
        self.assertTrue((self.root / "bin/codex").exists())
        self.assertIn("removed", recovery.rollback(self.home))

    def test_update_requires_prior_opt_in_and_local_not_auto_upgraded(self):
        self.assertIn("not enabled", recovery.update_if_enabled(self.repo, self.home))
        self.assertFalse(self.root.exists())
        self.install()
        self.assertIn("Developer-only", recovery.update_if_enabled(self.repo, self.home))

    def test_unpublished_manifest_fails_before_creating_state(self):
        (self.repo / "codex-release.json").write_text('{"schema_version":1,"available":false}')
        with self.assertRaisesRegex(recovery.RecoveryError, "No stable"):
            recovery.install(self.repo, self.home)
        self.assertFalse(self.root.exists())

    def test_developer_package_cannot_use_normal_installer(self):
        (self.repo / "codex-release.json").write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(recovery.RecoveryError, "stable release"):
            recovery.install(self.repo, self.home)

    def test_hash_failure_keeps_old_version_selected(self):
        self.install()
        before = (self.root / "managed-state.json").read_bytes()
        self.fixture(2)
        self.manifest["artifact"]["sha256"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(recovery.RecoveryError, "checksum"):
            self.install()
        self.assertEqual((self.root / "managed-state.json").read_bytes(), before)
        self.assertEqual(os.readlink(self.root / "current"), "packages/0.153.4+wake.1")

    def test_source_hash_failure_never_enables(self):
        self.manifest["source"]["sha256"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(recovery.RecoveryError, "checksum"):
            self.install()
        self.assertFalse((self.root / "managed-state.json").exists())
        self.assertFalse((self.home / ".zprofile").exists())

    def test_http_trickle_checks_deadline_before_waiting_for_entire_body(self):
        reader, writer = socket.socketpair()
        reader.settimeout(3)
        release = threading.Event()
        stopped = threading.Event()
        body_finished = threading.Event()
        data = b"x" * 128

        def serve():
            try:
                writer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 128\r\n\r\nx")
                # A filling read waits for the rest; read1 exposes the first byte.
                release.wait(1)
                if not stopped.is_set():
                    writer.sendall(data[1:])
                    body_finished.set()
            except OSError:
                pass
            finally:
                writer.close()

        thread = threading.Thread(target=serve)
        thread.start()
        response = http.client.HTTPResponse(reader)
        try:
            response.begin()
            info = {"url": "https://github.com/vectal-labs/immortal-agents/releases/download/v0.2.0/package.tar.gz",
                    "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            opener = SimpleNamespace(open=lambda *_args, **_kwargs: response)
            with patch.object(package, "build_opener", return_value=opener), patch.object(package.time, "monotonic", side_effect=[0, 0, 0.2]):
                with self.assertRaisesRegex(recovery.RecoveryError, "time limit"):
                    package.download(info, self.base / "partial.tar.gz", local=False, budget=0.1)
            self.assertFalse(body_finished.is_set(), "Downloader waited for the full HTTP body before enforcing its deadline")
        finally:
            stopped.set()
            release.set()
            response.close()
            reader.close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_newer_recovery_revision_refuses_downgrade(self):
        self.fixture(2)
        self.install()
        self.fixture(1)
        with self.assertRaisesRegex(recovery.RecoveryError, "revision is newer"):
            self.install()

    def test_profile_drift_prevents_update_and_rollback(self):
        self.install()
        profile = self.home / ".zprofile"
        profile.write_text(profile.read_text().replace('export PATH=', '# changed export PATH='))
        self.fixture(2)
        for operation in (self.install, lambda: recovery.rollback(self.home)):
            with self.assertRaisesRegex(recovery.RecoveryError, "PATH block changed"):
                operation()
        self.assertEqual(os.readlink(self.root / "current"), "packages/0.153.4+wake.1")

    def test_launcher_drift_never_overwritten(self):
        self.install()
        launcher = self.root / "bin/codex"
        launcher.unlink()
        launcher.write_text("external owner")
        with self.assertRaisesRegex(recovery.RecoveryError, "launcher changed"):
            self.install()
        self.assertEqual(launcher.read_text(), "external owner")

    def test_drift_during_download_prevents_activation(self):
        self.install()
        self.fixture(2)
        prepare = recovery.prepare
        def drift(*args):
            result = prepare(*args)
            (self.root / "current").unlink()
            (self.root / "current").symlink_to("external-choice")
            return result
        with patch.object(recovery, "prepare", side_effect=drift):
            with self.assertRaisesRegex(recovery.RecoveryError, "selection changed"):
                self.install()
        self.assertEqual(os.readlink(self.root / "current"), "external-choice")

    def test_profile_write_failure_restores_prior_profiles(self):
        (self.home / ".zprofile").write_text("original\n")
        original = activation.atomic
        def fail(path, data, mode=0o600):
            if Path(path).name == ".zshrc":
                raise OSError("fixture write failure")
            return original(path, data, mode)
        with patch.object(activation, "atomic", side_effect=fail):
            with self.assertRaisesRegex(OSError, "fixture"):
                self.install()
        self.assertEqual((self.home / ".zprofile").read_text(), "original\n")
        self.assertFalse((self.root / "current").exists())
        self.assertFalse((self.root / "managed-state.json").exists())

    def test_state_write_failure_restores_selection_and_profiles(self):
        self.install()
        self.fixture(2)
        before = (self.root / "managed-state.json").read_bytes()
        original = activation.write_json
        def fail(path, data):
            if Path(path).name == "managed-state.json":
                raise OSError("state fixture failure")
            return original(path, data)
        with patch.object(activation, "write_json", side_effect=fail):
            with self.assertRaisesRegex(OSError, "fixture"):
                self.install()
        self.assertEqual(os.readlink(self.root / "current"), "packages/0.153.4+wake.1")
        self.assertEqual((self.root / "managed-state.json").read_bytes(), before)

    def test_existing_bash_fallback_not_suppressed(self):
        fallback = self.home / ".profile"
        fallback.write_text("export PREEXISTING=1\n")
        self.install()
        self.assertFalse((self.home / ".bash_profile").exists())
        self.assertIn(activation.BEGIN, fallback.read_text())
        recovery.rollback(self.home)
        self.assertEqual(fallback.read_text(), "export PREEXISTING=1\n")

    def test_no_existing_bash_profile_restored_to_absent(self):
        self.install()
        self.assertTrue((self.home / ".bash_profile").exists())
        recovery.rollback(self.home)
        self.assertFalse((self.home / ".bash_profile").exists())

    def test_modified_installed_binary_and_extra_file_are_detected(self):
        self.install()
        target = self.root / "current"
        (target / "unexpected").write_text("extra")
        with self.assertRaisesRegex(recovery.RecoveryError, "Unexpected installed"):
            recovery.status(self.home)
        (target / "unexpected").unlink()
        (target / "bin/codex").write_text("tampered")
        with self.assertRaisesRegex(recovery.RecoveryError, "verification failed"):
            recovery.status(self.home)

    def test_archive_rejects_traversal_duplicate_hardlink_and_unknown(self):
        attacks = []
        for name in ("../escape", "bin/codex", "unexpected"):
            info = tarfile.TarInfo(name)
            info.size, info.mode = 1, 0o644
            attacks.append((info, b"x"))
        info = tarfile.TarInfo("evil-link")
        info.type, info.linkname = tarfile.LNKTYPE, "bin/codex"
        attacks.append((info, b""))
        for index, attack in enumerate(attacks, 10):
            with self.subTest(attack=attack[0].name):
                self.fixture(index, entries=[attack])
                with self.assertRaises(recovery.RecoveryError):
                    self.install()
                self.assertFalse((self.root / "current").exists())
                self.assertFalse((self.base / "escape").exists())

    def test_manifest_rejects_escaping_symlink_and_file_parent(self):
        self.manifest["symlinks"]["codex"] = "../escape"
        with self.assertRaisesRegex(recovery.RecoveryError, "escapes"):
            package.validate(self.manifest, local=True)
        self.manifest["symlinks"]["codex"] = "bin/codex"
        self.manifest["files"]["codex/child"] = self.manifest["files"]["LICENSE"]
        with self.assertRaisesRegex(recovery.RecoveryError, "used as directory"):
            package.validate(self.manifest, local=True)

    def test_stable_requires_validation_and_public_repo_urls(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["release"]["channel"] = "stable"
        with self.assertRaisesRegex(recovery.RecoveryError, "validation is incomplete"):
            package.validate(manifest)
        manifest["release"].update(notarized=True, signing_team_id="ABCDEFGHIJ", validation={"physical_wake": True, "clean_mac": True})
        with self.assertRaisesRegex(recovery.RecoveryError, "HTTPS GitHub"):
            package.validate(manifest)
        for name in ("source", "artifact"):
            manifest[name]["url"] = f"https://github.com/vectal-labs/immortal-agents/releases/download/v0.2.0/{name}.tar.gz"
        package.validate(manifest)
        for unsafe in ("http://github.com/vectal-labs/immortal-agents/releases/download/v0.2.0/a", "https://github.com/evil/repo/releases/download/v0.2.0/a", "https://github.com@evil.com/a", "file:///tmp/a"):
            with self.subTest(url=unsafe), self.assertRaises(recovery.RecoveryError):
                package.validate_url(unsafe, initial=True)
        with self.assertRaises(recovery.RecoveryError):
            package.validate_url("https://evil.com/asset")

    def test_concurrent_install_refused(self):
        with recovery.locked(self.root):
            with self.assertRaisesRegex(recovery.RecoveryError, "Another Codex"):
                self.install()

    def test_unsupported_platform_before_side_effects(self):
        self.platform.stop()
        with patch.object(package.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(recovery.RecoveryError, "Apple Silicon"):
                self.install()
        self.assertFalse(self.root.exists())

    def test_version_check_failure_never_activates(self):
        with patch.object(package.subprocess, "run", return_value=package.subprocess.CompletedProcess([], 0, "wrong version", "")):
            with self.assertRaisesRegex(recovery.RecoveryError, "version startup"):
                self.install()
        self.assertFalse((self.root / "managed-state.json").exists())

    def test_inactive_broken_install_does_not_block_selected_good_codex(self):
        self.guard.stop()
        selected = self.base / "selected-codex"
        selected.write_text("fixture")
        dormant = self.home / ".npm-global/bin/codex"
        dormant.parent.mkdir(parents=True)
        dormant.write_text("broken")
        def version(command, **kwargs):
            return package.subprocess.CompletedProcess(command, 0 if command[0] == str(selected) else 1, "codex-cli 0.153.4" if command[0] == str(selected) else "", "")
        with patch.object(activation.shutil, "which", return_value=str(selected)), patch.object(activation.subprocess, "run", side_effect=version):
            activation.check_no_downgrade(self.root, "0.153.4", {})

    def test_active_unknown_or_newer_codex_blocks_install(self):
        self.guard.stop()
        selected = self.base / "selected-codex"
        selected.write_text("fixture")
        for code, output in ((1, ""), (0, "codex-cli 0.154.0")):
            with self.subTest(output=output), patch.object(activation.shutil, "which", return_value=str(selected)), patch.object(activation.subprocess, "run", return_value=package.subprocess.CompletedProcess([], code, output, "")):
                with self.assertRaises(recovery.RecoveryError):
                    activation.check_no_downgrade(self.root, "0.153.4", {})

    def test_managed_upstream_downgrade_blocks_install(self):
        self.fixture(upstream="0.154.0")
        self.install()
        self.guard.stop()
        with self.assertRaisesRegex(recovery.RecoveryError, "managed Codex is newer"):
            activation.check_no_downgrade(self.root, "0.153.4", recovery.load_state(self.root))

    def test_bb_verification_requires_actual_path_and_hash(self):
        self.install()
        host = self.home / ".bb/host-id"
        host.parent.mkdir()
        host.write_text("host_fixture")
        expected = self.manifest["files"]["bin/codex"]["sha256"]
        selected = str(self.root / "bin/codex")
        response = package.subprocess.CompletedProcess([], 0, json.dumps({"codex": {"executablePath": selected}}), "")
        with patch.object(verification.shutil, "which", return_value="/fixture/bb"), patch.object(verification.subprocess, "run", return_value=response) as run:
            self.assertIn("hash verified", verification.bb_selection(self.home, self.root, expected))
            self.assertEqual(run.call_args.args[0], ["/fixture/bb", "machine", "provider-cli", "status", "host_fixture", "--json"])
            self.assertIn("another executable", verification.bb_selection(self.home, self.root, "0" * 64))

    def test_login_shell_timeout_is_not_reported_as_active(self):
        self.install()
        with patch.object(verification.subprocess, "run", side_effect=package.subprocess.TimeoutExpired("zsh", 20)):
            self.assertIn("could not verify", verification.login_shell(self.home, self.root, self.manifest["files"]["bin/codex"]["sha256"]))

    def test_signature_failure_never_activates(self):
        with patch.object(package, "check_signature", side_effect=recovery.RecoveryError("signature fixture")):
            with self.assertRaisesRegex(recovery.RecoveryError, "signature"):
                self.install()
        self.assertFalse((self.root / "current").exists())

    def test_stable_signature_check_requires_online_ticket_and_team(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["release"]["signing_team_id"] = "ABCDEFGHIJ"
        good = package.subprocess.CompletedProcess([], 0, "", "")
        with patch.object(package.subprocess, "run", return_value=good) as run:
            package.check_signature(self.base, manifest, local=False)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertIn("--check-notarization", commands[0])
        self.assertIn('anchor apple generic and certificate leaf[subject.OU] = "ABCDEFGHIJ" and notarized', commands[0])
        self.assertEqual(commands[1][:4], ("/usr/sbin/spctl", "--assess", "--type", "execute"))

    def test_custom_zdotdir_refused_before_profile_mutation(self):
        with patch.dict(os.environ, {"ZDOTDIR": str(self.base / "other-home")}), self.assertRaisesRegex(recovery.RecoveryError, "ZDOTDIR"):
            self.install()
        self.assertFalse((self.home / ".zprofile").exists())

    def test_ancestor_symlink_refused_before_any_outside_write(self):
        other = self.base / "other"
        other.mkdir()
        (self.home / ".local").symlink_to(other)
        with self.assertRaisesRegex(recovery.RecoveryError, "ancestors"):
            self.install()
        self.assertEqual(list(other.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
