# Stage 1b: history rewrite

Status: **mirror ready for review.** Nothing was pushed. The GitHub remote and the primary checkout are untouched.

- Mirror (bare, rewritten): `~/code/immortal-agents-rewrite`
- Scrub branch (original history, working-tree fixes only): `bb/stage-1b-scrub-private-data-rewrite-history-no-p-*` in the agent worktree
- Source of truth for what was private: `01-secrets-scan.md` (each finding now has a Resolution line)

## What was rewritten

Input: `origin/main` (including the rename commit that landed on GitHub during this work: "Rename to immortal-agents, add Pi support and provider-outage auto-revive") plus the scrub commits rebased on top of it (working-tree fixes, small logical commits). The mirror was cloned from GitHub with `git clone --mirror`, `main` was pointed at the last scrub commit, and every other ref (`bb/*`, `fix/*`, remote-tracking) was deleted before filtering. Only `refs/heads/main` survives; there are no tags.

`git filter-repo` ran twice on the mirror:

1. **Strip pass.** `--strip-blobs-with-ids` with the 137 blob IDs that ever lived under `docs/experiments/*-captures/` and are not in the final tree (raw Claude, Codex, Pi and bb captures, findings 1 to 4). `--invert-paths --path __pycache__/` removed the bytecode blob with the embedded private path (finding 8).
2. **Rewrite pass.** `--replace-text` and `--replace-message` with a list of literal and regex rules, plus `--path-rename` for ten path mappings whose old names carried private values (five bb fixtures, two Pi fixtures, three launchd plist names).

The rule list (61 lines) was **not committed** because it contains the private values themselves. It lives at `/tmp/claude-501/replace.txt` until reboot; it can be rebuilt from `01-secrets-scan.md`. Categories:

- 16 real bb thread IDs → the stable synthetic IDs used in docs and fixtures; regex catch-alls turn any leftover `thr_`, `env_`, `evt_`, turn ID, UUID, or opaque payload into `…redacted…`.
- Claude, Codex, Pi and ACP session IDs → `sess-*`; bb provider-thread and checkpoint IDs → `pt-*`, `ckpt-*`.
- `/Users/<user>` → `$HOME`; the Pi fixture folder → `--Users-operator-code--`; the worktree ID → `env_example`.
- The personal launchd prefix → `com.immortal-agents.`; the personal e-mail (only in captures) → `operator@example.com`.
- Adapter/service names, the USB interface, and the old Pi provider/model names → generic values.

Not rewritten on purpose: commit author/committer identity (public domain, David's call), the owner's first name in ADR decision attributions, and cmux surface numbers.

`git gc --prune=now --aggressive` was **not** run by the agent: the machine-wide dangerous-command guard blocks that command for agents. filter-repo already expired reflogs and repacked (`git fsck --unreachable` reports 0 objects, no loose objects). Run the gc yourself before pushing if you want it.

## Validation (fresh non-bare clone of the mirror)

| Check | Result |
|---|---|
| `gitleaks detect --source . --log-opts="--all" --redact --exit-code 1` | exit 0, no leaks |
| `git log --all -p \| grep -cE "<home-path>\|thr_[a-z0-9]{10}\|env_[a-z0-9]{10}\|<personal-launchd-prefix>"` (the literal home path and `com.<owner>` prefix, spelled out; not repeated here so this file stays clean) | 0 |
| `git log --all -p \| grep -c "<owner-domain>"` (the personal domain) | equals the number of `^Author:` lines (one per commit); no other matches |
| `git for-each-ref` | only `main` (plus the clone's own `origin/main`); no `bb/*` |
| `python3 -m unittest discover -s tests -p 'test_*.py'` at the new HEAD | all tests OK (76 at the time of writing) |
| Extra greps over `git log --all -p` for Codex `01a0…` IDs, adapter names, the USB interface, the old Pi fixture folder name, old Pi provider/model names, signed/encrypted payloads | 0 each |
| Tree of the mirror's `main` vs. the scrub branch tip | identical |

The exact numbers from the final run are in the agent's report; re-run the table yourself from a fresh clone before pushing.

## Commands for David (not run by the agent)

Review first:

```bash
git clone ~/code/immortal-agents-rewrite /tmp/immortal-agents-review
cd /tmp/immortal-agents-review && git log --oneline | head -20 && python3 -m unittest discover -s tests -p 'test_*.py'
```

Force-push the mirror and delete the old remote branches:

```bash
cd ~/code/immortal-agents-rewrite
git gc --prune=now --aggressive                      # optional; agent was blocked from running it
git remote add origin https://github.com/vectal-labs/immortal-agents.git
git ls-remote --heads origin                        # note every branch other than main
git push --force origin main
git push origin --delete <each-other-branch>       # the old bb/* and fix/* branches listed above
```

If branch protection rejects the force-push, disable it temporarily on GitHub, push, re-enable. Old commits stay reachable on GitHub through cached URLs and pull-request refs until GitHub purges them; open a support ticket ("purge unreachable objects") if that matters. Anyone with an old clone keeps the old history.

Re-clone (do not reuse the old checkout; its history no longer matches):

```bash
git clone https://github.com/vectal-labs/immortal-agents.git ~/code/immortal-agents-clean
```

## The rename work from the primary checkout

The rename work that was uncommitted on the primary checkout when this stage started was committed and pushed to GitHub `main` by its author while the scrub was in progress. The scrub branch was rebased onto it, so nothing needs re-applying. What the scrub changed in that commit's content:

- its two new bb fixtures got synthetic IDs (`thr_piwifi01`, `thr_picap001`, invented event, turn and checkpoint IDs) and were renamed to match; `tests/test_host_bb.py` references updated
- its watcher plist name and label became `com.immortal-agents.watcher`; the netguard label `com.immortal-agents.netguard`; plist paths use the `/Users/operator` placeholder
- `docs/launchd.md` and ADR 0021 keep its new `~/.immortal-agents` and `~/code/immortal-agents` paths, written as `~`/`$HOME`

Rules for future commits so the open-source greps stay at 0: synthetic `thr_`/`evt_` IDs with at most 9 characters after the prefix, no literal home directory, no personal launchd prefix. Load the new label after `launchctl bootout gui/$(id -u)/<old-label>`.

Other branches that other agents hold (bb worktrees) were based on the old history. Rebase each onto the new `main` with `git rebase --onto origin/main <old-base>`, or re-create them from a fresh clone. Do not merge old-history branches into the new `main`; that would drag the private objects back in.

## Repeating the rewrite

If the scrub branch changes, redo the mirror from scratch instead of filtering twice:

```bash
rm -rf ~/code/immortal-agents-rewrite
git clone --mirror https://github.com/vectal-labs/immortal-agents.git ~/code/immortal-agents-rewrite
cd ~/code/immortal-agents-rewrite
git fetch <worktree-path> <scrub-branch>:refs/heads/scrub
git update-ref refs/heads/main refs/heads/scrub && git update-ref -d refs/heads/scrub
git for-each-ref --format='%(refname)' | grep -v '^refs/heads/main$' | xargs -n1 git update-ref -d
git remote remove origin
# strip list: every blob ever under docs/experiments/*-captures/ that is not in main's tree
for c in $(git rev-list main); do git ls-tree -r "$c" | awk '$4 ~ /-captures\// {print $3}'; done | sort -u > /tmp/cap_blobs.txt
git ls-tree -r main | awk '{print $3}' | sort -u > /tmp/head_blobs.txt
comm -23 /tmp/cap_blobs.txt /tmp/head_blobs.txt > /tmp/strip_blobs.txt
git filter-repo --force --strip-blobs-with-ids /tmp/strip_blobs.txt --invert-paths --path __pycache__/
git filter-repo --force --replace-text /tmp/replace.txt --replace-message /tmp/replace.txt \
  --path-rename <old>:<new> ...   # the ten renames listed above
```

Keep synthetic IDs shorter than the catch-all regexes (`thr_`/`evt_` plus at most 9 characters) or the rewrite pass will rename them too.
