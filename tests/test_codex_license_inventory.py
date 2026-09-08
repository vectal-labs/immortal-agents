import importlib.util
import json
import io
import hashlib
import tarfile
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("codex_license_inventory", Path(__file__).parents[1] / "ops/codex-release/license_inventory.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LicenseInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        (self.source / "codex-rs").mkdir(parents=True)
        self.cache = self.root / "cargo"
        self.cache.mkdir()
        self.docs = self.root / "rust/share/doc/rust"
        self.docs.mkdir(parents=True)
        (self.docs / "COPYRIGHT-library.html").write_text("toolchain copyright")
        self.packages = []
        self.locks = []

    def package(self, name, license="MIT", notice="shared license"):
        root = self.cache / "registry/src/example" / (name + "-1.0.0")
        root.mkdir(parents=True)
        (root / "Cargo.toml").write_text("fixture")
        if notice:
            (root / "LICENSE").write_text(notice)
        item = {"name": name, "version": "1.0.0", "license": license, "license_file": None,
                "manifest_path": str(root / "Cargo.toml"), "source": "registry+https://github.com/rust-lang/crates.io-index"}
        self.packages.append(item)
        self.locks.append(f'[[package]]\nname="{name}"\nversion="1.0.0"\nsource="{item["source"]}"\nchecksum="{"0" * 64}"\n')
        return root

    def collect(self, package_inputs=()):
        (self.source / "codex-rs/Cargo.lock").write_text("\n".join(self.locks))
        locks = [{key: json.loads(value) for key, value in (line.split("=", 1) for line in text.splitlines() if "=" in line)}
                 for text in self.locks]
        return MODULE.collect(self.source, self.cache, self.root / "output", {"packages": self.packages},
                              "\n".join(p["name"] + " v1.0.0|MIT" for p in self.packages), self.root / "rust", package_inputs=package_inputs, locks=locks)

    def test_supplement_collects_copyright_but_preserves_unverified_origin(self):
        root = self.package("missing", notice=None)
        (root / "lib.rs").write_text("// Copyright The Authors.\n// Licensed under MIT.\n\nfn code() {}")
        raw = b"The full MIT license text"
        descriptor = {"name": "upstream-missing", "url": "https://example.invalid/LICENSE", "format": "text",
                      "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
                      "packages": ["missing@1.0.0"], "source_mapping_unverified": True}
        with patch.object(MODULE.urllib.request, "urlopen", return_value=io.BytesIO(raw)):
            result = self.collect([descriptor])
        self.assertEqual(len(result["packages"][0]["notices"]), 2)
        reasons = [p["reason"] for p in result["unresolved"] if p["package"] == "missing@1.0.0"]
        self.assertEqual(len(reasons), 1)
        self.assertIn("correspondence remains unverified", reasons[0])
        header = result["packages"][0]["notices"][0]
        self.assertNotIn("fn code", (self.root / "output" / header["path"]).read_text())

    def test_collects_nested_notices_deduplicates_text_and_excludes_symlinks(self):
        first = self.package("first")
        self.package("second")
        native = first / "native"
        native.mkdir()
        (native / "COPYING").write_text("native license")
        (native / "copying-phase.cc").write_text("source code, not a license")
        (native / "NOTICE").symlink_to(self.root / "outside")
        result = self.collect()
        paths = [n["path"] for p in result["packages"] for n in p["notices"]]
        self.assertEqual(len(paths), 3)
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(result["rust_library_notices"])
        self.assertFalse(result["review_complete"])
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_missing_license_is_explicit_not_legal_clearance(self):
        self.package("missing", notice=None)
        result = self.collect()
        self.assertTrue(any(x["package"] == "missing@1.0.0" for x in result["unresolved"]))
        self.assertTrue(any(x["package"] == "V8 native archive" for x in result["unresolved"]))
        self.assertFalse(result["review_complete"])

    def test_exact_mpl_source_archive_is_copied_only_after_checksum_matches(self):
        self.package("covered", license="MPL-2.0")
        archive = self.cache / "registry/cache/example/covered-1.0.0.crate"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"exact upstream source fixture")
        checksum = MODULE.sha(archive)
        self.locks[0] = self.locks[0].replace("0" * 64, checksum)
        result = self.collect()
        bundled = result["packages"][0]["source_archive"]
        self.assertEqual(bundled["sha256"], checksum)
        self.assertEqual((self.root / "output" / bundled["path"]).read_bytes(), archive.read_bytes())

    def test_manifest_cannot_read_license_outside_source_or_cache(self):
        self.package("escape")
        outside = self.root / "private-file"
        outside.write_text("must not collect")
        self.packages[0]["license_file"] = str(outside)
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.collect()

    def test_existing_inventory_is_not_overwritten(self):
        self.package("first")
        output = self.root / "output"
        output.mkdir()
        sentinel = output / "mine"
        sentinel.write_text("preserve")
        with self.assertRaisesRegex(ValueError, "already exist"):
            self.collect()
        self.assertEqual(sentinel.read_text(), "preserve")


class NativeNoticeTests(unittest.TestCase):
    def test_native_archive_reads_text_without_extracting_unsafe_paths(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as stream:
            item = tarfile.TarInfo("../../LICENSE")
            data = b"native license"
            item.size = len(data)
            stream.addfile(item, io.BytesIO(data))
        raw = archive.getvalue()
        descriptor = {"name": "native-1", "url": "https://example.invalid/source.tar.gz", "format": "tar.gz",
                      "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE.urllib.request, "urlopen", return_value=io.BytesIO(raw)):
            output = Path(temporary) / "output"
            output.mkdir()
            result = MODULE.collect_native([descriptor], output)
            self.assertFalse((Path(temporary) / "LICENSE").exists())
            notice = result[0]["notices"][0]
            self.assertEqual((output / notice["path"]).read_bytes(), data)

    def test_native_source_hash_mismatch_is_rejected(self):
        raw = b"changed source"
        descriptor = {"name": "native-1", "url": "https://example.invalid/LICENSE", "format": "text",
                      "sha256": "0" * 64, "size": len(raw)}
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE.urllib.request, "urlopen", return_value=io.BytesIO(raw)):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                MODULE.collect_native([descriptor], Path(temporary))
