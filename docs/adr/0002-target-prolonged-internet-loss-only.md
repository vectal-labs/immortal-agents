# 0002 — Target prolonged internet loss, not brief blips

## Context

CLI coding agents already have guardrails for brief internet loss. They retry automatically after a few seconds, a couple of times. A 5-second blip recovers on its own.

## Decision

We only solve internet loss longer than the agents' built-in retry windows — roughly a minute or longer, e.g. 30–60 minute outages. Brief blips are explicitly out of scope.

## Consequences

- The tool acts only after the network has been down long enough that built-in retries have given up.
- No need to race or duplicate the agents' own retry logic.
