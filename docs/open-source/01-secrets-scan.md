# Stage 1 secrets and private-data scan

Status: **RESOLVED in stage 1b** (working tree scrubbed, history rewritten in a mirror; see `02-history-rewrite.md`). Original status: 1 high, 7 medium, 1 low.

No confirmed reusable credential was found. Publishing is still blocked because private data is present in reachable history. Removing it fully requires a coordinated history rewrite.

## Scope and snapshot

- Scanned on 2026-09-03.
- Working-tree HEAD: `0fa5ef8`.
- Full-history scan: all 58 commits reachable from all refs. `origin/main` advanced to `4a0cd98` during the scan and was included.
- Checked tracked files, ignored working-tree content, all Git refs, commit metadata, deleted files, capture folders, fixtures, and `launchd/`.
- Examples below are redacted after four characters. No full private value is repeated here.

## Findings

### 1. High — raw Claude session and account data

- **Files:** `docs/experiments/0001-captures/**`, specifically 10 pane snapshots, 8 `jsonl_tail.jsonl` files, 8 `claude_ps.txt` files, and the run logs.
- **Commits:** `80d2916`, `128a6f7`.
- **Data:** account email `davi…@davi…`, user name, complete prompts, process output, private skill inventory, local paths, session/request/message IDs, and opaque signed-thinking payloads beginning `CAIS…`.
- **Proposed fix:** replace the raw captures with small synthetic fixtures containing only the required failure signals. Remove the originals from the current tree and rewrite them out of all history. Treat the opaque payloads as private even though no evidence shows they are reusable credentials.
- **Resolution (stage 1b):** `docs/experiments/0001-captures/` now holds hand-written stand-ins (dead/retrying pane text, a five-line synthetic JSONL tail, generic outage log). Every raw blob was stripped from all history by blob ID; the opaque signed payloads are gone with them.

### 2. Medium — raw Codex session captures

- **Files:** `docs/experiments/0003-captures/rollouts_before.txt`; all 93 `pane_*.txt` and 93 `rollout-*_tail.txt` files under `docs/experiments/0004-captures/**`; related IDs in `docs/experiments/0003-*.md` and `docs/experiments/0004-*.md`.
- **Commits:** `b1e3439`, `6c2fb43`.
- **Data:** real session/turn IDs beginning `01a0…`, prompts, local paths, endpoint/error records, timestamps, and token-usage records.
- **Proposed fix:** keep only synthetic, minimal failure records with invented IDs and paths. Remove the raw panes and rollout tails, then rewrite them out of history.
- **Resolution (stage 1b):** `0003-captures/` and `0004-captures/` reduced to synthetic rollout listings, one "working" and one "dead" pane, and a two-line synthetic rollout tail with invented session/turn IDs. Raw panes and rollout tails stripped from all history; the rollout-file names with real IDs vanished with them.

### 3. Medium — bb thread and provider identifiers

- **Files:** 76 `thr_….events` and 76 `thr_….status` files under `docs/experiments/0007-captures/**`; `docs/adr/0036-bb-as-second-host.md`; `docs/experiments/0006-bb-live-cut.md`; `docs/experiments/0007-bb-revive-and-codex.md`; `docs/experiments/0011-capture.sh`; `docs/experiments/0011-cursor-wifi-cut-fingerprints.md`; `tests/fixtures/bb_thr_….json`; and `tests/test_host_bb.py`. One additional fixture exists in `4a0cd98` on `main`.
- **Commits:** `9f810c9`, `3ad163d`, `daa4789`, `682804b`, `e853a50`, `31b6e75`, `4a0cd98`.
- **Data:** 14 observed thread IDs beginning `thr_…`, plus provider-thread, checkpoint, turn, and event IDs.
- **Proposed fix:** replace all real identifiers with stable synthetic values in docs and fixtures. Remove the capture directory and rewrite the original values out of history.
- **Resolution (stage 1b):** every real thread ID has a stable synthetic value (`thr_claudea01`, `thr_login0001`, `thr_latedns01`, `thr_e06…`, `thr_e07…`, `thr_e11…`, `thr_olderr001`); event, turn, provider-thread and checkpoint IDs are invented. `0007-captures/` keeps two synthetic snapshots. History: literal map plus a catch-all regex for any `thr_`/`evt_`/turn ID, and fixture files renamed in every commit.

### 4. Medium — Pi and terminal session state

