import argparse
import ast
from contextlib import contextmanager
from datetime import date
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from immortal.core import osa
from immortal.core.common import STATE_DIR

REPO = Path(__file__).resolve().parents[2]
PUBLIC_REPO = "https://github.com/vectal-labs/immortal-agents"
FEED_URL = "https://raw.githubusercontent.com/vectal-labs/immortal-agents/main/release.json"
API_URL = "https://api.github.com/repos/vectal-labs/immortal-agents/releases/tags/"
LABEL = "com.immortal-agents.updates"
INTERVAL = 3600
MAX_BYTES = 128 * 1024
VERSION_PATTERN = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


class UpdateError(Exception):
    pass


def version(value):
    if not isinstance(value, str) or not re.fullmatch(VERSION_PATTERN, value):
        raise UpdateError("Expected a stable version such as 1.2.3")
    return tuple(map(int, value.split(".")))


def source_version(text):
    try:
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__"
                                                   for t in node.targets):
                value = ast.literal_eval(node.value)
                version(value)
                return value
    except (SyntaxError, ValueError, TypeError):
        pass
    raise UpdateError("Installed version is missing or invalid; run the bootstrap instructions")


def installed_version(repo=REPO):
    return source_version((Path(repo) / "immortal" / "__init__.py").read_text())


