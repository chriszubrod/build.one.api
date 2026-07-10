"""Azure AI Foundry transport — OpenAI-compatible chat-completions over HTTPX.

Serves the models Chris provisioned in Foundry (DeepSeek-V4-Flash,
gpt-5.4-nano, gpt-5.4-mini, …). Foundry exposes an OpenAI-compatible
`/chat/completions` surface for both the Azure-OpenAI GPT family and the
Azure-AI-inference DeepSeek family, so one adapter covers all of them — the
`model` field in the body selects the deployment.

Emits the same canonical TransportEvents as the Anthropic adapter, so the
agent loop is provider-agnostic above this layer. The single real difference
is the wire format: OpenAI SSE (`data: {chunk}` lines, no `event:` lines;
tool calls stream incrementally as `delta.tool_calls`).

VERIFY-AT-SMOKE (depends on the exact Foundry deployment — confirm when the
endpoint/key are set):
  * URL shape — this uses the unified inference endpoint
    `{endpoint}/chat/completions?api-version=...`. Per-deployment Azure-OpenAI
    URLs (`/openai/deployments/{name}/chat/completions`) would need a tweak.
  * Token cap field — GPT-5.4 models may require `max_completion_tokens`
    instead of `max_tokens`; flip `_TOKEN_PARAM` if the API rejects it.
  * Auth header — `api-key` (Azure). Some Foundry routes want
    `Authorization: Bearer`.
"""
import asyncio
import json
import logging
import random
from typing import Any, AsyncIterator, Optional

import httpx

import config
from intelligence.messages.convert import to_openai_request
from intelligence.messages.types import Message
from intelligence.transport.base import (
    Done,
    TextDelta,
    ToolUseComplete,
    ToolUseStart,
    TransportError,
    TransportEvent,
    TurnEnd,
    TurnStart,
    Usage,
)


logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 529})
_MAX_RETRIES = 2            # 3 attempts total
_BASE_DELAY_SECONDS = 1.0
_MAX_DELAY_SECONDS = 8.0
# Confirmed working api-version for the Foundry /models inference route
# (smoke 2026-06-30): DeepSeek-V4-Flash + gpt-5.4-mini/nano all answer on it.
_DEFAULT_API_VERSION = "2024-05-01-preview"


