# Experiment 0003 — Codex outage staging plan

Prepared only. David launches the network cut later. Agents never run an
outage command or type into these panes.

## Stage

- Confirm Codex hooks populate `codex-hook-sessions.json` for sessions launched
  through `cx`.
- Five Codex CLI panes in cmux, shared cwd (proves surface-to-session
  isolation):
  - **Panes 1-3 (revival arm):** three IDENTICAL long mid-task sessions,
    streaming when the cut fires. After the cut, each gets a different
    treatment — pane 1: touch nothing (control — self-recover or hang
    forever?); pane 2: Esc + retype the task; pane 3: kill the process, then
    `codex resume`. One run answers ADR 0028 (what revives it) and ADR 0030
    (loud or silent death).
  - **Pane 4 (finished):** complete a short task, leave the clean prompt idle.
  - **Pane 5 (waiting):** stop at a real command or file-change approval
    prompt.
- David performs and timestamps the three revival treatments after restore.
  Keep observing well after restore — the reported hang outlives the outage.

## Capture

- Before cut: UTC time, surface IDs and titles, exact prompts, pane text, hook
  mappings, rollout paths, and rollout JSONL tails for every pane.
- During outage: capture pane text, rollout tails, and UTC timestamps at 30,
  60, and 120 seconds, then immediately before restore.
- After restore: capture each pane before treatment, immediately after its
  treatment, and at 1, 3, 5, 10, and 20 minutes. Continue while the untouched
  control remains hung.
- For pane 3, record the killed process/session, `codex resume` result, and how
  the original and resumed rollout files relate.
- Preserve watcher decision lines. Mark David's manual treatments separately
  and confirm the watcher sent no Codex input.

## Expected proof

- Panes 1-3 map to their own rollouts and expose whether death is loud or
  silent in both pane text and JSONL.
- ADR 0028: compare untouched control, Esc + retype, and kill + `codex resume`
  to show which method actually revives Codex v0.149.
- ADR 0030: show whether the control remains hung well after connectivity
  returns and whether any error record eventually appears.
- The finished pane logs `finished_clean` from `event_msg/task_complete`.
- The approval pane logs `waiting_user`.
- Ambiguous or unmapped panes skip with a reason. Codex remains observe-only.
