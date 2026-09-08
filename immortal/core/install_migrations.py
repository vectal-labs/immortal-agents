"""User-invoked, repeatable component migrations after a verified repo update."""

import argparse
from pathlib import Path
import subprocess
import sys


def apply(repo, home=None):
    # Import in this fresh process, after Git has installed the new release.
    from immortal.core import codex_recovery
    return codex_recovery.update_if_enabled(Path(repo), home=home)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--home", type=Path)
    args = parser.parse_args()
    from immortal.core.codex_recovery import RecoveryError
    try:
        print(apply(args.repo, args.home))
    except (RecoveryError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("Component migration: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
