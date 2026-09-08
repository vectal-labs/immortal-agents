"""Resolve evidenced BB provider routes; missing or ambiguous routes stay unknown."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
try:
    import tomllib
except ImportError:
    tomllib = None

from immortal.core.ready import safe_endpoint as _safe_url

_ALTERNATE_BACKENDS = ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY')
_PROXY_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')
_ROUTE_KEYS = ('CODEX_HOME', 'OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', *_ALTERNATE_BACKENDS, *_PROXY_KEYS)


def _metadata(events):
    identity, env = None, {}
    for event in events:
        data = event.get("data", {})
        if data.get("providerThreadId"):
            identity = data.get("providerThreadId")
        if event.get("type") == "provider.env-resolved":
            # This event describes configured overrides, not the bridge's full
            # inherited environment. Never substitute the watcher's environment.
            env = {entry.get("name"): entry.get("value") for entry in data.get("entries", [])
                   if isinstance(entry, dict) and entry.get("name") in _ROUTE_KEYS}
    return identity, env


def _codex_endpoint(identity, env, home):
    if not isinstance(identity, str) or not identity:
        return None
    database = home / "state_5.sqlite"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
        row = connection.execute(
            "SELECT model_provider, cwd FROM threads WHERE id = ?", (identity,),
        ).fetchone()
    if not row:
        return None
    provider, cwd = row
    config_path = home / "config.toml"
    if config_path.exists() and tomllib is None:
        return None
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    # Codex supports layered project config. Guessing its trust/precedence here
    # would turn an unrelated server into a false readiness signal.
    workspace = Path(cwd)
    for parent in (workspace, *workspace.parents):
        local_config = parent / ".codex" / "config.toml"
        if local_config != config_path and local_config.exists():
            return None
    if config.get("profile"):
        return None
    if config.get("model_provider", "openai") != provider:
        return None
    settings = config.get("model_providers", {}).get(provider, {})
    if settings.get("wire_api", "responses") != "responses":
        return None
    if "base_url" in settings:
        return _safe_url(settings["base_url"])
    if provider != "openai":
        return None
    auth = json.loads((home / "auth.json").read_text())
    mode = auth.get("auth_mode")
    if mode == "chatgpt":
        if "OPENAI_BASE_URL" in env:
            return None  # Its interaction with subscription auth is ambiguous.
        return _safe_url(config.get("chatgpt_base_url", "https://chatgpt.com/backend-api/codex"))
    if mode in ("apikey", "apiKey"):
        return _safe_url(env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    return None


def recovery_endpoint(provider, events, *, codex_home=None):
    """Return a safe probe URL, or None without logging config/credential data.

    Codex's local session index identifies its configured model provider; auth
    metadata distinguishes subscription from API routing. BB does not expose the
    full inherited provider environment; local config is evidence, not a complete
    effective-config API. Unknown/layered routes keep the existing recovery path.
    """
    try:
        identity, env = _metadata(events)
        if any(env.get(key) for key in _PROXY_KEYS):
            return None  # The watcher cannot authenticate or reproduce this proxy route.
        if provider == "claude-code":
            if any(str(env.get(key, '')).lower() not in ('', 'false', '0') for key in _ALTERNATE_BACKENDS):
                return None
            return _safe_url(env.get("ANTHROPIC_BASE_URL"))
        if provider != "codex":
            return None
        home = Path(codex_home or env.get("CODEX_HOME") or Path.home() / ".codex")
        if not home.is_absolute():
            return None
        endpoint = _codex_endpoint(identity, env, home)
        if endpoint and not endpoint.rstrip("/").endswith("/responses"):
            endpoint = endpoint.rstrip("/") + "/responses"
        return endpoint
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
        return None
