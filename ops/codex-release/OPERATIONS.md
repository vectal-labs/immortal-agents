# Managed Codex artifacts

Status: local preparation works. **No public release is approved or available.**
The initial package supports Apple Silicon macOS **15.0+**: its bundled zsh has
a higher deployment target than the main Codex executable. This is measured
from every executable, not inferred from the development machine.

Use Python 3.12+, Git, the exact Rust toolchain in `inputs.json`, and Xcode
command-line tools. The source build also uses upstream's checksum-pinned rg,
zsh, and V8 downloads. It requires no files from the maintainer's home directory.
Builds record the SDK version; deterministic archive metadata does not imply
bit-for-bit reproducible Rust builds across different SDKs.

## Build from pinned source

Run from the Immortal Agents checkout. Choose an empty work directory outside
personal configuration. Do not point output arguments at an existing install.

```bash
git clone --filter=blob:none --no-checkout https://github.com/openai/codex.git /tmp/codex-upstream
git -C /tmp/codex-upstream fetch origin 3d2ee51ca2d5db578f328aa75e20aa22c0197c9a
rustup toolchain install 1.97.1 --profile minimal
python3 ops/codex-release/release.py source --checkout /tmp/codex-upstream --output /tmp/codex-release/source
python3 ops/codex-release/release.py build --source /tmp/codex-release/source --package /tmp/codex-release/package
python3 ops/codex-release/release.py prepare-local --checkout /tmp/codex-upstream --package /tmp/codex-release/package --output /tmp/codex-release/artifacts
```

The build uses `cargo build --locked --release`, both native executables, and the
upstream canonical package layout. Patches and dependency-lock normalization are
separately hash-pinned. Build provenance records the prepared source fingerprint,
Rust/SDK versions, and unsigned executable hashes. Source mutation before a build
is rejected. No global Codex command, credentials, or provider configuration changes.

After compilation, the build runs `license_inventory.py` into
`package/third-party-notices/dependency-inventory`. This includes actual license
texts, dependency origins/checksums, and available source archives. Its
`review_complete: false` and unresolved entries are preserved. Automated
collection supplies evidence for the required release review; it does not
approve redistribution.

`prepare-local` can also import an existing tested package. Such imports stay
local: stable promotion requires build provenance from the pinned recipe.

Outputs:

- A complete package `.tar.gz`, with flat `bin/codex` layout, both helpers, rg,
  zsh, upstream LICENSE/NOTICE, rg/zsh license texts, and a modification notice.
- A modified-source `.tar.gz`, exported from the pinned commit and patched afresh.
  It includes Cargo.lock, vendored upstream source/licenses, and exact patch inputs.
  It also contains checksum-pinned ripgrep 15.2.0 source and zsh source at
  `77045ef899e53b9598bebc5a41db93a548a40ca6` with Codex's exact exec-wrapper patch.
  It excludes `.git`, credentials, build outputs, and local working-tree changes.
- `manifest.json`: hashes/sizes for both archives and every regular file, separate
  contained symlinks, exact minimum macOS, provenance, and `release.channel=local`.

