import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from immortal.core.provider_endpoint import recovery_endpoint
from immortal.hosts import bb


class ProviderEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "codex"
        self.home.mkdir()
        self.cwd = Path(self.tmp.name) / "project"
        self.cwd.mkdir()
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("CREATE TABLE threads(id TEXT, model_provider TEXT, cwd TEXT)")
            db.execute("INSERT INTO threads VALUES (?, ?, ?)", ("session", "openai", str(self.cwd)))
            db.commit()
        (self.home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}))
        self.events = [{"type": "thread/identity", "data": {"providerThreadId": "session"}}]

    def resolve(self, provider="codex"):
        return recovery_endpoint(provider, self.events, codex_home=self.home)

    def env(self, **values):
        self.events.append({"type": "provider.env-resolved", "data": {
            "entries": [{"name": k, "value": v} for k, v in values.items()]}})

    def test_subscription_uses_chatgpt(self):
        self.assertEqual(self.resolve(), "https://chatgpt.com/backend-api/codex/responses")

    def test_api_auth_uses_openai_api(self):
        (self.home / "auth.json").write_text('{"auth_mode":"apikey"}')
        self.assertEqual(self.resolve(), "https://api.openai.com/v1/responses")

    def test_configured_custom_route(self):
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("UPDATE threads SET model_provider='gateway'")
            db.commit()
        (self.home / "config.toml").write_text(
            'model_provider="gateway"\n[model_providers.gateway]\nbase_url="https://gateway.example/v1"\n')
        self.assertEqual(self.resolve(), "https://gateway.example/v1/responses")

    def test_api_override_uses_latest_event(self):
        (self.home / "auth.json").write_text('{"auth_mode":"apikey"}')
        self.env(OPENAI_BASE_URL="https://old.example/v1")
        self.env(OPENAI_BASE_URL="https://new.example/v2")
        self.assertEqual(self.resolve(), "https://new.example/v2/responses")

    def test_does_not_use_watcher_environment(self):
        with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://wrong.example/v1"}):
            self.assertEqual(self.resolve(), "https://chatgpt.com/backend-api/codex/responses")

    def test_new_identity_must_have_own_evidence(self):
        self.events.append({"type": "thread/identity", "data": {"providerThreadId": "missing"}})
        self.assertIsNone(self.resolve())

    def test_latest_runtime_identity_overrides_original_identity(self):
        self.events.append({"type": "provider/error", "data": {"providerThreadId": "missing"}})
        self.assertIsNone(self.resolve())

    def test_already_complete_endpoint_is_not_duplicated(self):
        (self.home / "config.toml").write_text(
            'chatgpt_base_url="https://chatgpt.example/backend-api/codex/responses"')
        self.assertEqual(self.resolve(), "https://chatgpt.example/backend-api/codex/responses")

    def test_project_config_stays_unknown(self):
        (self.cwd / ".codex").mkdir()
        (self.cwd / ".codex/config.toml").write_text('model_provider="gateway"')
        self.assertIsNone(self.resolve())

    def test_older_python_config_support_stays_unknown(self):
        (self.home / "config.toml").write_text('model_provider="openai"')
        with patch("immortal.core.provider_endpoint.tomllib", None):
            self.assertIsNone(self.resolve())

    def test_explicit_loopback_route(self):
        (self.home / "config.toml").write_text(
            '[model_providers.openai]\nbase_url="http://127.0.0.1:54321/v1"')
        self.assertEqual(self.resolve(), "http://127.0.0.1:54321/v1/responses")

    def test_changed_provider_configuration_stays_unknown(self):
        (self.home / "config.toml").write_text('model_provider="gateway"')
        self.assertIsNone(self.resolve())

    def test_claude_requires_explicit_endpoint(self):
        self.assertIsNone(self.resolve("claude-code"))
        self.env(ANTHROPIC_BASE_URL="https://anthropic.example")
        self.assertEqual(self.resolve("claude-code"), "https://anthropic.example/")

    def test_local_session_metadata_is_never_used_for_another_machine(self):
        data = Path(self.tmp.name) / 'bb'
        data.mkdir()
        (data / 'host-id').write_text('local-host')
        self.env(CODEX_HOME=str(self.home))
        target = {'ref': 'test-thread', 'harness_hint': 'codex', 'environment_host_id': 'remote-host'}
        with patch.dict('os.environ', {'BB_DATA_DIR': str(data)}), \
                patch.object(bb, '_thread_events', return_value=self.events) as read:
            self.assertIsNone(bb.recovery_endpoint(target))
            read.assert_not_called()
            target['environment_host_id'] = 'local-host'
            self.assertEqual(bb.recovery_endpoint(target), 'https://chatgpt.com/backend-api/codex/responses')
            read.assert_called_once_with('test-thread')

    def test_explicit_alternate_backend_or_proxy_does_not_use_direct_route(self):
        for key in ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY', 'HTTPS_PROXY'):
            with self.subTest(key=key):
                self.env(ANTHROPIC_BASE_URL='https://anthropic.example', **{key: '1'})
                self.assertIsNone(self.resolve('claude-code'))
        self.env(HTTPS_PROXY='https://proxy.example')
        self.assertIsNone(self.resolve())

    def test_unsafe_or_malformed_urls_are_unknown(self):
        for url in ("https://user:secret@example.com", "https://example.com?key=secret",
                    "http://example.com", "https://example.com:bad", "https://example.com:\n"):
            with self.subTest(url=url):
                self.env(ANTHROPIC_BASE_URL=url)
                self.assertIsNone(self.resolve("claude-code"))

    def test_unknown_routes_and_corrupt_auth_stay_unknown(self):
        for provider in ("pi", "acp-cursor", "other"):
            self.assertIsNone(self.resolve(provider))
        (self.home / "auth.json").write_text("bad json")
        self.assertIsNone(self.resolve())
