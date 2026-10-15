"""U-657 — per-model Anthropic transport routing via Azure Foundry Messages API."""

import asyncio
from typing import Any, Optional

import httpx
import pytest

from intelligence.messages.types import Message, Text
from intelligence.transport.anthropic import (
    ANTHROPIC_DIRECT_URL,
    AnthropicTransport,
    _normalize_anthropic_foundry_base,
    _resolve_messages_route,
)
from intelligence.transport.base import TransportError
from tests.loop_test_helpers import HAIKU, HINT, LOOKUP_TOOL, SAMPLING, SONNET_55

FOUNDRY_RESOURCE = "https://buildonefoundry.services.ai.azure.com"
FOUNDRY_MESSAGES_URL = f"{FOUNDRY_RESOURCE}/anthropic/v1/messages"
DIRECT_KEY = "direct-anthropic-key"
FOUNDRY_KEY = "azure-foundry-key"


def _minimal_sse_lines() -> list[str]:
    return [
        "event: message_start",
        (
            'data: {"type":"message_start","message":{"model":"claude-haiku-5-5",'
            '"usage":{"input_tokens":1,"output_tokens":0}}}'
        ),
        "",
        "event: message_stop",
        'data: {"type":"message_stop"}',
        "",
    ]


class _FakeStreamResponse:
    status_code = 200

    def __init__(self, lines: list[str]):
        self._lines = lines

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamContext:
    def __init__(self, capture: dict[str, Any], lines: list[str]):
        self._capture = capture
        self._response = _FakeStreamResponse(lines)

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeAsyncClient:
    instances: list["_FakeAsyncClient"] = []

    def __init__(self, **kwargs: Any):
        self.capture: dict[str, Any] = {}
        _FakeAsyncClient.instances.append(self)

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> _FakeStreamContext:
        self.capture = {
            "method": method,
            "url": url,
            "headers": dict(headers or {}),
            "json": json,
        }
        return _FakeStreamContext(self.capture, _minimal_sse_lines())


