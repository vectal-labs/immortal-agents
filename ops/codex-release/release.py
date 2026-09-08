#!/usr/bin/env python3
"""Prepare a local Codex artifact; promote only with verified release evidence."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
INPUTS = json.loads((HERE / "inputs.json").read_text())
EXECUTABLES = (
    "bin/codex", "bin/codex-code-mode-host", "codex-path/rg",
    "codex-resources/zsh/bin/zsh",
)
PUBLIC_BASE = "https://github.com/vectal-labs/immortal-agents/releases/download/"


def run(*args, cwd=None, env=None):
    return subprocess.check_output(args, cwd=cwd, env=env, text=True).strip()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def source_fingerprint(root):
    files, links = inventory(root)
    files.pop("immortal-release/source-inventory.json", None)
    return hashlib.sha256(json.dumps([files, links], sort_keys=True).encode()).hexdigest()


def safe_link(name, target):
    if PurePosixPath(target).is_absolute():
        return False
    depth = len(PurePosixPath(name).parent.parts)
    for part in PurePosixPath(target).parts:
        depth += -1 if part == ".." else (0 if part == "." else 1)
        if depth < 0:
            return False
    return True


def inventory(root):
    files, symlinks = {}, {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            if not safe_link(name, target) or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Unsafe package symlink: " + name)
            if not path.exists():
                raise ValueError("Dangling package symlink: " + name)
            symlinks[name] = target
        elif path.is_file():
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            files[name] = {"sha256": sha(path), "size": path.stat().st_size, "mode": mode}
        elif not path.is_dir():
            raise ValueError("Unsupported package entry: " + name)
    return files, symlinks


def archive(root, destination):
    """Stable archive metadata, independent of local owner and wall-clock time."""
    inventory(root)
    with Path(destination).open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for path in sorted(root.rglob("*")):
                    info = tar.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.pax_headers = {}
                    info.mode = 0o755 if info.isdir() or path.stat().st_mode & 0o111 else 0o644
                    if info.isfile():
                        with path.open("rb") as stream:
                            tar.addfile(info, stream)
                    else:
                        tar.addfile(info)


def extract_source(data, output):
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = tar.getmembers()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.islnk():
                raise ValueError("Unsafe source archive path")
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("Unsupported source archive entry")
            if member.issym() and not safe_link(member.name, member.linkname):
                raise ValueError("Unsafe source archive symlink")
        # Git-produced archives are trusted after path validation. Symlinks are
        # extracted last so no earlier link can redirect file writes.
        for member in sorted(members, key=lambda m: m.issym()):
            tar.extract(member, output, filter="data")


def helper_sources(output, patch_env):
    def download(item):
        with urlopen(item["url"], timeout=60) as response:
            data = response.read(item["size"] + 1)
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("Helper source checksum mismatch: " + item["name"])
        return item, data
    destination = output / "immortal-release/helper-sources"
    destination.mkdir()
    with ThreadPoolExecutor(max_workers=2) as pool:
        downloaded = list(pool.map(download, INPUTS["helper_sources"]))
    for item, data in downloaded:
        extract_source(data, destination)
        if item.get("patch"):
            patch = output / item["patch"]
            if sha(patch) != item["patch_sha256"]:
                raise ValueError("Helper source patch changed")
            run("git", "apply", str(patch), cwd=destination / item["root"], env=patch_env)


def prepare_source(checkout, output):
    if output.exists():
        raise ValueError("Source output must not exist")
    for patch in INPUTS["patches"]:
        if sha(REPO / patch["path"]) != patch["sha256"]:
            raise ValueError("Pinned patch changed: " + patch["path"])
    commit = run("git", "rev-parse", INPUTS["upstream_commit"] + "^{commit}", cwd=checkout)
    if commit != INPUTS["upstream_commit"]:
        raise ValueError("Wrong upstream commit")
    data = subprocess.check_output(["git", "archive", commit], cwd=checkout)
    output.mkdir(parents=True)
    extract_source(data, output)
    patch_env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        patch_env.pop(key, None)
    patch_env["GIT_CEILING_DIRECTORIES"] = str(output.parent)
    for patch in INPUTS["patches"]:
        run("git", "apply", str(REPO / patch["path"]), cwd=output, env=patch_env)
    # This source distribution also carries the exact modifications and recipe.
    recipe = output / "immortal-release"
    recipe.mkdir()
    shutil.copy2(HERE / "inputs.json", recipe / "inputs.json")
    for patch in INPUTS["patches"]:
        shutil.copy2(REPO / patch["path"], recipe / Path(patch["path"]).name)
    shutil.copytree(HERE / "licenses", recipe / "licenses")
    helper_sources(output, patch_env)
    write_json(recipe / "source-inventory.json", {"sha256": source_fingerprint(output)})
    return output


def build(source, package):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Only native Apple Silicon macOS builds are supported")
    if json.loads((source / "immortal-release/inputs.json").read_text()) != INPUTS:
        raise ValueError("Source inputs do not match the release recipe")
    fingerprint = json.loads((source / "immortal-release/source-inventory.json").read_text())["sha256"]
    if fingerprint != source_fingerprint(source):
        raise ValueError("Prepared source was modified; export it again")
    env = dict(os.environ)
    for key in ("RUSTC", "RUSTDOC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS"):
        env.pop(key, None)
    env.update(RUSTUP_TOOLCHAIN=INPUTS["rust_version"], CODEX_REPO_ROOT=str(source),
               STABLE_GIT_COMMIT=INPUTS["upstream_commit"] + "-wake-recovery",
               PYTHONDONTWRITEBYTECODE="1",
               CARGO_HOME=str(source.parent / "cargo-home"), CARGO_INCREMENTAL="0",
               CARGO_TARGET_DIR=str(source.parent / "cargo-target"))
    rustc = run("rustup", "which", "--toolchain", INPUTS["rust_version"], "rustc")
    cargo = run("rustup", "which", "--toolchain", INPUTS["rust_version"], "cargo")
    env["RUSTC"] = rustc
    env["PATH"] = str(Path(rustc).parent) + os.pathsep + env.get("PATH", "")
    if run(rustc, "--version", env=env).split()[1] != INPUTS["rust_version"]:
        raise ValueError("Install the exact pinned Rust toolchain")
    # Resolve pinned upstream V8 hashes through its own release helper.
    helper = ("import sys,json; sys.path.insert(0,'scripts'); "
              "from codex_package.v8 import resolve_codex_v8_cargo_env; "
              "from codex_package.targets import TARGET_SPECS; "
              "print(json.dumps(resolve_codex_v8_cargo_env(TARGET_SPECS['aarch64-apple-darwin'])))")
    # Prevent local V8 overrides from silently changing release inputs.
    for key in ("V8_FROM_SOURCE", "RUSTY_V8_ARCHIVE", "RUSTY_V8_SRC_BINDING_PATH"):
        env.pop(key, None)
    env.update(json.loads(run(sys.executable, "-c", helper, cwd=source, env=env)))
    subprocess.run([cargo, "build", "--locked", "--release", "--target", INPUTS["target"],
                    "--bin", "codex", "--bin", "codex-code-mode-host"],
                   cwd=source / "codex-rs", env=env, check=True)
    bins = Path(env["CARGO_TARGET_DIR"]) / INPUTS["target"] / "release"
    subprocess.run([sys.executable, str(source / "scripts/build_codex_package.py"),
                    "--target", INPUTS["target"], "--package-version", INPUTS["version"],
                    "--package-dir", str(package), "--entrypoint-bin", str(bins / "codex"),
                    "--code-mode-host-bin", str(bins / "codex-code-mode-host")], env=env, check=True)
    # Metadata resolves workspace manifests beyond the two binary dependency
    # graphs. Populate their pinned sources before offline notice collection.
    subprocess.run([cargo, "fetch", "--locked", "--target", INPUTS["target"]],
                   cwd=source / "codex-rs", env=env, check=True)
    subprocess.run([sys.executable, str(HERE / "license_inventory.py"),
                    "--source", str(source), "--cargo-home", env["CARGO_HOME"],
                    "--output", str(package / "third-party-notices/dependency-inventory")], env=env, check=True)
    write_json(package / "build-provenance.json", {
        "source_sha256": fingerprint, "inputs": INPUTS,
        "rustc": run(rustc, "--version", env=env),
        "sdk": run("xcrun", "--sdk", "macosx", "--show-sdk-version"),
        "license_inventory_inputs": {name: sha(HERE / name) for name in (
            "license_inventory.py", "native-license-inputs.json", "package-license-inputs.json")},
        "unsigned_executables": {name: sha(package / name) for name in EXECUTABLES}})


def minimum_macos(package):
    versions = []
    for name in EXECUTABLES:
        description = run("/usr/bin/file", "-b", str(package / name))
        if "Mach-O" not in description or "arm64" not in description:
            raise ValueError("Not an arm64 Mach-O executable: " + name)
        commands = run("/usr/bin/otool", "-l", str(package / name))
        values = re.findall(r"\bminos\s+(\d+(?:\.\d+)+)", commands)
        if not values:
            raise ValueError("Missing macOS deployment target: " + name)
        versions.extend(values)
    return max(versions, key=lambda value: tuple(int(v) for v in value.split(".")))


def prepare(package, checkout, output):
    if output.exists():
        raise ValueError("Artifact output must not exist")
    metadata = json.loads((package / "codex-package.json").read_text())
    if metadata.get("version") != INPUTS["version"] or metadata.get("target") != INPUTS["target"]:
        raise ValueError("Wrong package version or target")
    for name in EXECUTABLES:
        if not (package / name).is_file() or not os.access(package / name, os.X_OK):
            raise ValueError("Missing executable: " + name)
    files, symlinks = inventory(package)
    allowed = set(EXECUTABLES) | {"codex-package.json", "build-provenance.json", "LICENSE", "NOTICE", "IMMORTAL-NOTICE"}
    for name in set(files) | set(symlinks):
        if name not in allowed and not name.startswith("third-party-notices/"):
            raise ValueError("Unexpected package file; do not archive personal state: " + name)
    min_os = minimum_macos(package)
    with tempfile.TemporaryDirectory(prefix="codex-release-") as temporary:
        work = Path(temporary)
        source = prepare_source(checkout, work / "source")
        staged = work / "package"
        shutil.copytree(package, staged, symlinks=True)
        for name in ("LICENSE", "NOTICE"):
            shutil.copy2(source / name, staged / name)
        shutil.copytree(HERE / "licenses", staged / "third-party-notices", dirs_exist_ok=True)
        (staged / "IMMORTAL-NOTICE").write_text(
            "Modified OpenAI Codex distribution by Immortal Agents.\n"
            "Changes: WebSocket connection recovery and visible first retry.\n"
            "See the accompanying source archive and release manifest for exact inputs.\n"
        )
        output.mkdir(parents=True)
        artifact = output / ("codex-" + INPUTS["version"] + "-darwin-arm64.tar.gz")
        source_archive = output / ("codex-" + INPUTS["version"] + "-source.tar.gz")
        archive(staged, artifact)
        archive(source, source_archive)
        files, symlinks = inventory(staged)
        manifest = {"schema_version": 1, "version": INPUTS["version"],
                    "upstream_version": INPUTS["upstream_version"],
                    "upstream_commit": INPUTS["upstream_commit"],
                    "platform": "darwin-arm64", "min_macos": min_os,
                    "entrypoint": "bin/codex", "files": files, "symlinks": symlinks,
                    "patches": INPUTS["patches"], "build_inputs": INPUTS,
                    "source_tree_sha256": source_fingerprint(source),
                    "artifact": asset(artifact), "source": asset(source_archive),
                    "release": {"channel": "local", "signing_team_id": None,
                                "notarized": False, "validation": {
                                    "physical_wake": False, "clean_mac": False}}}
        write_json(output / "manifest.json", manifest)
    return manifest


def asset(path):
    return {"url": path.resolve().as_uri(), "sha256": sha(path), "size": path.stat().st_size}


def promote(manifest_path, package, evidence_path, base_url, team_id):
    """Verify external signing/evidence; never perform signing or publication."""
    manifest = json.loads(manifest_path.read_text())
    evidence = json.loads(evidence_path.read_text())
    if not re.fullmatch(r"[A-Z0-9]{10}", team_id):
        raise ValueError("A real Apple Developer Team ID is required")
    if not base_url.startswith(PUBLIC_BASE) or not re.fullmatch(r"[A-Za-z0-9._+-]+", base_url[len(PUBLIC_BASE):]):
        raise ValueError("Release URL must identify a pinned repository release")
    if evidence.get("artifact_sha256") != manifest["artifact"]["sha256"]:
        raise ValueError("Evidence must cover this exact archive")
    for key in ("physical_wake", "clean_mac", "e2e", "licenses_reviewed"):
        record = evidence.get(key, {})
        if record.get("passed") is not True or not record.get("report_sha256") or not record.get("reviewer"):
            raise ValueError("Missing reviewed validation evidence: " + key)
    receipt = evidence.get("notarization", {})
    if receipt.get("status") != "Accepted" or not receipt.get("id") or not receipt.get("submission_sha256"):
        raise ValueError("Accepted notarization evidence is required")
    provenance_file = package / "build-provenance.json"
    if not provenance_file.is_file():
        raise ValueError("Public release requires a package built by the pinned recipe")
    provenance = json.loads(provenance_file.read_text())
    if provenance.get("inputs") != INPUTS or provenance.get("source_sha256") != manifest.get("source_tree_sha256"):
        raise ValueError("Build provenance does not match distributed source")
    files, symlinks = inventory(package)
    if files != manifest["files"] or symlinks != manifest["symlinks"]:
        raise ValueError("Package changed since artifact preparation")
    for name in EXECUTABLES:
        binary = str(package / name)
        requirement = 'anchor apple generic and certificate leaf[subject.OU] = "' + team_id + '" and notarized'
        run("/usr/bin/codesign", "--verify", "--strict", "--check-notarization", "-R", requirement, "--verbose=2", binary)
        signing = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=4", binary],
                                 capture_output=True, text=True, check=True)
        if "TeamIdentifier=" + team_id not in signing.stderr.splitlines():
            raise ValueError("Unexpected signing identity: " + name)
        assessment = subprocess.run(["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=2", binary],
                                    capture_output=True, text=True, check=True)
        if "source=Notarized Developer ID" not in assessment.stderr:
            raise ValueError("Gatekeeper did not verify Apple notarization: " + name)
    # Signed bytes must be prepared first; no post-manifest mutation is allowed.
    for key in ("artifact", "source"):
        local = manifest_path.parent / Path(unquote(urlsplit(manifest[key]["url"]).path)).name
        if sha(local) != manifest[key]["sha256"] or local.stat().st_size != manifest[key]["size"]:
            raise ValueError("Release archive changed: " + key)
        manifest[key]["url"] = base_url + "/" + local.name
    manifest["release"] = {"channel": "stable", "signing_team_id": team_id,
                           "notarized": True, "validation": {"physical_wake": True, "clean_mac": True},
                           "evidence_sha256": sha(evidence_path)}
    write_json(manifest_path.parent / "stable-manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    if sys.version_info < (3, 12):
        parser.exit(1, "Release tooling requires Python 3.12 or newer; the runtime installer supports Python 3.9.\n")
    sub = parser.add_subparsers(dest="command", required=True)
    source = sub.add_parser("source")
    source.add_argument("--checkout", type=Path, required=True)
    source.add_argument("--output", type=Path, required=True)
    compile_parser = sub.add_parser("build")
    compile_parser.add_argument("--source", type=Path, required=True)
    compile_parser.add_argument("--package", type=Path, required=True)
    local = sub.add_parser("prepare-local")
    local.add_argument("--checkout", type=Path, required=True)
    local.add_argument("--package", type=Path, required=True)
    local.add_argument("--output", type=Path, required=True)
    stable = sub.add_parser("promote")
    stable.add_argument("--manifest", type=Path, required=True)
    stable.add_argument("--package", type=Path, required=True)
    stable.add_argument("--evidence", type=Path, required=True)
    stable.add_argument("--base-url", required=True)
    stable.add_argument("--team-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "source":
            prepare_source(args.checkout.resolve(), args.output.resolve())
        elif args.command == "build":
            build(args.source.resolve(), args.package.resolve())
        elif args.command == "prepare-local":
            prepare(args.package.resolve(), args.checkout.resolve(), args.output.resolve())
        else:
            promote(args.manifest.resolve(), args.package.resolve(), args.evidence.resolve(), args.base_url, args.team_id)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
