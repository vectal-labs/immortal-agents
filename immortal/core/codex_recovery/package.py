"""Pinned release validation and extraction; no activation side effects."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import tarfile
import time
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, build_opener


class RecoveryError(RuntimeError):
    pass


MAX_PACKAGE = 2 * 1024 ** 3
MAX_SOURCE = 512 * 1024 ** 2
PUBLIC_ASSETS = "/vectal-labs/immortal-agents/releases/download/"
REDIRECT_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


def relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise RecoveryError("Invalid package path")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in ("..", ".") for part in path.parts):
        raise RecoveryError("Unsafe package path: " + value)
    if any(part.startswith(".immortal-") for part in path.parts):
        raise RecoveryError("Reserved package path")
    return value


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def descriptor(value, limit, empty=False):
    if not isinstance(value, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("sha256", ""))):
        raise RecoveryError("Missing or invalid SHA-256")
    size = value.get("size")
    if type(size) is not int or not (0 if empty else 1) <= size <= limit:
        raise RecoveryError("Invalid release size")


def validate(manifest, local=False):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise RecoveryError("Unsupported Codex release manifest")
    version = manifest.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+\+[A-Za-z0-9.-]+", version):
        raise RecoveryError("Invalid managed Codex version")
    if not re.fullmatch(r"\d+\.\d+\.\d+", manifest.get("upstream_version", "")):
        raise RecoveryError("Invalid upstream version")
    if not re.fullmatch(re.escape(manifest["upstream_version"]) + r"\+wake\.\d+", version):
        raise RecoveryError("Recovery build must use upstream_version+wake.N")
    if not re.fullmatch(r"[a-f0-9]{40}", manifest.get("upstream_commit", "")):
        raise RecoveryError("Missing pinned upstream commit")
    if manifest.get("entrypoint") != "bin/codex" or manifest.get("platform") != "darwin-arm64":
        raise RecoveryError("Unsupported Codex package platform or entrypoint")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", str(manifest.get("min_macos", ""))):
        raise RecoveryError("Missing verified minimum macOS version")
    release = manifest.get("release", {})
    if not isinstance(release, dict):
        raise RecoveryError("Invalid release metadata")
    channel = release.get("channel")
    if channel != ("local" if local else "stable"):
        raise RecoveryError("Codex recovery has no installable stable release yet")
    if not local:
        checks = release.get("validation", {})
        if (not isinstance(checks, dict) or release.get("notarized") is not True or not re.fullmatch(r"[A-Z0-9]{10}", str(release.get("signing_team_id", "")))
                or checks.get("physical_wake") is not True or checks.get("clean_mac") is not True):
            raise RecoveryError("Stable Codex release validation is incomplete")
    for key, limit in (("artifact", MAX_PACKAGE), ("source", MAX_SOURCE)):
        descriptor(manifest.get(key), limit)
        validate_url(manifest[key].get("url", ""), local=local, initial=True)
    files, links = manifest.get("files"), manifest.get("symlinks")
    if not isinstance(files, dict) or not files or not isinstance(links, dict):
        raise RecoveryError("Missing package inventory")
    if set(files) & set(links):
        raise RecoveryError("Duplicate package inventory path")
    total = 0
    for name, info in files.items():
        relative(name)
        descriptor(info, MAX_PACKAGE, empty=True)
        if info.get("mode") not in (0o644, 0o755):
            raise RecoveryError("Package file mode must be 0644 or 0755")
        total += info["size"]
    if total > 4 * 1024 ** 3 or files.get("bin/codex", {}).get("mode") != 0o755:
        raise RecoveryError("Invalid executable or oversized package")
    for name, target in links.items():
        relative(name)
        if not isinstance(target, str) or not target or "\\" in target or PurePosixPath(target).is_absolute():
            raise RecoveryError("Unsafe package symlink")
        # Resolve lexically; links may use ../bin/codex but cannot leave the package.
        parts = list(PurePosixPath(name).parent.parts)
        for part in PurePosixPath(target).parts:
            if part == "..":
                if not parts:
                    raise RecoveryError("Package symlink escapes its root")
                parts.pop()
            elif part != ".":
                parts.append(part)
        if "/".join(parts) not in files:
            raise RecoveryError("Package links must point directly to inventoried files")
    for name in set(files) | set(links):
        if any(str(parent) in files or str(parent) in links for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise RecoveryError("Package file or symlink used as directory")
    return manifest


def check_platform(manifest):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RecoveryError("Codex recovery currently supports Apple Silicon macOS only")
    current = tuple(int(part) for part in platform.mac_ver()[0].split("."))
    minimum = tuple(int(part) for part in manifest["min_macos"].split("."))
    if current < minimum:
        raise RecoveryError("macOS is older than the package minimum")


def validate_url(url, local=False, initial=False):
    parsed = urlparse(url)
    if local:
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment or not parsed.path.startswith("/"):
            raise RecoveryError("Developer packages require absolute local file URLs")
    elif (parsed.scheme != "https" or parsed.hostname not in REDIRECT_HOSTS or parsed.username
          or parsed.password or parsed.port not in (None, 443) or parsed.fragment
          or (initial and (parsed.hostname != "github.com" or not parsed.path.startswith(PUBLIC_ASSETS)
                           or len(parsed.path[len(PUBLIC_ASSETS):].split("/")) != 2 or parsed.query))):
        raise RecoveryError("Release downloads must use this repository's HTTPS GitHub release assets")


class ReleaseRedirect(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(info, destination, local, budget=300):
    validate_url(info["url"], local=local, initial=True)
    deadline = time.monotonic() + budget
    stream = (Path(unquote(urlparse(info["url"]).path)).open("rb") if local
              else build_opener(ReleaseRedirect()).open(info["url"], timeout=30))
    count = 0
    with stream, Path(destination).open("xb") as output:
        # HTTPResponse.read(n) may keep waiting for n bytes under trickle traffic.
        # read1 returns after one buffered/socket read so the wall deadline is checked.
        read = getattr(stream, "read1", stream.read)
        while True:
            if time.monotonic() > deadline:
                raise RecoveryError("Release download exceeded its time limit")
            chunk = read(1024 * 1024)
            if time.monotonic() > deadline:
                raise RecoveryError("Release download exceeded its time limit")
            if not chunk:
                break
            count += len(chunk)
            if count > info["size"]:
                raise RecoveryError("Release download exceeded its declared size")
            output.write(chunk)
    if count != info["size"] or digest(destination) != info["sha256"]:
        raise RecoveryError("Release download checksum or size mismatch")


def extract(archive, destination, manifest):
    destination = Path(destination)
    destination.mkdir()
    files, links = manifest["files"], manifest["symlinks"]
    directories = {str(parent) for name in set(files) | set(links) for parent in PurePosixPath(name).parents if str(parent) != "."}
    seen = set()
    deadline = time.monotonic() + 120
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            if time.monotonic() > deadline:
                raise RecoveryError("Package extraction exceeded its time limit")
            name = relative(member.name.rstrip("/") if member.isdir() else member.name)
            if name in seen:
                raise RecoveryError("Duplicate archive entry")
            seen.add(name)
            target = destination / name
            if member.isdir() and name in directories:
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile() and name in files:
                info = files[name]
                if member.size != info["size"] or member.mode != info["mode"]:
                    raise RecoveryError("Archive file metadata mismatch")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        if time.monotonic() > deadline:
                            raise RecoveryError("Package extraction exceeded its time limit")
                        output.write(chunk)
                target.chmod(info["mode"])
            elif member.issym() and name in links and member.linkname == links[name]:
                # Materialize links only after all regular files have been checked.
                continue
            else:
                raise RecoveryError("Unexpected or unsafe archive entry: " + name)
    if not set(files).issubset(seen) or not set(links).issubset(seen):
        raise RecoveryError("Package archive is missing inventoried files")
    for name, target in links.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
    verify_files(destination, manifest)


def verify_files(directory, manifest):
    directory = Path(directory)
    expected = set(manifest["files"]) | set(manifest["symlinks"])
    directories = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != "."}
    for path in directory.rglob("*"):
        name = path.relative_to(directory).as_posix()
        if name in {".immortal-manifest.json", ".immortal-source.tar.gz"} and not path.is_symlink():
            continue
        if name in directories and path.is_dir() and not path.is_symlink():
            continue
        if name not in expected:
            raise RecoveryError("Unexpected installed package entry: " + name)
    for name, info in manifest["files"].items():
        path = directory / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != info["size"]
                or path.stat().st_mode & 0o7777 != info["mode"] or digest(path) != info["sha256"]):
            raise RecoveryError("Installed package verification failed: " + name)
    for name, target in manifest["symlinks"].items():
        path = directory / name
        if not path.is_symlink() or os.readlink(path) != target:
            raise RecoveryError("Installed package symlink changed: " + name)


def check_signature(directory, manifest, local):
    if local:
        return
    team = manifest["release"]["signing_team_id"]
    for name, info in manifest["files"].items():
        if info["mode"] != 0o755:
            continue
        path = str(Path(directory) / name)
        for command in (("/usr/bin/codesign", "--verify", "--strict", "--check-notarization", "-R", f'anchor apple generic and certificate leaf[subject.OU] = "{team}" and notarized', path),
                        ("/usr/sbin/spctl", "--assess", "--type", "execute", path)):
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RecoveryError("Package signature or Gatekeeper verification failed: " + name)


def smoke(directory, manifest):
    result = subprocess.run([str(Path(directory) / manifest["entrypoint"]), "--version"],
                            capture_output=True, text=True, timeout=20)
    versions = re.findall(r"\b\d+\.\d+\.\d+\b", result.stdout)
    if result.returncode or versions != [manifest["upstream_version"]]:
        raise RecoveryError("Downloaded Codex failed its version startup check")