- **Files:** both `tests/fixtures/pi/--Users-davi…/*.jsonl` files; `tests/test_detect_pi.py`; both files in `docs/experiments/0011-captures/**`; `docs/experiments/0013-captures/step0-snapshots.log`; and the related 0011/0013 capture scripts and write-ups.
- **Commits:** `8d5bf26`, `e853a50`, `31b6e75`, `0fa5ef8`; extra reachable cmux snapshots include `66788a3`, `77c24e4`, and `986c9ec`.
- **Data:** session IDs, local paths, exact user prompts, model/provider names, token/cost telemetry, surface IDs, TTYs, and timestamps.
- **Proposed fix:** create purpose-built synthetic fixtures and a short redacted experiment summary. Remove raw terminal snapshots and rewrite them out of history.
- **Resolution (stage 1b):** Pi fixtures regenerated under `tests/fixtures/pi/--Users-operator-code--/` with invented session IDs, generic provider/model names and cwd `/Users/operator/code`. `0011-captures/` and `0013-captures/` hold synthetic pane text only. Old fixture blobs rewritten (IDs, provider, model) and renamed throughout history; terminal snapshots stripped.

### 5. Medium — personal email in commit metadata

- **Files:** Git author and committer metadata; the same address also appears in the 10 Claude pane files covered by finding 1.
- **Commits:** every one of the 58 commits reachable through `--all`; earliest commit `f40ab09`.
- **Data:** `davi…@davi…` and the matching full author name. `curs…@curs…` appears once as a service co-author address and is not David's private data.
- **Proposed fix:** decide whether the author identity is intentionally public. If not, configure a public/noreply identity for future commits and rewrite author/committer metadata across all reachable history.
- **Resolution (stage 1b):** kept on purpose. The author identity uses the owner's public domain and stays as is; no author/committer metadata was rewritten. The address still appears in the 10 pane captures, which were stripped from history.

### 6. Medium — personal home paths and launchd identity

- **Files:** `detect_pi.py`; `tests/test_detect_pi.py`; `docs/adr/0021-launchd-runs-from-primary-checkout.md`; `docs/experiments/0002-jsonl-mapping-and-silent-hang.md`; both `launchd/com.….plist` files. Raw-capture occurrences are covered by findings 1–4.
- **Commits:** `80d2916`, `a967488`, `8fc6012`, `355dac0`, `8d5bf26`.
- **Data:** `/Users/davi…`, a worktree ID beginning `env_…`, and reverse-DNS labels beginning `com.…`.
- **Proposed fix:** use `$HOME`, generated install paths, neutral fixture paths, and a project-owned launchd label. Rewrite the old literal values out of history.
- **Resolution (stage 1b):** `/Users/<user>` paths are now `$HOME`/`~` in docs and scripts, `/Users/operator` as a documented placeholder in the plists and fixtures; the worktree ID is `env_example`. launchd labels are `com.immortal-agents.watcher` and `com.immortal-agents.netguard` (files renamed in every commit). History: literal replacements plus `env_` catch-all.

### 7. Medium — personal and machine-specific operational details

- **Files:** `AGENTS.md`; ADRs 0015–0043; experiment write-ups 0001–0013; `docs/experiments/0001-captures/outage.log`; `docs/experiments/0001-wifi-kill.md`; `docs/incidents/0001-launchctl-submit-cut-loop.md`; and `docs/launchd.md`.
- **Commits:** first introduced in `80d2916` and `a967488`; expanded through `e853a50` and `31b6e75`.
- **Data:** the owner's first/full name, the USB ethernet and phone-tethering network service names, the USB interface name, plus real process IDs, TTYs, surface IDs, and detailed local incident timestamps.
- **Proposed fix:** replace the person's name with “operator” where ownership is not important. Generalize hardware, process, and terminal identifiers while preserving the technical lesson. Rewrite the original values out of history if they must remain private.
- **Resolution (stage 1b):** owner name replaced by "operator" where ownership does not matter (experiment write-ups, incident, scripts, machine descriptions in ADRs). ADR decision attributions and approval rules keep the name on purpose, as does commit authorship. Adapter/service names, the USB interface, PIDs and TTYs are generalised; cmux surface numbers stay (ephemeral pane indexes of throwaway workspaces). History: adapter and interface names replaced; the first name was not rewritten out of history because it remains public via authorship.

### 8. Medium — deleted bytecode retains an absolute private path

