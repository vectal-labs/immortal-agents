# 0004 — Run the Wi-Fi kill experiment before extensive building

## Context

We don't actually know what Claude Code does when the internet drops mid-task. Research shows three possible behaviors: visible retry-then-error, silent hang, or silent stop. Which one dominates in practice decides our whole detection and resume design. Building on assumptions risks solving the wrong problem.

## Decision

Before extensive building, run the experiment first: start a real Claude Code task, kill Wi-Fi mid-turn, and observe what the terminal shows and what the session JSONL records. Document the observed behavior in this repo. Design detection and resume based on what we learn.

## Consequences

- No watchdog or resume code gets built until the experiment results are documented.
- Detection logic will be grounded in observed behavior, not guesses.