Helper provenance is pinned in `inputs.json`. Zsh's source commit and patch match
the [artifact release workflow](https://github.com/openai/codex/blob/rust-v0.134.0-alpha.3/.github/workflows/rust-release-zsh.yml).
The zsh [license](https://github.com/zsh-users/zsh/blob/77045ef899e53b9598bebc5a41db93a548a40ca6/LICENCE)
distinguishes core shell code from certain optional GPL shell functions; this
binary package contains only `bin/zsh`, while the source archive preserves the
complete source and per-file notices. Ripgrep is
[dual MIT/Unlicense](https://github.com/BurntSushi/ripgrep/blob/15.2.0/COPYING).
The final license review also covers compiled Rust/V8 dependencies; these four
helper notice files alone do not constitute that review.

## Stable promotion

Local manifests are never the default update feed. Before promotion:

1. Review all bundled third-party notices and source obligations; include any
   additional notices required by compiled dependencies/helpers in the package.
2. Sign all four executables with the release Apple Developer Team identity.
   Review hardened-runtime entitlements for V8/JIT and subprocess behavior;
   do not apply guessed blanket entitlements. Test the signed package again.
3. Notarize the signed distribution through Apple. Preserve the accepted receipt
   and submission digest. Check Gatekeeper on a genuinely fresh Mac with quarantine.
4. Re-run E2E and physical sleep/wake on the exact signed build. The physical
   wake test needs the user's separately reserved approval. A simulated socket
   outage or an isolated home directory does not count as either physical test.
5. Prepare the final archives from those signed bytes. Never mutate them afterward.
   Record reviewed evidence for that exact archive hash.

Evidence JSON has `artifact_sha256`; `physical_wake`, `clean_mac`, `e2e`, and
`licenses_reviewed` objects containing `passed: true`, `report_sha256`, and
`reviewer`; and a `notarization` object containing `status: "Accepted"`, `id`,
and `submission_sha256`. Reports and Apple receipts are reviewed release evidence,
not a substitute for the live signature/Gatekeeper checks run during promotion.

Extract the final package archive into an empty directory, then:

```bash
python3 ops/codex-release/release.py promote \
  --manifest /tmp/codex-release/artifacts/manifest.json \
  --package /tmp/codex-release/final-package \
  --evidence /tmp/codex-release/evidence.json \
  --base-url https://github.com/vectal-labs/immortal-agents/releases/download/v0.2.0 \
  --team-id REALTEAMID
```

### CLI notarization commands

After reviewing the pinned upstream entitlement files, sign a release copy with
your own Developer ID Application identity. The existing upstream templates are
`.github/scripts/macos-signing/{codex,codex-code-mode-host}.entitlements.plist`;
both enable JIT and unsigned executable memory. Test those privileges against
the actual release; do not replace them with unrelated app entitlements.

```bash
CODEX_SIGN_IDENTITY='Developer ID Application: YOUR ORGANIZATION (REALTEAMID)'
for binary in codex codex-code-mode-host; do
  codesign --force --timestamp --options runtime --sign "$CODEX_SIGN_IDENTITY" \
    --entitlements "/tmp/codex-release/source/.github/scripts/macos-signing/$binary.entitlements.plist" \
    "/tmp/codex-release/package/bin/$binary"
done
for binary in codex-path/rg codex-resources/zsh/bin/zsh; do
  codesign --force --timestamp --options runtime --sign "$CODEX_SIGN_IDENTITY" \
    "/tmp/codex-release/package/$binary"
done
```

Submit a ZIP of the signed package; Apple does not accept our `.tar.gz` as a
notarization container. Keep credentials in an existing Keychain profile:

```bash
ditto -c -k --keepParent /tmp/codex-release/package /tmp/codex-release/notary-payload.zip
shasum -a 256 /tmp/codex-release/notary-payload.zip
xcrun notarytool submit /tmp/codex-release/notary-payload.zip \
  --keychain-profile immortal-release --wait --output-format json \
  > /tmp/codex-release/notary-receipt.json
```

Require `status: Accepted`, retain the submission ID/digest, and retrieve its
full log with `xcrun notarytool log ID --keychain-profile immortal-release LOG.json`.
Prepare the distribution tarball from the same signed bytes afterward.
Standalone executables and ZIP archives cannot be stapled. This flow therefore
requires online ticket checks; it does not promise first-launch verification
offline. A future offline installer would need a stapled supported container.
[Apple's notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)
documents those formats and ticket limitations.

For each of the four executables, promotion checks:

```bash
codesign --verify --strict --check-notarization \
  -R 'anchor apple generic and certificate leaf[subject.OU] = "REALTEAMID" and notarized' \
  PATH_TO_EXECUTABLE
spctl --assess --type execute --verbose=2 PATH_TO_EXECUTABLE
```

The executable check uses `execute`, not `install`.
[Apple DTS](https://developer.apple.com/forums/thread/822378) recommends checking
the real installation/run experience as well. Run clean-Mac tests through the
documented Terminal/BB workflow; Finder double-clicks of raw CLI files follow a
[different Gatekeeper path](https://developer.apple.com/forums/thread/689337).

This validates exact inventory, provenance, every Apple signature/Team ID, and
`source=Notarized Developer ID` from Gatekeeper before writing
`stable-manifest.json`. It does not sign, submit to Apple, upload, publish, or
change the repository feed. Missing evidence or unsigned binaries fail closed.

After separately authorized publication, verify both uploaded asset hashes and
a clean installation before replacing the disabled `codex-release.json` feed
with the stable manifest. Keep updater installation manual until the rollout has
proven reliable. Never rewrite an already published version's bytes.

## Checks

```bash
python3 -m unittest discover -s tests -p test_codex_release.py -v
python3 -m unittest discover -s tests
```

Packaging tests use no signing credentials, no internet, and no installed Codex
changes. Live lifecycle tests should consume an explicit local manifest in an
isolated temporary installation.

## Fresh recipe verification — 2026-09-08

The actual `source`, `build`, notice collector, and `prepare-local` commands
completed on Apple Silicon, Rust 1.97.1, SDK 15.5. A clean Cargo target finished
the release build in **14m54s**. Five startup checks passed: CLI version,
app-server help, code-mode-host help, rg version, and zsh version. The complete
prepared-source fingerprint remained unchanged after removing this run's Python
bytecode cache; the recipe now disables generation of that cache.

- Fresh unsigned CLI SHA256: `136daa931e660ec967ffb45c817f23cd58e58d9c16861af68a8024df69e38f07`.
- Final package: 175,504,166 bytes; SHA256 `517b82b1a12114131ec8c8e2094eabd9b54a16f8d017f9215b12009526983400`.
- Modified source: 17,887,521 bytes; SHA256 `4b90bc744f397994799e4fecd26d766920a77d205bb41d27b0888e5a325fb90b`.
- The source archive exactly matches an independent earlier preparation.
- The package contains 666 files and an inventory of 1,013 dependency packages,
  with 13 unresolved review items preserved. This is **not license clearance**.

Private evidence files are `source.log`, `build.log`, `startup-result.json`,
`license-inventory-final.log`, `prepare-final.log`, and `artifact-validation.json`
in the thread's `codex-distribution-fresh-build` directory. This unsigned build
is separate from the previously validated installed binary. Signing, a fresh-Mac
release run, physical wake, and final license review remain public-release gates.
