"""Distribution archives and stable-release gates, with no network or signing."""

import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from immortal.core.codex_recovery.package import extract

SCRIPT = Path(__file__).resolve().parents[1] / "ops/codex-release/release.py"
SPEC = importlib.util.spec_from_file_location("codex_release", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class CodexReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "package"
        self.package.mkdir()
        for name in release.EXECUTABLES:
            binary = self.package / name
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b"executable " + name.encode())
            binary.chmod(0o755)
        release.write_json(self.package / "codex-package.json", {
            "version": release.INPUTS["version"], "target": release.INPUTS["target"]})

    def test_archive_is_reproducible_and_preserves_flat_layout(self):
        first, second = self.root / "a.tar.gz", self.root / "b.tar.gz"
        release.archive(self.package, first)
        for file in self.package.rglob("*"):
            os.utime(file, (100000, 100000))
        release.archive(self.package, second)
        self.assertEqual(release.sha(first), release.sha(second))
        with tarfile.open(first) as tar:
            self.assertEqual(tar.extractfile("bin/codex").read(), b"executable bin/codex")
            self.assertEqual(tar.getmember("bin/codex").mode, 0o755)
            self.assertTrue(all(m.uid == 0 and m.mtime == 0 for m in tar.getmembers()))

    def test_symlinks_are_separate_and_cannot_escape(self):
        link = self.package / "bin/helper"
        link.symlink_to("codex-code-mode-host")
        files, links = release.inventory(self.package)
        self.assertNotIn("bin/helper", files)
        self.assertEqual(links, {"bin/helper": "codex-code-mode-host"})
        link.unlink()
        link.symlink_to("../../outside")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            release.archive(self.package, self.root / "bad.tar.gz")

    def test_dangling_links_and_special_files_rejected(self):
        link = self.package / "missing"
        link.symlink_to("no-such-file")
        with self.assertRaisesRegex(ValueError, "Dangling"):
            release.inventory(self.package)
        link.unlink()
        os.mkfifo(self.package / "pipe")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            release.inventory(self.package)

    def test_extract_source_rejects_traversal_before_writing(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as tar:
            member = tarfile.TarInfo("../outside")
            member.size = 1
            tar.addfile(member, io.BytesIO(b"x"))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            release.extract_source(data.getvalue(), self.root / "source")
        self.assertFalse((self.root / "outside").exists())

    def test_minimum_os_includes_helpers(self):
        def command(*args, **kwargs):
            if args[0].endswith("file"):
                return "Mach-O 64-bit executable arm64"
            return "minos 15.0" if args[-1].endswith("zsh") else "minos 11.0"
        with patch.object(release, "run", side_effect=command):
            self.assertEqual(release.minimum_macos(self.package), "15.0")

    def prepared(self, build_provenance=False):
        output = self.root / "artifacts"
        def source(checkout, destination):
            destination.mkdir()
            (destination / "LICENSE").write_text("upstream license")
            (destination / "NOTICE").write_text("upstream notice")
            (destination / "changed.rs").write_text("patched source")
            return destination
        if build_provenance:
            fixture = source(self.root, self.root / "fixture-source")
            release.write_json(self.package / "build-provenance.json", {
                "inputs": release.INPUTS, "source_sha256": release.source_fingerprint(fixture)})
        with patch.object(release, "minimum_macos", return_value="15.0"), \
                patch.object(release, "prepare_source", side_effect=source):
            manifest = release.prepare(self.package, self.root, output)
        return output, manifest

    def test_local_manifest_contains_actual_archive_and_file_hashes(self):
        output, manifest = self.prepared()
        self.assertEqual(manifest["release"]["channel"], "local")
        self.assertFalse(manifest["release"]["notarized"])
        self.assertEqual(manifest["files"]["bin/codex"]["sha256"], release.sha(self.package / "bin/codex"))
        for asset in ("artifact", "source"):
            archive = output / Path(unquote(urlsplit(manifest[asset]["url"]).path)).name
            self.assertEqual(release.sha(archive), manifest[asset]["sha256"])
            self.assertEqual(archive.stat().st_size, manifest[asset]["size"])
            self.assertTrue(manifest[asset]["url"].startswith("file://"))
        self.assertIn("LICENSE", manifest["files"])
        with tarfile.open(output / Path(unquote(urlsplit(manifest["source"]["url"]).path)).name) as tar:
            self.assertEqual(tar.extractfile("changed.rs").read(), b"patched source")

    def test_public_promotion_rejects_missing_evidence_without_running_signing(self):
        output, manifest = self.prepared()
        evidence = output / "evidence.json"
        release.write_json(evidence, {"artifact_sha256": manifest["artifact"]["sha256"]})
        with patch.object(release, "run") as command:
            with self.assertRaisesRegex(ValueError, "physical_wake"):
                release.promote(output / "manifest.json", self.package, evidence,
                                release.PUBLIC_BASE + "v0.2.0", "0123456789")
            command.assert_not_called()
        self.assertFalse((output / "stable-manifest.json").exists())

    def test_public_promotion_rejects_evidence_for_other_binary(self):
        output, manifest = self.prepared()
        evidence = output / "evidence.json"
        release.write_json(evidence, {"artifact_sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "exact archive"):
            release.promote(output / "manifest.json", self.package, evidence,
                            release.PUBLIC_BASE + "v0.2.0", "0123456789")

    def test_public_url_cannot_redirect_assets_to_other_repository(self):
        output, _ = self.prepared()
        evidence = output / "evidence.json"
        release.write_json(evidence, {})
        with self.assertRaisesRegex(ValueError, "pinned repository"):
            release.promote(output / "manifest.json", self.package, evidence,
                            "https://example.com/v0.2.0", "0123456789")

    def test_extracted_final_archive_passes_promotion_only_with_verification(self):
        output, manifest = self.prepared(build_provenance=True)
        evidence = output / "evidence.json"
        records = {key: {"passed": True, "report_sha256": "b" * 64, "reviewer": "fixture"}
                   for key in ("physical_wake", "clean_mac", "e2e", "licenses_reviewed")}
        release.write_json(evidence, {**records, "artifact_sha256": manifest["artifact"]["sha256"],
                                     "notarization": {"status": "Accepted", "id": "fixture-id",
                                                      "submission_sha256": "c" * 64}})
        extracted = self.root / "extracted"
        extract(next(output.glob("*-darwin-arm64.tar.gz")), extracted, manifest)
        def verify(args, **kwargs):
            class Result:
                stderr = ("TeamIdentifier=0123456789\n" if args[0].endswith("codesign")
                          else "accepted\nsource=Notarized Developer ID\n")
            return Result()
        with patch.object(release, "run", return_value=""), \
                patch.object(release.subprocess, "run", side_effect=verify) as verification:
            stable = release.promote(output / "manifest.json", extracted, evidence,
                                     release.PUBLIC_BASE + "v0.2.0", "0123456789")
            self.assertEqual(verification.call_count, 8)
        self.assertEqual(stable["release"]["channel"], "stable")
        self.assertEqual(stable["artifact"]["sha256"], manifest["artifact"]["sha256"])
        (extracted / "bin/codex").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "changed since"):
            release.promote(output / "manifest.json", extracted, evidence,
                            release.PUBLIC_BASE + "v0.2.0", "0123456789")

    def test_changed_source_fingerprint_is_detected(self):
        first = release.source_fingerprint(self.package)
        (self.package / "bin/codex").write_bytes(b"different build")
        self.assertNotEqual(first, release.source_fingerprint(self.package))

    def test_import_rejects_personal_state_before_archiving(self):
        (self.package / "auth.json").write_text("private")
        with patch.object(release, "minimum_macos") as inspection:
            with self.assertRaisesRegex(ValueError, "personal state"):
                release.prepare(self.package, self.root, self.root / "artifact")
            inspection.assert_not_called()
        self.assertFalse((self.root / "artifact").exists())


if __name__ == "__main__":
    unittest.main()
