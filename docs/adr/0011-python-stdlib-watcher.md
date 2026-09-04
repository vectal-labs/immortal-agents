# 0011 — Write the watcher in Python 3, stdlib only

## Context

The watcher must HTTP-probe connectivity, parse session JSONL tails, call the cmux CLI, and keep a small state log. Candidates: bash+jq, Python 3 stdlib, TypeScript/Node, Swift. Bash gets fragile for state machines and JSON. Node and Swift add toolchain weight to something that must run untouched forever.

## Decision

Write the watcher in Python 3 using only the standard library. One file if possible, no dependencies, run directly by launchd.

## Consequences

- Clean JSON/JSONL handling and readable decision logic; easy to test.
- No pip installs, no node_modules, no build step — nothing to break over time.
- Must use the system/dev-tools Python 3; pin the launchd plist to an absolute interpreter path.
