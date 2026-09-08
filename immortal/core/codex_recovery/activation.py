"""Owned launcher/profile edits and truthful activation reporting."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from .package import RecoveryError

BEGIN = "# >>> immortal-agents managed Codex >>>"
END = "# <<< immortal-agents managed Codex <<<"
PROFILES = (".zprofile", ".zshrc", ".bashrc")


def atomic(path, data, mode=0o600):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, data):
    atomic(path, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())


def select(root, target):
    path = root / "current"
    temporary = root / (".current-" + str(os.getpid()))
    try:
        temporary.symlink_to(target)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def profile_block(root):
    return "\n" + BEGIN + "\nexport PATH=" + shlex.quote(str(root / "bin")) + ':"$PATH"\n' + END + "\n"


def profile_plan(home, root, state):
    if os.environ.get("ZDOTDIR") and Path(os.environ["ZDOTDIR"]).absolute() != home:
        raise RecoveryError("Custom ZDOTDIR is unsupported; managed PATH activation requires the home shell profiles")
    changes = []
    block = profile_block(root)
    stored = state.get("profiles", {})
    # Creating .bash_profile would suppress an existing .bash_login or .profile.
    bash_login = next((name for name in (".bash_profile", ".bash_login", ".profile") if (home / name).exists()), ".bash_profile")
    names = tuple(stored) if stored else PROFILES + (bash_login,)
    for name in names:
        if name not in PROFILES + (".bash_profile", ".bash_login", ".profile"):
            raise RecoveryError("Unexpected managed shell profile")
        path = home / name
        if path.is_symlink():
            raise RecoveryError("Symlinked shell profiles are unsupported by the managed installer; existing configuration was left untouched")
        before = path.read_bytes() if path.exists() else None
        text = (before or b"").decode("utf-8")
        expected = stored.get(name)
        if expected:
            if text.count(expected) != 1 or text.count(BEGIN) != 1 or text.count(END) != 1:
                raise RecoveryError("Managed PATH block changed; refusing to overwrite shell configuration")
            after = text.replace(expected, block)
        else:
            if BEGIN in text or END in text:
                raise RecoveryError("Unrecognized managed PATH block; inspect shell configuration")
            after = text + block
        changes.append((path, before, after.encode(), path.stat().st_mode & 0o777 if path.exists() else 0o644))
    return changes, {name: block for name in names}


def restore_profiles(changes):
    for path, before, _after, mode in reversed(changes):
        if before is None:
            path.unlink(missing_ok=True)
        else:
            atomic(path, before, mode)


def check_owned(root, state):
    current, launcher = root / "current", root / "bin/codex"
    if state.get("enabled"):
        if not current.is_symlink() or os.readlink(current) != state.get("current"):
            raise RecoveryError("Managed Codex selection changed externally; no automatic replacement was made")
        if not launcher.is_symlink() or os.readlink(launcher) != "../current/bin/codex":
            raise RecoveryError("Managed Codex launcher changed externally; no automatic replacement was made")
    elif current.exists() or current.is_symlink() or launcher.exists() or launcher.is_symlink():
        raise RecoveryError("Unrecognized existing managed Codex launcher; refusing to replace it")


def version_tuple(value):
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", value)
    return tuple(int(part) for part in match.groups()) if match else None


def check_no_downgrade(root, upstream, state):
    if state.get("enabled"):
        installed = json.loads((root / state["current"] / ".immortal-manifest.json").read_text())
        if version_tuple(installed["upstream_version"]) > version_tuple(upstream):
            raise RecoveryError("Selected managed Codex is newer; refusing to downgrade")
    required = {shutil.which("codex"), state.get("original_codex")} - {None}
    candidates = set(required)
    # Inspect common package-manager locations even when our PATH block is first.
    home = root.parents[3]
    candidates.update(str(home / name) for name in (".bun/bin/codex", ".npm-global/bin/codex", ".local/bin/codex"))
    candidates.update(("/opt/homebrew/bin/codex", "/usr/local/bin/codex"))
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        resolved = Path(candidate).resolve()
        if state.get("enabled") and resolved.is_relative_to((root / "packages").resolve()):
            continue
        try:
            result = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            if candidate not in required:
                continue
            raise RecoveryError("Could not verify the selected Codex version; refusing to replace its selection")
        version = version_tuple(result.stdout)
        if result.returncode or version is None:
            if candidate in required:
                raise RecoveryError("Could not verify the selected Codex version; refusing to replace its selection")
            continue
        if version > version_tuple(upstream):
            raise RecoveryError("Existing Codex is newer than this recovery build; refusing to downgrade")


def activation_status(root):
    found = shutil.which("codex")
    target = root / "current/bin/codex"
    shell = bool(found and target.exists() and Path(found).resolve() == target.resolve())
    message = "Shell PATH: managed binary selected." if shell else "Shell PATH: pending a new login shell or PATH configuration."
    # Do not equate a CLI PATH check with the cached runtime of a GUI application.
    return message + " BB: runtime activation unverified; restart idle sessions normally. Existing running sessions may still use an older binary."
