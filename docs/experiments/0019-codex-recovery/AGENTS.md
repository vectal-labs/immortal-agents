# Codex recovery experiment

- These fixtures start isolated Codex processes and loopback-only mock servers.
- Use a disposable `CODEX_HOME` and workspace. Never use real project threads.
- `recovery_e2e.py` may suspend only its own child process. It must not sleep the Mac or change network settings.
- Tool tests use a fixed local printf command and keep Codex sandboxing enabled.
- `pilot.py` starts and cleans up a separate BB instance. Its generated state can contain machine credentials; never commit `runs/`.
- `live-probe.py` authenticates only to ChatGPT, sends control frames, and writes no credentials or inference requests.
- Keep the paired experiment write-up accurate about simulated failures versus physical sleep/wake.
