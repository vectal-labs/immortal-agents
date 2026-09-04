# 0005 — Run agents inside cmux first

## Context

Resuming a stopped session means delivering a "keep going" prompt to it. How we deliver it depends entirely on where the agent sessions run (plain terminal, tmux, cmux, etc.). We should design for one real setup first, not all possible setups.

## Decision

We will first begin by running the observed agent sessions inside cmux — not anything else. Detection and prompt delivery target cmux sessions first.

## Consequences

- Prompt delivery can use cmux's API/CLI to send input to a specific pane, which is much more reliable than generic UI automation.
- Other environments (plain terminals, tmux, etc.) are out of scope until the cmux version works.
