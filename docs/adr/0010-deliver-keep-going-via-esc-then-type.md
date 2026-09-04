# 0010 — Deliver "keep going" as ESC, then typed prompt, into the cmux pane

## Context

A session stopped by internet loss is in one of two states: showing an API error and waiting at the prompt (easy), or silently hung mid-stream (stuck). Typing alone only fixes the first. Killing the process and running `claude --resume` always works but loses live terminal state. Doing nothing but notifying the user defeats the purpose.

## Decision

Deliver the resume through the session's cmux pane: press ESC first, then type "keep going" and Enter. ESC interrupts a hung stream and is harmless when the session is just sitting at an error, so one sequence covers both states.

## Consequences

- Depends on cmux input delivery (ADR 0005); the live terminal session is preserved.
- The experiment (ADR 0004) must confirm ESC actually interrupts a hung stream and is harmless on an idle-with-error session.
- Kill-and-resume remains a possible fallback later, not the default.
