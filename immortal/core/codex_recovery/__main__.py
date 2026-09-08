import argparse
from pathlib import Path
import subprocess
import sys

from . import RecoveryError, check, install, rollback, status, update_if_enabled


def main():
    parser = argparse.ArgumentParser(description="Manage the optional Codex recovery component")
    parser.add_argument("command", choices=("install", "status", "check", "update", "rollback"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--local-manifest", type=Path, help="Developer-only local package; never a stable release")
    parser.add_argument("--home", type=Path, help="Explicit developer test home; leaves the real home untouched")
    arguments = parser.parse_args()
    if arguments.local_manifest and arguments.command != "install":
        parser.error("--local-manifest is only valid for explicit developer installation")
    try:
        if arguments.command == "install":
            result = install(arguments.repo, home=arguments.home, local_manifest=arguments.local_manifest)
        elif arguments.command == "update":
            result = update_if_enabled(arguments.repo, home=arguments.home)
        elif arguments.command == "rollback":
            result = rollback(home=arguments.home)
        elif arguments.command == "check":
            result = check(home=arguments.home)
        else:
            result = status(home=arguments.home)
        print(result)
        return 0
    except (RecoveryError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("Codex recovery: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
