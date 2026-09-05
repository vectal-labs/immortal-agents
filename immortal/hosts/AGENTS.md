# Host adapters
- Adapters enumerate targets, read their state, and submit recovery actions. Policy belongs in detectors or `core/bb_recovery.py`.
- bb submission failures must match the latest request. New work and successful/interrupted turns invalidate older failures.
- Before retry/reset, recheck status, request identity, queued messages, and interactions.
- Use `bb thread retry --turn` for rejected input. Preserve bb's original input and acceptance record.
- Keep the existing terminal/Cursor recovery contracts intact.
- `host-daemon-restarted` recovery requires matching accepted input, interrupted turn, and display error. Check the owning machine is connected, then use guarded `retry --turn`; BB continues accepted input without replaying it (`bb guide threads`).
