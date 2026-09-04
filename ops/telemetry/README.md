# Telemetry server
Stdlib-only sink for anonymous client events (`immortal/core/telemetry.py`).
Deploy: Render > New > Blueprint, point at this repo; `render.yaml` here sets up the service and a 1 GB disk at `/data`.
Run locally: `DATA_DIR=/tmp PORT=8000 python3 ops/telemetry/server.py`, then point clients at it with `TELEMETRY_URL=http://localhost:8000/event`.
Read: `curl https://immortal-agents-telemetry.onrender.com/stats` returns counts by event type.
