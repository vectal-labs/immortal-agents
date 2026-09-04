# 0006 — Claude Code CLI is the first agent to observe

## Context

Claude Code exists in several forms: the terminal CLI, the desktop app, and the VS Code extension. Each behaves differently on network loss and stores state differently. ADR 0001 already scopes us to Claude Code; this pins down which form.

## Decision

We will use the Claude Code CLI (the `claude` terminal command) as the first agent to observe — in the experiment (ADR 0004) and in the first working version.

## Consequences

- Detection targets the CLI's session JSONL files in `~/.claude/projects/` and the `claude` process.
- Desktop app and VS Code extension behavior is out of scope for now.
