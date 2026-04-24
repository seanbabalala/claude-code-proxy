from __future__ import annotations

import json

import httpx
import pytest

from claude_code_proxy.app import collect_claude_code_startup_diagnostics, create_app
from claude_code_proxy.config import Settings


def _settings() -> Settings:
    return Settings(
        upstream_base_url="https://example.com/v1/chat/completions",
        upstream_api_key="upstream-token",
        gateway_api_key="local-dev-token",
        default_upstream_model="claude-opus-4-6-v1",
        default_max_tokens=32000,
        force_upstream_model=False,
        model_map={"claude-sonnet-4-5": "claude-opus-4-6-v1"},
        upstream_timeout_seconds=30,
    )


@pytest.mark.asyncio
async def test_messages_non_stream_translates_to_anthropic_response() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_1",
                "choices": [
                    {
                        "message": {"content": "Hello from upstream"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    app = create_app(settings=_settings(), upstream_transport=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/v1/messages",
            headers={"x-api-key": "local-dev-token"},
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "message"
    assert data["content"][0]["text"] == "Hello from upstream"
    assert "Bearer upstream-token" in captured["headers"]["authorization"]
    assert '"model":"claude-opus-4-6-v1"' in captured["body"]
    assert '"max_completion_tokens":256' in captured["body"]


@pytest.mark.asyncio
async def test_count_tokens_endpoint() -> None:
    app = create_app(settings=_settings(), upstream_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/v1/messages/count_tokens",
            headers={"x-api-key": "local-dev-token"},
            json={
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    assert response.json()["input_tokens"] > 0


@pytest.mark.asyncio
async def test_messages_use_default_max_tokens_when_not_provided() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_2",
                "choices": [
                    {
                        "message": {"content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    app = create_app(settings=_settings(), upstream_transport=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/v1/messages",
            headers={"x-api-key": "local-dev-token"},
            json={
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    assert '"max_completion_tokens":32000' in captured["body"]


@pytest.mark.asyncio
async def test_force_upstream_model_pins_sonnet_request_to_opus_line() -> None:
    captured: dict[str, object] = {}
    settings = _settings()
    settings.force_upstream_model = True

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_3",
                "choices": [
                    {
                        "message": {"content": "Hello from forced opus"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    app = create_app(settings=settings, upstream_transport=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/v1/messages",
            headers={"x-api-key": "local-dev-token"},
            json={
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    assert response.json()["model"] == "claude-opus-4-6-v1"
    assert '"model":"claude-opus-4-6-v1"' in captured["body"]


@pytest.mark.asyncio
async def test_streaming_response_translates_openai_chunks_to_anthropic_events() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        content = (
            'data: {"choices":[{"delta":{"content":"Hel"},"finish_reason":null}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":null}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=content,
        )

    app = create_app(settings=_settings(), upstream_transport=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=30) as client:
        response = await client.post(
            "/v1/messages",
            headers={"x-api-key": "local-dev-token"},
            json={
                "model": "claude-sonnet-4-5",
                "stream": True,
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 200
    body = response.text
    assert "event: message_start" in body
    assert "event: content_block_start" in body
    assert "text_delta" in body
    assert "message_stop" in body


def test_collect_startup_diagnostics_warns_when_user_settings_bypass_proxy(tmp_path) -> None:
    settings = _settings()
    settings.force_upstream_model = True
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_BASE_URL": "https://other-gateway.example.com/claudecode",
                    "ANTHROPIC_AUTH_TOKEN": "wrong-token",
                },
                "model": "sonnet",
            }
        ),
        encoding="utf-8",
    )

    diagnostics = collect_claude_code_startup_diagnostics(settings, settings_path)
    messages = [item.message for item in diagnostics]

    assert any("bypass this proxy" in message for message in messages)
    assert any("401" in message for message in messages)
    assert any("label the session as Sonnet" in message for message in messages)


def test_collect_startup_diagnostics_stays_quiet_when_user_settings_match_proxy(tmp_path) -> None:
    settings = _settings()
    settings.force_upstream_model = True
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8000",
                    "ANTHROPIC_AUTH_TOKEN": "local-dev-token",
                },
                "model": "opus",
            }
        ),
        encoding="utf-8",
    )

    diagnostics = collect_claude_code_startup_diagnostics(settings, settings_path)

    assert diagnostics == []
