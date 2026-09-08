#!/usr/bin/env python3
"""Collect pinned dependency notices and source origins; never grant legal clearance."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.request

TARGET = "aarch64-apple-darwin"
ROOT_PACKAGES = ("codex-cli", "codex-code-mode-host")
CODE_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp", ".rs", ".py", ".sh", ".json", ".toml", ".gn"}
NOTICE = re.compile(r"^(unlicense|licen[cs]e|copying|copyright|notice)(?:[._-].*)?$", re.I)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lock_packages(path):
    import tomllib  # The release CLI requires Python 3.12+; pure collectors work on 3.9.
    return tomllib.loads(path.read_text())["package"]


def run(arguments, cwd, env):
    return subprocess.check_output(arguments, cwd=cwd, env=env, text=True, timeout=300)


def selected_packages(tree):
    result = set()
    for line in tree.splitlines():
        match = re.match(r"([^ ]+) v([^ |]+)", line)
        if match:
            result.add((match[1], match[2]))
    if not result:
        raise ValueError("Dependency tree did not contain packages")
    return result


def notice_files(root):
    """Keep actual texts, including notices shipped with bundled C libraries."""
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "target") and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if NOTICE.fullmatch(name) and path.suffix.lower() not in CODE_SUFFIXES and path.is_file() and not path.is_symlink():
                result.append(path)
    return result


def collect_text(path, output):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Unexpectedly large license text")
    checksum = sha(path)
    destination = output / "texts" / (checksum + path.suffix if path.suffix == ".html" else checksum + ".txt")
    destination.parent.mkdir(exist_ok=True)
    if not destination.exists():
        shutil.copyfile(path, destination)
    return {"path": destination.relative_to(output).as_posix(), "sha256": checksum}


def copyright_header(root, output):
    for path in (root / "src/lib.rs", root / "lib.rs"):
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text()
        match = re.match(r"(/\*.*?\*/|(?://[^\n]*\n)+)", text, re.S)
        if match and "Copyright" in match[0]:
            temporary = output / ".copyright-header"
            temporary.write_text(match[0] + "\n")
            notice = {"source_path": path.relative_to(root).as_posix() + ":leading-comment", **collect_text(temporary, output)}
            temporary.unlink()
            return notice
    return None


def registry_origin(package, locks):
    matches = [p for p in locks if p["name"] == package["name"] and p["version"] == package["version"]
               and p.get("source") == package.get("source")]
    if len(matches) != 1:
        raise ValueError("Package does not have one exact Cargo.lock entry: " + package["name"])
    lock = matches[0]
    source = package["source"]
    if source.startswith("registry+"):
        if source != "registry+https://github.com/rust-lang/crates.io-index":
            raise ValueError("Unrecognized registry; inspect its source URL manually")
        return {"kind": "registry", "source": source, "sha256": lock["checksum"],
                "download": f"https://static.crates.io/crates/{package['name']}/{package['name']}-{package['version']}.crate"}
    if not source.startswith("git+https://github.com/") or "@" in source or "#" not in source:
        raise ValueError("Unrecognized Git source; inspect its origin manually")
    return {"kind": "git", "source": source, "commit": source.rsplit("#", 1)[1]}


def collect(source, cargo_home, output, metadata, tree, rust_sysroot, native_inputs=(), package_inputs=(), locks=None):
    source, cargo_home, output = map(Path, (source, cargo_home, output))
    if output.exists():
        raise ValueError("Inventory output must not already exist")
    output.mkdir(parents=True)
    selected = selected_packages(tree)
    if locks is None:
        locks = lock_packages(source / "codex-rs/Cargo.lock")
    packages = [p for p in metadata["packages"] if (p["name"], p["version"]) in selected]
    if {(p["name"], p["version"]) for p in packages} != selected:
        raise ValueError("Cargo metadata is missing a selected package")
    rows, unresolved = [], []
    supplement_names = {identifier for item in package_inputs for identifier in item.get("packages", [])}
    for package in sorted(packages, key=lambda p: (p["name"], p["version"], p.get("source") or "")):
        root = Path(package["manifest_path"]).parent
        row = {"name": package["name"], "version": package["version"], "license_expression": package.get("license"), "notices": []}
        row["origin"] = registry_origin(package, locks) if package.get("source") else {
            "kind": "source-distribution", "path": root.relative_to(source).as_posix()}
        found = notice_files(root)
        # Workspace crates often inherit a parent license file; preserve the nearest one.
        if not found and (not package.get("source") or package["source"].startswith("git+")):
            parent = root
            boundary = source if not package.get("source") else cargo_home / "git/checkouts"
            while parent.is_relative_to(boundary):
                found = [p for p in parent.iterdir() if p.is_file() and not p.is_symlink() and NOTICE.fullmatch(p.name)]
                if found or parent == boundary or (parent / ".git").exists():
                    break
                parent = parent.parent
        if package.get("license_file"):
            explicit = Path(package["license_file"])
            if not explicit.is_absolute():
                explicit = root / explicit
            if explicit.exists() and explicit not in found:
                if not any(explicit.resolve().is_relative_to(base.resolve()) for base in (source, cargo_home)):
                    raise ValueError("License file escapes the source and Cargo cache")
                found.append(explicit)
        for path in found:
            label = os.path.relpath(path, root)
            row["notices"].append({"source_path": label, **collect_text(path, output)})
        if row["name"] + "@" + row["version"] in supplement_names:
            header = copyright_header(root, output)
            if header:
                row["notices"].append(header)
        if not found:
            unresolved.append({"package": package["name"] + "@" + package["version"], "reason": "No license/notice text found in the downloaded package; metadata alone is insufficient."})
        if "MPL-2.0" in (package.get("license") or ""):
            row["source_delivery"] = "Exact source origin recorded; retain this source under MPL and document how recipients obtain it."
            if row["origin"]["kind"] == "registry":
                archives = list((cargo_home / "registry/cache").glob("*/" + package["name"] + "-" + package["version"] + ".crate"))
                exact = next((p for p in archives if sha(p) == row["origin"]["sha256"]), None)
                if exact:
                    destination = output / "sources" / exact.name
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copyfile(exact, destination)
                    row["source_archive"] = {"path": destination.relative_to(output).as_posix(), "sha256": sha(destination)}
                else:
                    unresolved.append({"package": package["name"], "reason": "Exact MPL crate source archive missing from cache."})
            else:
                commit = row["origin"]["commit"]
                if not re.fullmatch(r"[a-f0-9]{40}", commit) or not root.resolve().is_relative_to((cargo_home / "git/checkouts").resolve()):
                    raise ValueError("Unexpected MPL Git checkout")
                env = dict(os.environ)
                for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
                    env.pop(key, None)
                if run(["git", "rev-parse", "HEAD"], root, env).strip() != commit or run(["git", "status", "--porcelain", "--untracked-files=no"], root, env).strip():
                    raise ValueError("MPL Git source checkout differs from its pinned revision")
                destination = output / "sources" / ("git-" + commit + ".tar.gz")
                destination.parent.mkdir(exist_ok=True)
                if not destination.exists():
                    run(["git", "archive", "--format=tar.gz", "--output=" + str(destination), commit], root, env)
                row["source_archive"] = {"path": destination.relative_to(output).as_posix(), "sha256": sha(destination)}
        rows.append(row)
    toolchain = []
    rust_docs = Path(rust_sysroot) / "share/doc/rust"
    for path in [rust_docs / "COPYRIGHT-library.html", *sorted((rust_docs / "licenses").glob("*.txt"))]:
        if path.is_file():
            toolchain.append({"source_path": path.relative_to(rust_docs).as_posix(), **collect_text(path, output)})
    if not toolchain:
        unresolved.append({"package": "Rust standard library", "reason": "Toolchain library copyright inventory unavailable."})
    helper = source / "immortal-release/helper-sources/ripgrep-15.2.0/Cargo.lock"
    helper_lock = []
    if helper.exists():
        helper_lock = [{k: p[k] for k in ("name", "version", "source", "checksum") if k in p}
                       for p in lock_packages(helper)]
    supplements = collect_native(package_inputs, output)
    by_package = {p["name"] + "@" + p["version"]: p for p in rows}
    for supplement in supplements:
        for identifier in supplement.get("packages", []):
            if identifier not in by_package:
                raise ValueError("Supplement names an unselected package: " + identifier)
            by_package[identifier]["notices"].extend({**notice, "upstream_url": supplement["url"]}
                                                    for notice in supplement["notices"])
            unresolved = [item for item in unresolved if not (item["package"] == identifier and item["reason"].startswith("No license/notice text"))]
            if supplement.get("source_mapping_unverified") and not any(item["package"] == identifier for item in unresolved):
                unresolved.append({"package": identifier, "reason": "Repository license texts collected, but crate omitted VCS metadata; publication-to-source-commit correspondence remains unverified."})
    unresolved += [
        {"package": "V8 native archive", "reason": "Pinned source and notices collected for V8, ICU, libc++, libc++abi, llvm-libc, Abseil, highway, fast_float and simdutf. Source build rules select core_lib_icu and custom libc++ runtime. Confirm the downloaded native release archive was produced by these exact source/build inputs; checksum verification alone does not attest that relationship."},
        {"package": "ripgrep + PCRE2", "reason": "Observed binary uses PCRE2 10.45; exact 10.45 source and license collected separately. Helper source lock references pcre2-sys 0.2.10 with vendored 10.46, so it does not attest the prebuilt binary's actual dependency graph. Match remaining Rust dependency notices to the helper's exact build recipe."},
        {"package": "Embedded assets/native libraries", "reason": "Recursive package notices include native code and data notices. Confirm selected embedded assets against the build, especially deno_core_icudata payload and syntect syntax/theme dumps; filenames and Cargo metadata cannot establish embedded asset provenance."},
    ]
    result = {"schema_version": 1, "review_complete": False, "target": TARGET,
              "scope": "Cargo normal and build dependency graph for codex-cli and codex-code-mode-host; excludes dev edges. Includes build-time-only crates, not a binary-level SBOM.",
              "cargo_lock_sha256": sha(source / "codex-rs/Cargo.lock"), "packages": rows,
              "rust_library_notices": toolchain, "ripgrep_lock_superset": helper_lock,
              "native_evidence": collect_native(native_inputs, output), "upstream_package_notices": supplements,
              "unresolved": unresolved}
    (output / "inventory.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (output / "README.txt").write_text("Dependency notice/source inventory. review_complete=false.\nSee inventory.json for exact source origins, notices, and unresolved coverage.\nShared text files are deduplicated by SHA-256. sources/ contains checksum-verified MPL registry crate archives when available.\nThis inventory is not legal clearance or a build attestation.\n")
    return result


def collect_native(inputs, output):
    def download(item):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", item["name"]) or not item["url"].startswith("https://"):
            raise ValueError("Invalid native source descriptor")
        destination = output / "sources" / (item["name"] + (".tar.gz" if item["format"] == "tar.gz" else ".download"))
        destination.parent.mkdir(exist_ok=True)
        with urllib.request.urlopen(item["url"], timeout=120) as stream, destination.open("xb") as target:
            count = 0
            while chunk := stream.read(1024 * 1024):
                count += len(chunk)
                if count > item["size"]:
                    raise ValueError("Native source exceeded its pinned size")
                target.write(chunk)
        if count != item["size"] or sha(destination) != item["sha256"]:
            raise ValueError("Native source checksum mismatch: " + item["name"])
        return destination

    if len({item["name"] for item in inputs}) != len(inputs):
        raise ValueError("Duplicate notice input name")
    (output / "sources").mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        downloads = list(pool.map(download, inputs))
    rows = []
    for item, destination in zip(inputs, downloads):
        row = {**item, "notices": []}
        if item["format"] == "tar.gz":
            if item.get("retain_source", True):
                row["source_archive"] = destination.relative_to(output).as_posix()
            with tarfile.open(destination, "r:gz") as archive:
                for member in archive:
                    name = Path(member.name).name
                    if member.isfile() and NOTICE.fullmatch(name) and Path(name).suffix.lower() not in CODE_SUFFIXES:
                        if member.size > 4 * 1024 * 1024:
                            raise ValueError("Native license text is oversized")
                        data = archive.extractfile(member).read()
                        temporary = output / ".notice-text"
                        temporary.write_bytes(data)
                        row["notices"].append({"source_path": member.name, **collect_text(temporary, output)})
                        temporary.unlink()
            if not item.get("retain_source", True):
                destination.unlink()
        elif item["format"] in ("text", "base64"):
            raw = destination.read_bytes()
            data = base64.b64decode(raw, validate=True) if item["format"] == "base64" else raw
            destination.write_bytes(data)
            row["notices"].append(collect_text(destination, output))
            destination.unlink()
        else:
            raise ValueError("Unknown native source format")
        if not row["notices"]:
            raise ValueError("Native input contained no notice texts: " + item["name"])
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = json.loads((args.source / "immortal-release/inputs.json").read_text())
    env = dict(os.environ, CARGO_HOME=str(args.cargo_home.absolute()), RUSTUP_TOOLCHAIN=inputs["rust_version"])
    cargo = run(["rustup", "which", "--toolchain", inputs["rust_version"], "cargo"], args.source, env).strip()
    rustc = run(["rustup", "which", "--toolchain", inputs["rust_version"], "rustc"], args.source, env).strip()
    env["RUSTC"] = rustc
    cwd = args.source / "codex-rs"
    command = [cargo, "tree", "--offline", "--locked", "--target", TARGET, "--edges", "normal,build", "--prefix", "none", "--format", "{p}|{l}"]
    for name in ROOT_PACKAGES:
        command += ["-p", name]
    tree = run(command, cwd, env)
    metadata = json.loads(run([cargo, "metadata", "--offline", "--locked", "--format-version", "1", "--filter-platform", TARGET], cwd, env))
    sysroot = run([rustc, "--print", "sysroot"], cwd, env).strip()
    native = json.loads(Path(__file__).with_name("native-license-inputs.json").read_text())
    if native["upstream_commit"] != inputs["upstream_commit"]:
        raise ValueError("Native notice inputs belong to another Codex release")
    package_inputs = json.loads(Path(__file__).with_name("package-license-inputs.json").read_text())
    if package_inputs["upstream_commit"] != inputs["upstream_commit"]:
        raise ValueError("Package notice inputs belong to another Codex release")
    result = collect(args.source.absolute(), args.cargo_home.absolute(), args.output.absolute(), metadata, tree, sysroot, native["inputs"], package_inputs["inputs"])
    print(json.dumps({"packages": len(result["packages"]), "unresolved": len(result["unresolved"]), "review_complete": False}))


if __name__ == "__main__":
    main()
