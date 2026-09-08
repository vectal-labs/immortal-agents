# Codex distribution verification — 2026-09-08

Status: implementation candidate. Stable public binary distribution remains gated on physical wake testing, a second clean Mac, and Developer ID signing/notarization. An isolated temporary home is not a second Mac.

## Assumptions checked

- **Installing files means BB uses them:** false. The executable chosen by BB must be checked independently; cached Codex processes retain their executable.
- **The existing updater executes new installer steps:** false. The previous updater fast-forwards Git and restarts the watcher. A new migration subprocess and a one-time bootstrap for older installations are required.
- **A package-manager launcher is durable:** false. Homebrew can recreate or overwrite its managed links. Keep Immortal's launcher in a separate user-owned directory and check PATH precedence. [Homebrew manual](https://docs.brew.sh/Manpage)
- **Changing a shell profile necessarily refreshes a running GUI app:** false in general. Node resolves commands using the child's supplied PATH, or its own inherited PATH. [Node child processes](https://nodejs.org/api/child_process.html)
- **Checksums prove Apple trusted-developer identity:** false. Developer ID certificates establish that identity; notarization is a separate Apple service. The current local CLI has an ad-hoc linker signature and no TeamIdentifier. [Apple Developer ID](https://developer.apple.com/developer-id/)
- **Unit tests prove clean-Mac distribution:** false. They can test install transitions, but cannot establish physical wake recovery, first-download Gatekeeper behavior, or compatibility with another user's configuration.

## Research completed

Three distinct DeepAPI deep researches finished with `completeness: complete`:

1. Signed macOS CLI distribution and atomic activation: `449836cf-2ece-4f65-a940-1d5e55ea0f76`.
2. Actual PATH inheritance and package-manager conflicts: `c8163882-ecfa-4357-bc81-efecebe37ab8`.
3. Persistent opt-in, updater bootstrap, rollback, and user expectations: `20602b6c-89f5-489a-8a9b-105efbca978a`.

All complete responses are retained in private thread storage. Eight primary documentation pages were fetched separately; all eight returned content without truncation. Raw research also included third-party search results; the recommendations below rely on the primary sources and local code inspection.

## Measured local behavior

Read-only checks on the actual Mac, macOS 26.6.2, found:

- Installed package metadata: `0.153.4+wake.1`, target `aarch64-apple-darwin`, with matching package resource/path directories.
- `file` reports a Mach-O arm64 executable. `codesign -dvv` reports `Signature=adhoc` and `TeamIdentifier=not set`.
- The current initial activation redirects the Bun-managed `codex` launcher. This is the installation to migrate away from, not a pattern to distribute.
- This BB child PATH already puts `~/.local/bin` before `~/.bun/bin`. Other users may have a different order.
- Installed BB host-daemon source maps show `runtime-shell-env.ts` probing the configured shell with `-ilc`, then `-lc` as fallback. A failed probe may retain the previous successful PATH.
- BB's `app.ts` uses a 10,000 ms cache for runtime shell-environment refresh. Provider health, model listing, installation status, and explicit shell refresh invoke this path. The implementation calls `replaceBaseShellEnv`; it does not replace the executable of an already-running Codex process.

The previous recovery experiment measured about 15 seconds to reconnect instead of 300 seconds and preserved completed commands. See [the recovery experiment](../experiments/0019-codex-wake-recovery.md). Those results validate the binary's tested recovery behavior, not this installer or physical wake.

## Implementation recommendations

Keep the runtime small: versioned package directories, one managed launcher, persisted opt-in, a previous-selection record, a lock, and atomic replacement of the active pointer. Never replace npm/Bun/Homebrew files during normal installation. On this Mac, restore the earlier Bun launcher only after matching its recorded ownership/target; refuse to overwrite unexpected drift.

Install into a temporary directory on the same filesystem. Verify trusted manifest fields and archive hash before extraction. Reject escaping paths, links, special files, duplicate entries, and unexpected executable paths. Verify executable startup and required helpers before selection. Keep the previous package until explicitly retired; existing processes may still need its resources.

Separate component updates from updater-code updates. The fresh migration process must import the newly installed release. Persist failure state and retry migrations even when the repository is already at the target version. No opt-in means no Codex download or activation. Rustup similarly separates toolchain updates, updater self-updates, and check-only behavior. [Rustup basic usage](https://rust-lang.github.io/rustup/basics.html)

Report installed version, active launcher resolution, and observed process versions separately. Shell-profile changes must be marked and idempotent, preserve unrelated content, handle spaces, and avoid a wrapper recursively resolving itself. Unknown PATH or process state means unverified, not active. Do not automatically stop working agents.

## Release trust and remaining gates

Pin the upstream source commit, behavior patch hash, Rust version, external dependency lock, helper origins, and final archive bytes. A provenance JSON file records a claim; it is not a cryptographic build attestation. GitHub attestations bind artifacts to a build identity and only help when verified by consumers. The current no-CI ADR is still in force, so do not claim GitHub Actions provenance for a locally built package. [GitHub attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations)

GitHub immutable releases prevent published asset and tag replacement. Assemble and inspect a complete draft before publishing; update the release feed only after the final assets are available and verified. [Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)

Apple's notarization service accepts ZIP archives, disk images, and signed installer packages. It does not directly accept tar.gz. Submit an appropriate container of the final signed binaries, retain the accepted result, then package the same final binary bytes for download. Validate real quarantined-download behavior on a clean Mac; never make quarantine removal an automatic installer step. [Apple notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)

The user approved the proposed optional distribution architecture. That does not manufacture missing signing credentials, a second physical Mac, or a completed physical-wake measurement. Stable readiness must remain false until the evidence exists. No new ADR was written.

## Install lifecycle measurements

**24/24 live lifecycle cases passed** using the final local v2 artifact. These are actual filesystem transitions, actual packaged binary executions, and actual shell invocations on this Mac. They are not mocked network/model calls or a second-device test. The suite passed an explicit temporary home containing spaces; process `HOME` was unchanged. Existing user installation and network/power state were not modified.

- Fresh installation: **3.252 s**; repeat installation: **0.239 s**, without duplicate profile blocks.
- Real packaged `codex --version`: **0.013 s**; binary hash matched the approved input exactly.
- BB-style `/bin/zsh -ilc` resolved the managed launcher in **0.013 s**, despite a user `.zshrc` that prepended Bun. This tests shell resolution, not a live BB conversation.
- Explicit check verified the temporary login shell and correctly omitted real process counting for that home.
- A second local selection activated in **3.142 s**; rollback restored the previous package in **0.177 s**. This uses the same real binary under a second fixture revision to exercise selection transitions; it does not claim another Codex build was released.
- A second rollback disabled the component and restored existing profile bytes. Credential sentinels and package-manager executables were unchanged. An update after opt-out stayed disabled.
- No opt-in caused no installation. An unpublished stable manifest was refused. The stable updater did not promote a developer-only installation.
- Corrupt package hash, corrupt source hash, same-version metadata collision, external launcher drift, external profile drift, a newer external Codex, an older managed revision, and a held installation lock were all rejected. Existing selection survived failed installs.
- A real fixture process that slept during startup was stopped after **20.003 s**. This establishes the startup-check timeout, not a network download timeout.

The first pass exposed a real compatibility bug: a dormant broken Homebrew Codex command blocked installation even though the selected Bun Codex worked. The runtime now requires verification of the selected/original command, but skips broken unselected candidates. Review also corrected zsh startup ordering, managed downgrade checks, pre-selection ownership rechecks, Bash profile fallback, and misleading custom-ZDOTDIR verification. The final pass was green after these fixes.

Final evidence: private `distribution-research/lifecycle-174451/report.json` and replay script `distribution-research/lifecycle.py` in this thread's storage. The initial failed report is retained; its downstream failures are not counted as independent product defects.

- Final artifact SHA-256: `7f3abc0f39cdc131fe684a86f9b8df8c631a7d44b0a2c00d67035118ace89d78`.
- Native CLI SHA-256: `b278342ed1e6db758eb497cb706101dc64a513980d0c1def3f83536482539104`.

Remaining measurements: physical sleep/wake, another clean Mac, signed/notarized quarantined-download launch, actual public release download, and a live BB conversation through this managed installation. None is represented as passed by the lifecycle suite.

## Dependency notices and source delivery

The release now runs [the dependency collector](../../ops/codex-release/license_inventory.py) after the native build. It uses the pinned Rust toolchain and locked macOS dependency graph, checks downloaded sources by SHA-256, preserves copyright headers, and deduplicates actual notice texts. The generated package contains `third-party-notices/dependency-inventory/inventory.json`, its texts, and retained source archives. The collector always reports `review_complete: false`; it does not issue legal clearance.

```sh
python3 ops/codex-release/license_inventory.py \
  --source /path/to/exported/source \
  --cargo-home /path/to/build/cargo-home \
  --output /path/to/package/third-party-notices/dependency-inventory
```

The real fresh-build run inventoried **1,013 normal/build packages, including 880 external packages**. Build-only packages are included; this is not a claim that every listed crate is linked into the binary. Eight behavior tests cover source-hash rejection, path safety, source delivery, missing notices, attribution preservation, and output ownership. These tests also pass on the repository's Python 3.9 runner; the actual release collector parses TOML on release Python.

Collected evidence extends well beyond the four helper notices:

- Exact Cargo.lock origins/checksums and actual recursive license texts, including bundled AWS-LC, ring, SQLite and Oniguruma notices; Rust's library copyright inventory.
- Exact source delivery for 12 MPL-2.0 packages: ten checksum-verified registry archives and one pinned Git archive covering both nucleo packages.
- Upstream license files omitted from published crate archives, fetched at their recorded commits. Fax's later maintainer restoration is identified separately. `io_tee` and `eventsource-stream` carry the official Apache 2.0 text offered by their published declarations.
- Pinned V8 15.0.245.2 source and notices, ICU, libc++, libc++abi, llvm-libc, Abseil 20250814.1, highway, fast_float and simdutf. The source rules explicitly select `core_lib_icu` and fold the custom libc++ runtime into the archive; these are identified build inputs, not an assumed list of optional libraries.
- Actual PCRE2 **10.45** source/license, matching the shipped ripgrep's reported version. An additional 32 dependency source/notice sets cover the ripgrep lock superset beyond matching Codex dependencies and helper source files. Other-platform/build-only entries are not presented as linked macOS components.

The final descriptor set leaves **13 explicit review items**, not a generic unperformed download task:

1. Two published MIT packages, `debugserver-types@0.5.0` and `deno_core_icudata@0.77.0`, omit original MIT attribution notices at their recorded commits and current upstream. Their declared terms remain recorded. ICU's collected notices cover the identified upstream data license separately from the Rust wrapper; confirm the payload's corresponding version and attribution. Missing filenames are not a determination that redistribution is forbidden.
2. Eight packages omit VCS metadata. Their source copyright headers and repository or expressly linked license texts are preserved, but publication-to-source-commit correspondence remains unverified: allocative, allocative_derive, display_container, fxhash, lock_free_hashtable, static_interner, strong_hash and strong_hash_derive.
3. Three native/asset provenance checks: match the downloaded V8 archive to the identified source/build inputs; establish ripgrep's exact prebuilt dependency recipe; match embedded ICU data and syntect syntax/theme dumps to their original notices. Ripgrep's source lock vendors PCRE2 10.46 while the executable reports 10.45, so that lock alone cannot prove the binary's build recipe.

All available source downloads and original notice evidence are collected. The remaining checks concern original attribution or correspondence with actual shipped bytes. Signing credentials, physical wake testing, and another clean Mac remain separate release gates.

## Final integrated verification

The full repository suite passed **468 tests** on Python 3.14. The fresh pinned release build completed in **14m54s**, and all five executable startup checks passed. Its source archive matched the independently prepared v2 archive exactly; this does not claim reproducible executable bytes across builds.

The frozen fresh package then passed **24/24 actual lifecycle checks**, including exact executable hash, shell selection, installation, upgrade, rollback, corruption/drift rejection, and preservation of fixture credentials and package-manager files. Fresh installation took **3.649 s**, upgrade **3.255 s**, and rollback **0.241 s**. Process HOME stayed unchanged. The report is retained in private thread storage at `distribution-research/lifecycle-fresh-181230/report.json`.

Fresh package SHA-256: `517b82b1a12114131ec8c8e2094eabd9b54a16f8d017f9215b12009526983400`. The artifact contains 666 inventoried files, including the final notice snapshot and its 13 explicit review items. See [release operations](../../ops/codex-release/OPERATIONS.md) for build and source hashes. These results do not replace signed-build recovery E2E, physical wake, clean-Mac, or public-download validation. No new public version was released.