def validate_feed(data):
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise UpdateError("Unsupported release feed")
    releases = data.get("releases")
    if not isinstance(releases, list) or len(releases) > 50:
        raise UpdateError("Invalid release list")
    seen = set()
    for release in releases:
        if not isinstance(release, dict):
            raise UpdateError("Invalid release entry")
        number = version(release.get("version"))
        if number in seen:
            raise UpdateError("Duplicate release version")
        seen.add(number)
        if type(release.get("important")) is not bool:
            raise UpdateError("Release importance must be true or false")
        summary = release.get("summary")
        if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 200 or any(ord(c) < 32 for c in summary):
            raise UpdateError("Release summary must be a short, single line")
        if not isinstance(release.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", release["commit"]):
            raise UpdateError("Release commit must be a full Git SHA")
        try:
            stamp = release["published_at"]
            if not isinstance(stamp, str) or date.fromisoformat(stamp).isoformat() != stamp:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise UpdateError("Invalid release date")
        expected = f"{PUBLIC_REPO}/releases/tag/v{release['version']}"
        if release.get("notes_url") != expected:
            raise UpdateError("Release notes must link to the matching public GitHub release")
    return sorted(releases, key=lambda r: version(r["version"]), reverse=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpdateError("Release request redirected; no update was trusted")


def fetch_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "immortal-agents-updates"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise UpdateError("Release response is too large")
    return json.loads(body)


def fetch_feed():
    return fetch_json(FEED_URL)


def published(release):
    data = fetch_json(API_URL + "v" + release["version"])
    return (isinstance(data, dict) and data.get("draft") is False and data.get("prerelease") is False
            and bool(data.get("published_at")) and data.get("tag_name") == "v" + release["version"]
            and data.get("html_url") == release["notes_url"])


def load_state(state_dir=STATE_DIR):
    try:
        data = json.loads((Path(state_dir) / "updates.json").read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise UpdateError("Cannot read update state; inspect updates.json before retrying")
    if not isinstance(data, dict):
        raise UpdateError("Invalid update state")
    return data


def save_state(state_dir, state):
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".updates-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / "updates.json")
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def locked(state_dir):
    Path(state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    with (Path(state_dir) / "updates.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError("Another update check or update is running")
        yield


def notify_update(release):
    title = "Immortal Agents update " + release["version"]
    message = release["summary"] + " Run ./install.sh update in your immortal-agents folder."
    return desktop_notification(title, message)


def desktop_notification(title, message):
    try:
        osa.run_jxa("const app = Application.currentApplication(); app.includeStandardAdditions = true; "
                    f"app.displayNotification({osa.js(message)}, {{withTitle: {osa.js(title)}}});", timeout=10)
        return True
    except (osa.OsaError, subprocess.TimeoutExpired, OSError):
        return False


def check(repo=REPO, state_dir=STATE_DIR, now=None, force=False):
    now = time.time() if now is None else now
    with locked(state_dir):
        state = load_state(state_dir)
        if state.pop("test_pending", False):
            state["notification_test"] = "attempted"
            save_state(state_dir, state)
            ok = desktop_notification("Immortal Agents update alerts",
                                      "Important updates will appear here. No automatic installs.")
            state["notification_test"] = "submitted" if ok else "failed"
            save_state(state_dir, state)
            print("Notification test " + state["notification_test"] + "; visible delivery is not confirmed.")
        last = state.get("checked_at", 0)
        if not force and isinstance(last, (int, float)) and 0 <= now - last < INTERVAL:
            return
        state["checked_at"] = now
        save_state(state_dir, state)
        try:
            current = version(installed_version(repo))
            releases = validate_feed(fetch_feed())
            newer = [r for r in releases if version(r["version"]) > current]
            important = next((r for r in newer if r["important"]), None)
            verified = state.setdefault("verified_releases", [])
            for candidate in {r["version"]: r for r in [newer[0] if newer else None, important] if r}.values():
                identity = candidate["version"] + ":" + candidate["commit"]
                if identity not in verified:
                    if not published(candidate):
                        raise UpdateError("The release is not published yet")
                    verified.append(identity)
            state["verified_releases"] = verified[-50:]
            state["available"] = newer[0] if newer else None
            state["checked_ok_at"] = now
            state.pop("check_error", None)
            notified = version(state.get("notified_version", "0.0.0"))
            if important and version(important["version"]) > notified:
                state["notified_version"] = important["version"]
                state["notification"] = "attempted"
                save_state(state_dir, state)
                state["notification"] = "submitted" if notify_update(important) else "failed"
            save_state(state_dir, state)
        except (UpdateError, OSError, ValueError, TimeoutError):
            state["check_error"] = "Release check unavailable; will retry later"
            save_state(state_dir, state)
        print(status(repo, state_dir))


def status(repo=REPO, state_dir=STATE_DIR):
    current = installed_version(repo)
    state = load_state(state_dir)
    lines = ["Installed version: " + current]
    pending = state.get("available")
    if pending and version(pending["version"]) > version(current):
        lines += [f"Update available: {pending['version']} — {pending['summary']}",
                  "Run ./install.sh update", pending["notes_url"]]
    elif state.get("checked_ok_at"):
        lines.append("No newer published release found at the last check")
    else:
        lines.append("Updates have not been checked successfully yet")
    if state.get("migration_required"):
        lines.append("Component update incomplete: run ./install.sh update again")
    if state.get("restart_required"):
        lines.append("Watcher restart required: run ./install.sh update again")
    if state.get("check_error"):
        lines.append(state["check_error"])
    lines.append("Notification test: " + state.get("notification_test", "not run"))
    if state.get("notification") == "failed":
        lines.append("Update notification failed; check macOS notification settings")
    return "\n".join(lines)


def job_path(home=None):
    return Path(home or Path.home()) / "Library" / "LaunchAgents" / (LABEL + ".plist")


def job_config(repo, state_dir, python):
    return {"Label": LABEL, "RunAtLoad": True, "StartInterval": INTERVAL,
            "ProgramArguments": [str(python), "-m", "immortal.core.updates", "check"],
            "WorkingDirectory": str(Path(repo).resolve()),
            "EnvironmentVariables": {"WATCHER_STATE_DIR": str(state_dir), "HOME": str(Path.home()),
                                     "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONDONTWRITEBYTECODE": "1"},
            "StandardOutPath": str(Path(state_dir) / "updates.stdout.log"),
            "StandardErrorPath": str(Path(state_dir) / "updates.stderr.log")}


def launchctl(*args, check=True):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=15, check=check)


def install_job(repo=REPO, state_dir=STATE_DIR, python=None, home=None):
    path = job_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(state_dir):
        state = load_state(state_dir)
        if "notification_test" not in state:
            state["test_pending"] = True
        save_state(state_dir, state)
    path.write_bytes(plistlib.dumps(job_config(repo, state_dir, python or sys.executable)))
    domain = f"gui/{os.getuid()}"
    if launchctl("print", domain + "/" + LABEL, check=False).returncode == 0:
        launchctl("bootout", domain + "/" + LABEL)
    launchctl("bootstrap", domain, str(path))
    print("Hourly update checker installed.")
    if state.get("test_pending"):
        print("A test notification will be requested from launchd.")
    else:
        print("Notification setup unchanged. Use ./install.sh notification-test to retest.")
    print("If no alert appears, check System Settings > Notifications > Script Editor and Focus settings.")


def remove_job(home=None):
    domain = f"gui/{os.getuid()}"
    if launchctl("print", domain + "/" + LABEL, check=False).returncode == 0:
        launchctl("bootout", domain + "/" + LABEL)
    job_path(home).unlink(missing_ok=True)


def test_notification(state_dir=STATE_DIR):
    with locked(state_dir):
        state = load_state(state_dir)
        state["test_pending"] = True
        save_state(state_dir, state)
    launchctl("kickstart", f"gui/{os.getuid()}/{LABEL}")
    print("Notification test requested from the installed LaunchAgent. Visible delivery is not confirmed.")


def announcement(path):
    releases = validate_feed(json.loads(Path(path).read_text()))
    if not releases:
        raise UpdateError("No release is ready to announce")
    release = releases[0]
    if not published(release):
        raise UpdateError("Publish the public GitHub Release before announcing it")
    return (f"Immortal Agents {release['version']}: {release['summary']}\n\n"
            "From your existing immortal-agents folder: ./install.sh update\n"
            "Older installs without that command: git pull --ff-only && ./install.sh\n"
            f"{release['notes_url']}\n"
            "For release emails: GitHub repository > Watch > Custom > Releases.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "status", "setup", "remove", "notification-test", "announcement"))
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "check":
            check(args.repo, force=args.force)
        elif args.command == "status":
            print(status(args.repo))
        elif args.command == "setup":
            install_job(args.repo, python=args.python)
        elif args.command == "remove":
            remove_job()
        elif args.command == "notification-test":
            test_notification()
        else:
            print(announcement(args.repo / "release.json"))
    except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Update service failed: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
