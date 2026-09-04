# 0003 — Resume by sending a "keep going" prompt, only to affected sessions

## Context

Once internet is back, stopped sessions need to be resumed. Sending a prompt to the wrong session is dangerous: a session that was waiting for the user's review would charge ahead unsupervised.

## Decision

The moment the computer regains internet, send a short prompt — "keep going" — to the agent sessions that stopped. Send it ONLY to sessions that stopped mid-task specifically because of the internet loss, never to all sessions. Bias toward false negatives (missing a resume) over false positives (resuming the wrong session).

## Consequences

- Detection must reliably separate "stopped due to internet loss mid-task" from "finished" and "waiting for user input".
- A missed resume is acceptable; a wrong resume is not.
