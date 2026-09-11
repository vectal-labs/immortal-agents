# Codex recovery release runbook

The managed Codex component ships in a later watcher release; until it passes the checks below, the repository version and public feed advertise only watcher releases and `codex-release.json` stays unavailable. ADR 0047 keeps builds and verification local; no GitHub Actions workflow is added.

1. Verify clean public source history, pin the upstream commit, patch checksums, Rust version and helper inputs. Build with the release tool under `ops/codex-release`.
2. Include the upstream LICENSE/NOTICE, bundled dependency notices and corresponding modified sources. Never include credentials, session logs or machine-specific state in artifacts.
3. Sign the macOS executables using the release owner's Developer ID identity. Notarize the deliverable and verify first launch under normal Gatekeeper rules; do not strip quarantine or ask users to disable system protections.
4. Run all repository tests plus the packaged Codex transport/RPC and installed BB pilot fixtures. Record archive SHA-256, toolchain, platform and exact test results. Bind validation evidence to the artifact that will be released.
5. On a second clean Apple Silicon Mac, test download, installation, shell/BB resolution, upgrade, rollback and first launch. Record macOS version and binary hash. A temporary home on the development Mac is useful integration coverage, not this clean-Mac gate.
6. Run an explicitly authorized physical sleep/wake test and verify observer delivery plus successful recovery. Do not cut the network as a substitute without fresh approval and the repository's outage procedure.
7. Prepare the stable component manifest from those verified artifacts. Add it to the release commit and bump `immortal/__init__.py` to the next version. Run the full repository suite again after integration changes. Confirm package checksums, signing Team ID, artifact URLs and source URLs.
8. Tag the tested public commit with that version and publish the GitHub Release with the complete artifacts. Test the actual public download, install and rollback paths before announcing.
9. Only after the release exists and downloads work, add its exact version, commit and notes URL to release.json on main. Retain older important announcements. The feed update is a separate commit so the release commit can be referenced exactly.
10. Publish the one-time bootstrap instructions from docs/codex-recovery.md. Explain that existing processes need a fresh runtime. Within a day, inspect opt-in diagnostic evidence or a consenting user's test; do not collect prompts or credentials.

Current external requirements: no valid code-signing identity was available on the development Mac, no release-signing repository secrets were listed, and physical-wake/second-Mac evidence is absent. The repository can prepare and test the candidate, but cannot honestly satisfy these gates without those resources.

Custom distribution and eventual retirement deserve an approved ADR. This runbook does not create or supersede one.

## Prepared release notes (unpublished)

**vNEXT: Optional managed Codex connection recovery**

Opt in with `git pull --ff-only` followed by `./install.sh --codex-recovery`. This installs a separately maintained Codex build for Apple Silicon macOS 15+. Ordinary installs remain opted out. Existing users need this one-time bootstrap; the old updater cannot install the new component itself.

The recovery build probes model connections and reconnects when the peer stops responding. Local outage tests reduced detection/recovery from about 5 minutes to about 15 seconds. This does not detect every stalled model response.

Use `./install.sh update` for verified component updates, `./install.sh check-codex` to inspect shell/BB selection, and `./install.sh rollback-codex` to restore the previous selection. Active agents keep running; new runtimes use the selected build. Credentials and package-manager installations remain untouched.

Publish these notes only with the completed signing, physical-wake, clean-Mac, packaged E2E and license evidence linked below them. Do not present the current unsigned development package as this release.
