"""Explicit, versioned installation of the Codex connection-recovery component."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile

from . import activation, package
from .package import RecoveryError


def location(home=None):
    return Path(home or Path.home()).absolute() / ".local/share/immortal-agents/codex"


def load_state(root):
    path = root / "managed-state.json"
    if not path.exists():
        return {}
    if path.is_symlink():
        raise RecoveryError("Managed state cannot be a symlink")
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise RecoveryError("Unrecognized managed Codex state")
    for key in ("current", "previous"):
        value = state.get(key)
        if value is not None:
            package.relative(value)
            if len(Path(value).parts) != 2 or Path(value).parts[0] != "packages":
                raise RecoveryError("Unsafe managed package selection")
    return state


@contextmanager
def locked(root):
    # These paths are owned by this component, never package-manager entrypoints.
    if any(path.is_symlink() for path in (root,) + tuple(root.parents)[:4]):
        raise RecoveryError("Managed installation ancestors cannot be symlinks")
    for path in (root, root / "packages", root / "bin"):
        if path.is_symlink():
            raise RecoveryError("Managed installation directory cannot be a symlink")
        path.mkdir(parents=True, exist_ok=True)
        if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
            raise RecoveryError("Managed installation directory must be user-owned and not group/world writable")
    lockpath = root / "managed.lock"
    if lockpath.is_symlink():
        raise RecoveryError("Managed lock cannot be a symlink")
    with lockpath.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryError("Another Codex installation or rollback is running") from exc
        yield


def read_manifest(repo, local_manifest):
    path = Path(local_manifest) if local_manifest else Path(repo) / "codex-release.json"
    if not path.is_file():
        raise RecoveryError("No stable Codex recovery package is published yet")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise RecoveryError("Codex release manifest is too large")
    manifest = json.loads(path.read_text())
    if isinstance(manifest, dict) and manifest.get("available") is False:
        raise RecoveryError("No stable Codex recovery package is published yet")
    return package.validate(manifest, local=local_manifest is not None)


def prepare(root, manifest, local):
    target = root / "packages" / manifest["version"]
    if target.is_symlink():
        raise RecoveryError("Versioned package directory cannot be a symlink")
    if target.exists():
        saved = target / ".immortal-manifest.json"
        if not saved.is_file() or json.loads(saved.read_text()) != manifest:
            raise RecoveryError("This version already exists with different release metadata")
        package.verify_files(target, manifest)
        if package.digest(target / ".immortal-source.tar.gz") != manifest["source"]["sha256"]:
            raise RecoveryError("Installed source archive verification failed")
        package.check_signature(target, manifest, local)
        package.smoke(target, manifest)
        return target
    with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as temporary:
        temporary = Path(temporary)
        artifact, source, unpacked = temporary / "artifact.tar.gz", temporary / "source.tar.gz", temporary / "package"
        package.download(manifest["artifact"], artifact, local)
        package.download(manifest["source"], source, local, budget=120)
        package.extract(artifact, unpacked, manifest)
        package.check_signature(unpacked, manifest, local)
        package.smoke(unpacked, manifest)
        activation.write_json(unpacked / ".immortal-manifest.json", manifest)
        shutil.move(source, unpacked / ".immortal-source.tar.gz")
        os.rename(unpacked, target)
    return target


def install(repo, home=None, local_manifest=None):
    manifest = read_manifest(repo, local_manifest)
    package.check_platform(manifest)
    home = Path(home or Path.home()).absolute()
    root = location(home)
    with locked(root):
        state = load_state(root)
        activation.check_owned(root, state)
        activation.check_no_downgrade(root, manifest["upstream_version"], state)
        if state.get("enabled") and state["version"].split("+")[0] == manifest["upstream_version"]:
            if int(state["version"].rsplit(".", 1)[1]) > int(manifest["version"].rsplit(".", 1)[1]):
                raise RecoveryError("Selected recovery revision is newer; use explicit rollback to downgrade")
        changes, profiles = activation.profile_plan(home, root, state)
        target = prepare(root, manifest, local_manifest is not None)
        if load_state(root) != state:
            raise RecoveryError("Managed installation changed while preparing the package")
        activation.check_owned(root, state)
        changes, profiles = activation.profile_plan(home, root, state)
        selected = str(target.relative_to(root))
        previous = state.get("current")
        launcher = root / "bin/codex"
        changed_profiles = []
        made_launcher = False
        try:
            for change in changes:
                path, before, after, mode = change
                if path.is_symlink() or (path.read_bytes() if path.exists() else None) != before:
                    raise RecoveryError("Shell profile changed during activation; retry after edits finish")
                activation.atomic(path, after, mode)
                changed_profiles.append(change)
            activation.check_owned(root, state)
            if not launcher.is_symlink():
                launcher.symlink_to("../current/bin/codex")
                made_launcher = True
            activation.select(root, selected)
            package.smoke(root / "current", manifest)
            result = {"schema_version": 1, "enabled": True, "current": selected,
                      "previous": previous if previous != selected else state.get("previous"),
                      "version": manifest["version"], "channel": manifest["release"]["channel"],
                      "created_profiles": state.get("created_profiles", [path.name for path, before, _after, _mode in changes if before is None]),
                      "profiles": profiles, "original_codex": state.get("original_codex") or shutil.which("codex")}
            activation.write_json(root / "managed-state.json", result)
        except BaseException:
            if previous:
                activation.select(root, previous)
            else:
                (root / "current").unlink(missing_ok=True)
            if made_launcher:
                launcher.unlink(missing_ok=True)
            activation.restore_profiles(changed_profiles)
            raise
    label = "Developer-only package" if local_manifest is not None else "Codex recovery"
    return f"{label} {manifest['version']} installed and selected. " + activation.activation_status(root)


def update_if_enabled(repo, home=None):
    state = load_state(location(home))
    if not state.get("enabled"):
        return "Codex recovery is not enabled; nothing changed."
    if state.get("channel") != "stable":
        return "Developer-only Codex package selected; stable updater did not change it."
    return install(repo, home=home)


def status(home=None):
    root = location(home)
    state = load_state(root)
    if not state.get("enabled"):
        suffix = " A separate legacy installation exists; it is not managed by this updater." if (root / "activation.json").exists() else ""
        return "Codex recovery is not enabled through the managed installer." + suffix
    activation.check_owned(root, state)
    target = root / state["current"]
    manifest = json.loads((target / ".immortal-manifest.json").read_text())
    package.validate(manifest, local=state["channel"] == "local")
    package.verify_files(target, manifest)
    activation.profile_plan(Path(home or Path.home()).absolute(), root, state)
    return f"Codex recovery {state['version']} installed ({state['channel']}); package verified. " + activation.activation_status(root)


def check(home=None):
    from . import verification
    summary = status(home)
    root = location(home)
    state = load_state(root)
    if not state.get("enabled"):
        return summary
    manifest = json.loads((root / state["current"] / ".immortal-manifest.json").read_text())
    return summary + "\n" + verification.check(Path(home or Path.home()).absolute(), root, manifest)


def rollback(home=None):
    root = location(home)
    if not load_state(root).get("enabled"):
        return "No enabled managed Codex installation to roll back."
    with locked(root):
        state = load_state(root)
        activation.check_owned(root, state)
        changes, _profiles = activation.profile_plan(Path(home or Path.home()).absolute(), root, state)
        previous = state.get("previous")
        if previous:
            target = root / previous
            manifest = json.loads((target / ".immortal-manifest.json").read_text())
            package.validate(manifest, local=manifest["release"]["channel"] == "local")
            package.verify_files(target, manifest)
            package.check_signature(target, manifest, manifest["release"]["channel"] == "local")
            package.smoke(target, manifest)
            new_state = {**state, "current": previous, "previous": None, "version": manifest["version"], "channel": manifest["release"]["channel"]}
            try:
                activation.select(root, previous)
                activation.write_json(root / "managed-state.json", new_state)
            except BaseException:
                activation.select(root, state["current"])
                raise
            return f"Restored Codex recovery {manifest['version']}. Existing sessions were not restarted."
        changed = []
        try:
            for path, before, _after, mode in changes:
                after = before.decode().replace(state["profiles"][path.name], "").encode()
                if not after and path.name in state.get("created_profiles", []):
                    path.unlink()
                else:
                    activation.atomic(path, after, mode)
                changed.append((path, before, after, mode))
            (root / "current").unlink()
            (root / "bin/codex").unlink()
            activation.write_json(root / "managed-state.json", {"schema_version": 1, "enabled": False})
        except BaseException:
            activation.select(root, state["current"])
            launcher = root / "bin/codex"
            if not launcher.is_symlink():
                launcher.symlink_to("../current/bin/codex")
            activation.restore_profiles(changed)
            raise
    return "Managed Codex selection and owned PATH blocks removed. Original Codex installations and running sessions were left untouched."
