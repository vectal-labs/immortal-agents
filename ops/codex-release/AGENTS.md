# Codex release tooling
- `release.py` prepares pinned source, builds complete packages, and emits distribution manifests.
- `license_inventory.py` collects dependency notices and source evidence; it never grants legal clearance.
- Local artifacts are explicitly untrusted. Stable promotion requires evidence for the exact archive and verified Apple signatures.
- Never sign, notarize, publish, or replace the installed Codex implicitly.
- Keep manifest schema aligned with `immortal/core/codex_recovery/`; test archive boundaries and failed promotion.
- Do not archive personal configuration, credentials, build caches, or `.git` directories.