_REASONING_PREFIXES = ("gpt-", "o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    """GPT-5.4 / o-series are reasoning models. Confirmed at smoke they (a)
    require `max_completion_tokens` not `max_tokens`, and (b) reject
    `temperature`/`top_p` (the lever is `reasoning_effort`). DeepSeek-V4-Flash
    is non-reasoning: `max_tokens` + temperature, and it rejects
    `reasoning_effort`."""
    return model.lower().startswith(_REASONING_PREFIXES)


def _token_param(model: str) -> str:
    """Output-token-cap body field: `max_completion_tokens` for reasoning
    models, `max_tokens` otherwise. A defensive swap-retry in stream() covers
    any model this guesses wrong."""
    return "max_completion_tokens" if _is_reasoning_model(model) else "max_tokens"


def _filter_gen_params(model: str, extra_body: dict[str, Any]) -> dict[str, Any]:
    """Keep only the generation params this model's family accepts, so a caller
    can pass a generous superset without a provider 400."""
    eb = dict(extra_body)
    if _is_reasoning_model(model):
        eb.pop("temperature", None)
        eb.pop("top_p", None)
    else:
        eb.pop("reasoning_effort", None)
    return eb


def _retry_delay(attempt: int, retry_after: Optional[float] = None) -> float:
    exp = min(_BASE_DELAY_SECONDS * (2 ** attempt), _MAX_DELAY_SECONDS)
    jittered = exp * (0.75 + random.random() * 0.5)
    if retry_after is not None and retry_after > 0:
        return max(retry_after, jittered)
    return jittered


class FoundryTransport:
    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: float = 120.0,
    ):
        settings = config.Settings()
        self._api_key = api_key or getattr(settings, "foundry_api_key", None)
        self._endpoint = (endpoint or getattr(settings, "foundry_endpoint", None) or "").rstrip("/")
        self._api_version = (
            api_version
            or getattr(settings, "foundry_api_version", None)
            or _DEFAULT_API_VERSION
        )
        self._timeout = timeout

    def _models_base(self) -> str:
        """Resolve the resource-level `/models` inference base from whatever
        FOUNDRY_ENDPOINT is set to. The Azure AI Foundry *project* endpoint
        (`https://<res>.services.ai.azure.com/api/projects/<proj>`), the bare
        resource root, and an explicit `.../models` base all collapse to
        `https://<res>.services.ai.azure.com/models` — the OpenAI-compatible
        chat-completions surface that serves every deployment (model-in-body)."""
        ep = self._endpoint
        if "/api/projects/" in ep:
            ep = ep.split("/api/projects/")[0]
        ep = ep.rstrip("/")
        if ep.endswith("/models"):
            ep = ep[: -len("/models")]
        return f"{ep}/models"

    def _url(self) -> str:
        return f"{self._models_base()}/chat/completions?api-version={self._api_version}"

    async def stream(
        self,
        messages: list[Message],
        model: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        tools: Optional[list[dict[str, Any]]] = None,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[TransportEvent]:
        if not self._api_key:
            yield TransportError(message="FOUNDRY_API_KEY is not configured", code="missing_api_key")
            return
        if not self._endpoint:
            yield TransportError(message="FOUNDRY_ENDPOINT is not configured", code="missing_endpoint")
            return

        body = to_openai_request(
            messages, model=model, system=system, max_tokens=max_tokens, tools=tools,
        )
        # Per-model output-token cap field; stream with terminal usage accounting.
        token_param = _token_param(model)
        if token_param != "max_tokens":
            body[token_param] = body.pop("max_tokens")
        # Generation params, filtered to what this model's family accepts.
        if extra_body:
            body.update(_filter_gen_params(model, extra_body))
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}

        # Foundry /models route authenticates the resource key as a bearer token.
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        token_param_swapped = False

        async with httpx.AsyncClient(timeout=httpx.Timeout(
            connect=10.0, read=self._timeout, write=30.0, pool=10.0,
        )) as client:
            resp_ctx = None
            for attempt in range(_MAX_RETRIES + 1):
                resp_ctx = client.stream("POST", self._url(), headers=headers, json=body)
                resp = await resp_ctx.__aenter__()
                if resp.status_code == 200:
                    break
                err_body = await resp.aread()
                try:
                    ra_raw = resp.headers.get("retry-after")
                    retry_after = float(ra_raw) if ra_raw else None
                except (TypeError, ValueError):
                    retry_after = None
                status = resp.status_code
                await resp_ctx.__aexit__(None, None, None)
                resp_ctx = None
                err_text = err_body.decode(errors="replace")
                # Defensive: if _token_param() guessed the cap field wrong for
                # this deployment, the API returns a specific 400 — swap the
                # field once and retry (covers models the heuristic doesn't know).
                if status == 400 and not token_param_swapped and "max_completion_tokens" in err_text:
                    if "max_tokens" in body:
                        body["max_completion_tokens"] = body.pop("max_tokens")
                        token_param_swapped = True
                        continue
                    if "max_completion_tokens" in body and "max_tokens" in err_text:
                        body["max_tokens"] = body.pop("max_completion_tokens")
                        token_param_swapped = True
                        continue
                if status in _RETRYABLE_STATUSES and attempt < _MAX_RETRIES:
                    delay = _retry_delay(attempt, retry_after)
                    logger.info(
                        "foundry transport: retrying after HTTP %s (attempt %d/%d, sleeping %.1fs)",
                        status, attempt + 1, _MAX_RETRIES + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                yield TransportError(
                    message=f"HTTP {status}: {err_text[:500]}",
                    code=f"http_{status}",
                )
                return

            try:
                async for ev in _chunks_to_events(_parse_openai_sse(resp), model):
                    yield ev
            finally:
                if resp_ctx is not None:
                    await resp_ctx.__aexit__(None, None, None)


async def _chunks_to_events(
    chunks: AsyncIterator[dict[str, Any]],
    fallback_model: str,
) -> AsyncIterator[TransportEvent]:
    """Translate a stream of OpenAI chat-completion chunks into canonical
    TransportEvents. Pure (no I/O) so it is unit-testable with canned chunks.

    Handles incremental `delta.tool_calls`: ids/names/arguments arrive across
    multiple chunks and are accumulated per `index`, then flushed as
    ToolUseComplete when `finish_reason` lands.
    """
    usage = Usage()
    turn_started = False
    stop_reason: Optional[str] = None
    tool_calls: dict[int, dict[str, str]] = {}   # index -> {id, name, args}
    started_tool_indexes: set[int] = set()

    async for chunk in chunks:
        if not turn_started:
            yield TurnStart(model=chunk.get("model", fallback_model))
            turn_started = True

        u = chunk.get("usage")
        if isinstance(u, dict):
            usage = _usage_from_openai(u)

        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0] or {}
        delta = choice.get("delta") or {}

        text = delta.get("content")
        if text:
            yield TextDelta(text=text)

        for tc in (delta.get("tool_calls") or []):
            idx = tc.get("index", 0)
            slot = tool_calls.setdefault(idx, {"id": "", "name": "", "args": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]
            if idx not in started_tool_indexes and slot["id"] and slot["name"]:
                started_tool_indexes.add(idx)
                yield ToolUseStart(id=slot["id"], name=slot["name"])

        finish = choice.get("finish_reason")
        if finish:
            stop_reason = finish
            for idx in sorted(tool_calls):
                slot = tool_calls[idx]
                if not slot["id"] and not slot["name"]:
                    continue
                if idx not in started_tool_indexes:
                    yield ToolUseStart(id=slot["id"], name=slot["name"])
                    started_tool_indexes.add(idx)
                try:
                    parsed = json.loads(slot["args"]) if slot["args"] else {}
                except json.JSONDecodeError:
                    parsed = {}
                yield ToolUseComplete(id=slot["id"], name=slot["name"], input=parsed)
            yield TurnEnd(stop_reason=stop_reason)

    yield Done(usage=usage)


def _usage_from_openai(u: dict[str, Any]) -> Usage:
    details = u.get("prompt_tokens_details") or {}
    return Usage(
        input_tokens=u.get("prompt_tokens", 0) or 0,
        output_tokens=u.get("completion_tokens", 0) or 0,
        cache_read_input_tokens=details.get("cached_tokens", 0) or 0,
    )


async def _parse_openai_sse(resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Yield chunk dicts from an OpenAI-style SSE stream.

    OpenAI emits `data: {json}` per line and a final `data: [DONE]`; there are
    no `event:` lines. Blank lines and comments are ignored.
    """
    async for line in resp.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue
