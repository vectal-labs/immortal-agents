# Experiment 0017 — Codex 0.153.2 never gives up during a 92-minute cut

Date: 2026-09-04. Real Wi-Fi cut on commit `370b886`. David flips the
Wi-Fi radio off and on by hand under ADR 0037. The launchd watcher (pid
40572) runs unattended from the primary checkout. 111 tests green.

## Question

Experiment 0004 (Codex 0.149, local proxy) showed Codex dying at ~5 min
and writing `task_complete`, which ADR 0033 relies on. Experiment 0016
(21 min) saw no `task_complete`. Does Codex still give up at all?

## Setup

- cmux `workspace:10` (`0017 codex cut`), shared cwd `/tmp/oar-exp17-live`.
- Codex 0.153.2, direct OpenAI (no `openai_base_url` in config),
  `gpt-5.6-sol high fast`.
  - `surface:24` — Codex target A, 20-rewrite essay, chat only
  - `surface:25` — Codex target B, same task
  - `surface:26` — Codex control, answered `ready`, idle at prompt
- Capture: `experiments/0017-capture.sh` (50 min, every 20s) →
  `experiments/0017-captures/snapshots.log`. It ended at `13:54Z`,
  before recovery — the cut ran far longer than the planned 30 min.

## Timeline (UTC)

- `13:05:16` — both targets start their turn.
- `13:05:56` — connectivity lost (watcher offline at `13:06:06`).
- `13:11:04` — last rollout write for both targets (a `token_count`).
- From ~`13:12` on, both target panes show
  `• Reconnecting... waiting for network (Xm Ys • esc to interrupt)` /
  `└ Connection failed: error sending request`. The counter kept climbing
  (13m 08s at the last capture). No error banner, no idle prompt, no
  `task_complete`.
- `14:37:55` — connectivity restored after 5,519.6 s (92 min). APIs
  ready in 0 s.
- `14:37:56` — watcher decisions: all three Codex panes `skip`
  (`pane=other`, `task_complete_in_outage=0`, `pane_not_network_error`).
  Control `skip` is correct. No `keep going` sent anywhere. No false
  positives across ~25 other surfaces.
- `14:43:26` — target A writes `task_complete` with the full V1–V20 essay
  (103 k chars). Self-recovered.
- `14:44`–`14:47` — target B is re-streaming its response from `## V1`
  again (scrollback shows V1–V16, then V1–V7, then V1 restarting); rollout
  still being written. Alive, self-recovering, no watcher involvement.

## Result

Codex 0.153.2 direct-to-OpenAI does not give up on network loss. It
retries indefinitely with a visible `Reconnecting... waiting for network`
counter, then resumes the turn by itself once the network returns. The
0004 death mode (`task_complete` inside the outage, error at idle prompt)
did not reproduce in 21 min (0016) or 92 min (0017).

Consequences:

- The watcher has nothing to revive for Codex on a plain network cut.
  Its `skip` was the correct decision.
- ADR 0033's third signal (`task_complete` inside the outage) is now
  unreachable on this Codex version. It still protects against false
  positives. Whether to keep, relax, or retire it is David's call.
- `Reconnecting... waiting for network` is not in the Codex fingerprint
  list. It should not be: it means "alive and retrying", not dead.