- **File:** historical-only `__pycache__/detect.cpython-314.pyc`.
- **Commits:** added in `80d2916`, deleted in `a967488`.
- **Data:** embedded source path `/Users/davi…/.bb/worktrees/env_…/offline-agent-restart/detect.py`.
- **Proposed fix:** rewrite this blob out of history. Keep `__pycache__/` ignored.
- **Resolution (stage 1b):** `__pycache__/` removed from every commit with a path filter. `__pycache__/` and `*.pyc` stay ignored.

### 9. Low — Git ref names expose bb thread IDs

- **Files:** Git refs, not working-tree files: five local `refs/heads/bb/*-thr_…` names and one matching remote-tracking ref.
- **Commits:** ref tips are `0fa5ef8`, `1547a1b`, and `80d2916`.
- **Data:** six ref-name occurrences containing private bb thread IDs.
- **Proposed fix:** rename or remove the private refs before publication and push only the intended public branches.
- **Resolution (stage 1b):** the mirror carries only `refs/heads/main`. All `bb/*`, `cmux/*` and the merged `fix/*` refs were dropped before the rewrite. The matching remote branches must be deleted on GitHub with the push commands in `02-history-rewrite.md`.

## Confirmed absent

- Gitleaks found no API key, access token, password, cookie, SSH/private key, or other recognized credential in history or the working tree.
- No `.env` file or `.env` contents were found in tracked paths or reachable history.
- No private webhook URL was found. The Discord webhook is read from a file outside the repo or from `DISCORD_WEBHOOK_URL`.
- No Wi-Fi SSID, BSSID, real hostname, or private URL was found. Public API URLs and localhost test URLs are intentional.
- `private/` is ignored by `.gitignore`, is absent from this worktree, and has never appeared as a committed path or reachable Git object.

## Final grep disposition

The required keyword grep is fully accounted for by these groups:

- `detect*.py`, `procs.py`, and `sim/proxy.py`: `token` means a parsed string or protocol token. False positive.
- `host_*.py`, `logbook.py`, `sim.py`, `watcher.py`, and tests: matches occur inside `os.environ` or ordinary variable names. False positive.
- `notify.py`: “secret” is a safety comment and `DISCORD_WEBHOOK_URL` is only an environment-variable name. No value is stored. False positive.
- Experiment prose outside captures: “tokens” describes token counts. False positive.
- `docs/experiments/0001-captures/**` and `docs/experiments/0004-captures/**`: keyword matches are inside raw session records. These are true private-data findings 1 and 2, but no credential value was found.
- `docs/experiments/0007-captures/**` and `tests/fixtures/**`: token-count/session fields are covered by findings 3 and 4. No credential value was found.
- This report: expected documentation matches only.
- There are no `BEGIN RSA`, `BEGIN OPENSSH`, or password-value hits.

## Validation results

- `gitleaks detect --source . --log-opts="--all" --redact -v`: exit 0; no leaks.
- `gitleaks detect --no-git --source .`: exit 0; no leaks.
- Repeated `gitleaks detect --source . --log-opts="--all" --redact --exit-code 1` after each pass: exit 0.
- Targeted `git log -p --all -S` searches covered email, home paths, thread IDs, webhook variables, `.env`, SSH key headers, `private/`, and SSIDs.
- Manual Git-object inspection found the historical bytecode path that text scanners missed.

## Scan checklist

1. Read `AGENTS.md`, `.gitignore`, `README.md`, and the incident report. Inventory `git ls-files` and `git log --oneline --all`.
2. Run `gitleaks detect --source . --log-opts="--all" --redact -v`.
3. Run `gitleaks detect --no-git --source .`.
4. Search the working tree and `git log -p --all -S` for emails, `/Users/`, `/home/`, `.env`, credentials, private URLs, hostnames, SSIDs, `thr_`, UUIDs, and session-state fields.
5. Inspect `docs/experiments/*-captures/`, `tests/fixtures/`, `launchd/`, commit metadata, refs, and deleted/binary objects.
6. Confirm `git check-ignore -v private/` succeeds and `git log --all -- private/` is empty.
7. Run `gitleaks detect --source . --log-opts="--all" --redact --exit-code 1` and `git grep -inE "(api[_-]?key|secret|token|password|BEGIN (RSA|OPENSSH)|\.env)"`. Explain every hit before publishing.

## Required decision

Choose one before stage 2:

- Approve a coordinated history rewrite and ref cleanup after synthetic replacements are ready; or
- Explicitly accept that the private data above will become public.

No history rewrite, deletion, credential rotation, commit, or push was performed during this scan.
