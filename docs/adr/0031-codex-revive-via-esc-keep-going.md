# 0031 — Revive Codex with ESC + "keep going" (resolves ADR 0028)

## Context

Experiment 0004: after a 900s cut, Codex v0.149 gave up at ~4m50s with a
loud `502 Provider unreachable` error and sat idle at its prompt for 10+
hours. Typing "keep going" + Enter revived it instantly; kill + `codex resume`
also worked but took three steps and hit an update prompt.

## Decision

Deliver the Codex revive exactly like Claude's (ADR 0010): ESC, then type
"keep going", then Enter into the cmux pane (David, 2026-08-27). ESC is
harmless on an idle Codex prompt; one shared delivery path for both harnesses.

## Consequences

- No kill/resume logic; `watcher.resume()` serves both harnesses.
- Supersedes the deferral in ADR 0028.
