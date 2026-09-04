# 0028 — Defer the Codex resume mechanism until experiment 0003

## Context

A network-hung Codex reportedly ignores Esc + retyping (openai/codex #17433);
reviving it may require killing the process and `codex resume`. All reports are
from older versions than the installed 0.149.

## Decision

Decide nothing yet (David, 2026-08-23). Experiment 0003 (real cut on live
Codex sessions, David-launched) must first show how v0.149 actually dies and
what actually revives it. Until then the watcher stays observe-only for Codex.

## Consequences

- No Codex resume code gets written on guesses; the experiment picks between
  Esc+retype, kill+`codex resume`, or observe-only.
