#!/usr/bin/env python3
"""A local JSON-RPC fixture. Never calls a model or executes any tools."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

if "--version" in sys.argv:
    print("codex-cli 0.153.4-bb-pilot-mock")
    raise SystemExit(0)

log_path = Path(os.environ["BB_PILOT_RPC_LOG"])
thread_id = str(uuid.uuid4())


def emit(value):
    print(json.dumps(value), flush=True)


def notify(method, params):
    emit({"jsonrpc": "2.0", "method": method, "params": params})


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    with log_path.open("a") as log:
        log.write(json.dumps({"pid": os.getpid(), "method": method,
                              "isolated_codex_home": os.environ.get("CODEX_HOME"),
                              "time": time.time()}) + "\n")
    if "id" not in message:
        continue
    params = message.get("params") or {}
    result = {}
    if method == "initialize":
        result = {"userAgent": "bb-pilot-mock/0.1"}
    elif method == "model/list":
        result = {"data": [{"id": "bb-pilot-mock", "model": "bb-pilot-mock",
                    "displayName": "Local fixture (no inference)", "description": "Offline fixture",
                    "hidden": False, "supportedReasoningEfforts": [{"reasoningEffort": "medium", "description": "Fixture"}],
                    "defaultReasoningEffort": "medium", "isDefault": True,
                    "inputModalities": ["text"], "supportsPersonality": False}], "nextCursor": None}
    elif method == "account/read":
        result = {"account": None, "requiresOpenaiAuth": False}
    elif method in ("thread/start", "thread/resume"):
        thread_id = params.get("threadId", thread_id)
        result = {"thread": {"id": thread_id}}
    elif method == "turn/start":
        turn = {"id": str(uuid.uuid4()), "status": "inProgress", "items": [], "error": None}
        result = {"turn": turn}
    emit({"jsonrpc": "2.0", "id": message["id"], "result": result})
    if method == "turn/start":
        notify("turn/started", {"threadId": thread_id, "turn": turn})
        if os.environ.get("BB_PILOT_SIMULATE_RETRY") == "True":
            notify("error", {"threadId": thread_id, "turnId": turn["id"], "willRetry": True,
                            "error": {"message": "Reconnecting... 1/1", "additionalDetails": "websocket heartbeat timed out waiting for pong", "codexErrorInfo": "other"}})
        item = {"type": "agentMessage", "id": str(uuid.uuid4()), "text": "BB_PILOT_WIRING_OK", "phase": "final_answer"}
        notify("item/started", {"threadId": thread_id, "turnId": turn["id"], "item": item})
        notify("item/agentMessage/delta", {"threadId": thread_id, "turnId": turn["id"], "itemId": item["id"], "delta": item["text"]})
        notify("item/completed", {"threadId": thread_id, "turnId": turn["id"], "item": item})
        notify("turn/completed", {"threadId": thread_id, "turn": {**turn, "status": "completed", "items": [item]}})
