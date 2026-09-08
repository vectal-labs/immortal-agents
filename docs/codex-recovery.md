# Managed Codex recovery

Status: implementation candidate. No stable managed Codex package is published. `codex-release.json` deliberately reports unavailable until release validation passes. The current public watcher release remains v0.1.0.

## Install and update

After the managed release is published, from a clean primary checkout:

```sh
git pull --ff-only
./install.sh --codex-recovery
./install.sh status
./install.sh check-codex
```

The flag is one-time consent to use an Immortal-maintained Codex build. Ordinary installs do not enable it. The initial `git pull` and reinstall are necessary for existing users: the old updater only changes repository code and restarts the watcher, so it cannot install the new component or run migrations itself.

Once enabled:

```sh
./install.sh update
./install.sh status
./install.sh check-codex
./install.sh rollback-codex
```

Updates remain manual. The hourly checker only announces published releases. The updated updater runs the installed release's component migration in a fresh process; a failed component migration remains retryable at the same repository version. It preserves authentication, approval settings, telemetry choice and running turns.

Only Apple Silicon macOS is supported initially. The complete current package requires macOS 15 or later because its bundled zsh requires macOS 15. Other platforms fail clearly before activation.

## Activation and rollback

Packages live in a user-owned versioned directory under `~/.local/share/immortal-agents/codex`. Installation verifies the archive, file inventory, platform and release signing before an atomic selection change. A managed launcher and shell PATH integration select the build without overwriting Bun, npm or Homebrew executables.

Installed is not the same as active. Check actual executable resolution in your shell and BB. Existing Codex processes keep the binary already in memory until their runtime is released. Installation never kills running agents. PATH changes may need a fresh shell or BB runtime; status must report this rather than treating a version string as proof.

`check-codex` verifies a fresh zsh shell, BB's new-launch executable and hash, and running native process paths. Missing BB or a different selection is reported explicitly. The installer appends owned PATH blocks to zsh and Bash profiles, preserves existing contents and Bash's login-profile precedence, and refuses symlinked profiles or a custom `ZDOTDIR` it cannot safely manage. Watcher uninstall leaves this optional component intact; use `rollback-codex` to remove its selection.

The compiled CLI currently prints upstream version `0.153.4`. The managed package version and file hash identify `0.153.4+wake.1`. A package-manager update can change resolution; status detects that drift. Do not blindly downgrade a newer upstream Codex.

Rollback changes the selection for future processes. It does not erase sessions or edit credentials. Preserve the original upstream installation and the previous managed package until the replacement has been verified.

## What the fix covers

The model WebSocket receives periodic liveness probes and an immediate probe on macOS wake/network hints. A missing matching Pong starts existing Codex recovery after a ten-second reply budget. Model inactivity remains a separate timeout; healthy quiet reasoning continues.

The local outage fixture improved recovery from 300.004 to about 15.4 seconds. A Pong proves peer responsiveness, not model progress. Physical wake and fresh-Mac validation remain release requirements. See experiment 0019 and the distribution evaluation for exact evidence and limitations.

## Developer validation

A local unsigned manifest is accepted only through the explicit developer module option, never normal `install.sh`. It must not be described as a stable public installation. Use disposable home paths for fixture tests; never repoint HOME or CODEX_HOME to test the release. The release tool under `ops/codex-release` prepares pinned artifacts and blocks stable promotion until the required evidence is present.

## Maintenance

Every supported Codex upgrade needs a new pinned build and the transport, cancellation, tool-history, activation and rollback tests. Keep the upstream patch small. Once a tested official version includes recovery, distribute a migration back to upstream and retire the managed build. Do not silently replace a user's newer upstream release with this older fork.
