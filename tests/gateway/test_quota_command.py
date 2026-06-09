"""Tests for gateway /quota command credential forwarding."""

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_quota_command_forwards_runtime_api_key(monkeypatch):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._session_model_overrides = {}
    runner._normalize_source_for_session_key = MagicMock(side_effect=lambda source: source)
    runner._session_key_for_source = MagicMock(return_value="agent:main:telegram:private:123")

    event = MagicMock()
    event.get_command_args.return_value = ""
    event.source = MagicMock()

    monkeypatch.setattr(
        "gateway.run._load_gateway_config",
        lambda: {"model": {"default": "gpt-5.5", "provider": "openai-codex"}},
    )
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda: {"api_key": "fresh-token", "provider": "openai-codex", "model": "gpt-5.5"},
    )

    captured = {}

    async def fake_fetch_quota_async(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "model": kwargs["model"], "provider": kwargs["provider"]}

    monkeypatch.setattr("hermes_cli.quota.fetch_quota_async", fake_fetch_quota_async)
    monkeypatch.setattr("hermes_cli.quota.render_quota_response", lambda data: "rendered")

    result = await runner._handle_quota_command(event)

    assert result == "rendered"
    assert captured["model"] == "gpt-5.5"
    assert captured["provider"] == "openai-codex"
    assert captured["api_key"] == "fresh-token"


@pytest.mark.asyncio
async def test_quota_command_uses_session_model_override(monkeypatch):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._session_model_overrides = {
        "agent:main:telegram:private:123": {
            "model": "deepseek-v4-flash",
            "provider": "openai-proxy",
            "api_key": "override-key",
        }
    }
    runner._normalize_source_for_session_key = MagicMock(side_effect=lambda source: source)
    runner._session_key_for_source = MagicMock(return_value="agent:main:telegram:private:123")

    event = MagicMock()
    event.get_command_args.return_value = ""
    event.source = MagicMock()

    monkeypatch.setattr(
        "gateway.run._load_gateway_config",
        lambda: {"model": {"default": "gpt-5.5", "provider": "openai-codex"}},
    )

    captured = {}

    async def fake_fetch_quota_async(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "model": kwargs["model"], "provider": kwargs["provider"]}

    monkeypatch.setattr("hermes_cli.quota.fetch_quota_async", fake_fetch_quota_async)
    monkeypatch.setattr("hermes_cli.quota.render_quota_response", lambda data: "rendered")

    result = await runner._handle_quota_command(event)

    assert result == "rendered"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["provider"] == "openai-proxy"
    assert captured["api_key"] == "override-key"