@pytest.fixture
def mock_httpx(monkeypatch: pytest.MonkeyPatch):
    _FakeAsyncClient.instances.clear()

    def _factory(**kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return _FakeAsyncClient


@pytest.fixture(autouse=True)
def clear_foundry_env(monkeypatch: pytest.MonkeyPatch):
    # setenv(""), not delenv: Settings() also reads .env, and a value there
    # would otherwise leak into every test once the setting is configured.
    for name in ("ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_FOUNDRY_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_MODELS", "claude-haiku-5-5")


async def _collect_stream(transport: AnthropicTransport, model: str, **kwargs: Any):
    events = []
    async for ev in transport.stream(
        [Message(role="user", content=[Text(text="hi")])],
        model=model,
        **kwargs,
    ):
        events.append(ev)
    return events


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("model", [HAIKU, SONNET_55])
def test_unset_foundry_base_uses_direct_url_and_key(mock_httpx, clear_foundry_env, model):
    transport = AnthropicTransport(api_key=DIRECT_KEY)
    events = _run(_collect_stream(transport, model))
    assert not any(isinstance(e, TransportError) for e in events)
    cap = mock_httpx.instances[0].capture
    assert cap["url"] == ANTHROPIC_DIRECT_URL
    assert cap["headers"]["x-api-key"] == DIRECT_KEY


def test_configured_foundry_routes_haiku_only(mock_httpx, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", FOUNDRY_RESOURCE)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", FOUNDRY_KEY)

    transport = AnthropicTransport(api_key=DIRECT_KEY)
    haiku_events = _run(_collect_stream(transport, HAIKU))
    assert not any(isinstance(e, TransportError) for e in haiku_events)
    haiku_cap = mock_httpx.instances[0].capture
    assert haiku_cap["url"] == FOUNDRY_MESSAGES_URL
    assert haiku_cap["headers"]["x-api-key"] == FOUNDRY_KEY

    sonnet_events = _run(_collect_stream(transport, SONNET_55))
    assert not any(isinstance(e, TransportError) for e in sonnet_events)
    sonnet_cap = mock_httpx.instances[1].capture
    assert sonnet_cap["url"] == ANTHROPIC_DIRECT_URL
    assert sonnet_cap["headers"]["x-api-key"] == DIRECT_KEY


def test_foundry_key_falls_back_to_foundry_api_key(mock_httpx, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", FOUNDRY_RESOURCE)
    monkeypatch.setenv("FOUNDRY_API_KEY", FOUNDRY_KEY)

    transport = AnthropicTransport(api_key=DIRECT_KEY)
    _run(_collect_stream(transport, HAIKU))
    assert mock_httpx.instances[0].capture["headers"]["x-api-key"] == FOUNDRY_KEY


@pytest.mark.parametrize(
    "raw, expected",
    [
        (FOUNDRY_RESOURCE, f"{FOUNDRY_RESOURCE}/anthropic"),
        (f"{FOUNDRY_RESOURCE}/", f"{FOUNDRY_RESOURCE}/anthropic"),
        (f"{FOUNDRY_RESOURCE}/anthropic", f"{FOUNDRY_RESOURCE}/anthropic"),
        (f"{FOUNDRY_RESOURCE}/anthropic/", f"{FOUNDRY_RESOURCE}/anthropic"),
        (f"{FOUNDRY_RESOURCE}/anthropic/v1", f"{FOUNDRY_RESOURCE}/anthropic"),
        (f"{FOUNDRY_RESOURCE}/models", f"{FOUNDRY_RESOURCE}/anthropic"),
        (f"{FOUNDRY_RESOURCE}/api/projects/buildoneai", f"{FOUNDRY_RESOURCE}/anthropic"),
        (f"  {FOUNDRY_RESOURCE}  ", f"{FOUNDRY_RESOURCE}/anthropic"),
    ],
)
def test_normalize_foundry_base_url(raw, expected):
    assert _normalize_anthropic_foundry_base(raw) == expected


def test_normalize_produces_messages_url(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", f"{FOUNDRY_RESOURCE}/anthropic/")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", FOUNDRY_KEY)
    url, key, missing = _resolve_messages_route(HAIKU, direct_api_key=DIRECT_KEY)
    assert url == FOUNDRY_MESSAGES_URL
    assert key == FOUNDRY_KEY
    assert missing is None


def test_missing_foundry_key_yields_error_without_http(mock_httpx, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", FOUNDRY_RESOURCE)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "")
    monkeypatch.setenv("FOUNDRY_API_KEY", "")

    transport = AnthropicTransport(api_key=DIRECT_KEY)
    events = _run(_collect_stream(transport, HAIKU))
    assert len(events) == 1
    err = events[0]
    assert isinstance(err, TransportError)
    assert err.code == "missing_api_key"
    assert err.message == "ANTHROPIC_FOUNDRY_API_KEY is not configured"
    assert mock_httpx.instances == []


def test_request_body_identical_between_routes(mock_httpx, monkeypatch):
    transport = AnthropicTransport(api_key=DIRECT_KEY)
    extra = {**SAMPLING, **HINT, "stop_sequences": ["END"]}
    kwargs = dict(
        system="sys",
        max_tokens=1024,
        tools=[LOOKUP_TOOL],
        extra_body=extra,
    )
    _run(_collect_stream(transport, HAIKU, **kwargs))
    direct_body = mock_httpx.instances[0].capture["json"]

    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", FOUNDRY_RESOURCE)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", FOUNDRY_KEY)
    _run(_collect_stream(transport, HAIKU, **kwargs))
    foundry_body = mock_httpx.instances[1].capture["json"]

    assert foundry_body == direct_body


def test_whitespace_only_base_url_is_treated_as_unset(mock_httpx, monkeypatch):
    """A blank-ish setting must not produce a relative URL; it means 'not configured'."""
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", "   ")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", FOUNDRY_KEY)
    _run(_collect_stream(AnthropicTransport(api_key=DIRECT_KEY), HAIKU))
    cap = mock_httpx.instances[0].capture
    assert cap["url"] == ANTHROPIC_DIRECT_URL
    assert cap["headers"]["x-api-key"] == DIRECT_KEY


def test_foundry_resource_root_is_shared_with_the_chat_transport():
    from intelligence.transport.foundry import FoundryTransport

    t = FoundryTransport.__new__(FoundryTransport)
    t._endpoint = f"{FOUNDRY_RESOURCE}/api/projects/buildoneai"
    assert t._models_base() == f"{FOUNDRY_RESOURCE}/models"
