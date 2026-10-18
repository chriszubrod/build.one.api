"""Anthropic Messages API transport — direct HTTPX, no vendor SDK.

Uses the `/v1/messages` endpoint with `stream: true` and parses the SSE
response into canonical TransportEvents.

SSE events consumed:
  message_start                              → TurnStart, seed Usage
  content_block_start   (text)               → (no event; deltas arrive next)
  content_block_start   (tool_use)           → ToolUseStart, begin accumulating input JSON
  content_block_start   (thinking)           → (no event; buffer opens, seeded from the start)
  content_block_start   (redacted_thinking)  → (no event; opaque data held whole)
  content_block_delta   (text_delta)         → TextDelta
  content_block_delta   (input_json_delta)   → append to active tool_use's JSON buffer
  content_block_delta   (thinking_delta)     → append to active thinking buffer
  content_block_delta   (signature_delta)    → append to active thinking signature
  content_block_stop    (tool_use)           → parse JSON, emit ToolUseComplete
  content_block_stop    (thinking/redacted)  → emit ThinkingComplete with the whole block
  message_delta                              → update stop_reason + Usage.output_tokens
  message_stop                               → TurnEnd, then Done
  error                                      → TransportError

Thinking blocks are emitted whole (byte-identity rule: messages.types.Thinking).
ping and other events are ignored.
"""
import asyncio
import json
from dataclasses import dataclass, field
import logging
import random
from typing import Any, AsyncIterator, Optional, Tuple, Union

import httpx

import config
from intelligence.cascade import claude_budget
from intelligence.messages.convert import to_anthropic_request
from intelligence.messages.types import Message, RedactedThinking, Thinking
from intelligence.transport.foundry import foundry_resource_root
from intelligence.transport.base import (
    Done,
    TextDelta,
    ThinkingComplete,
    ToolUseComplete,
    ToolUseStart,
    TransportError,
    TransportEvent,
    TurnEnd,
    TurnStart,
    Usage,
)


logger = logging.getLogger(__name__)

ANTHROPIC_DIRECT_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Transient upstream conditions worth retrying. Anthropic returns 529
# during capacity events; 429 for rate limits; 503 during deploys.
_RETRYABLE_STATUSES = frozenset({429, 503, 529})
_MAX_RETRIES = 2            # 3 attempts total
_BASE_DELAY_SECONDS = 1.0   # exponential: 1s, 2s, 4s…
_MAX_DELAY_SECONDS = 8.0


def _retry_delay(attempt: int, retry_after: Optional[float] = None) -> float:
    """Exponential backoff with jitter, honoring server's Retry-After."""
    exp = min(_BASE_DELAY_SECONDS * (2 ** attempt), _MAX_DELAY_SECONDS)
    jittered = exp * (0.75 + random.random() * 0.5)  # ±25%
    if retry_after is not None and retry_after > 0:
        return max(retry_after, jittered)
    return jittered


_EFFORT_ONLY_PREFIXES = (
    "claude-haiku-5",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-fable",
    "claude-mythos",
)

_REASONING_EFFORT_TO_OUTPUT_EFFORT = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}


def _effort_only_model(model: str) -> bool:
    """Models that reject temperature/top_p/top_k (HTTP 400) and take effort
    via `output_config` instead."""
    return model.lower().startswith(_EFFORT_ONLY_PREFIXES)


# Off-switch per family (longest prefix wins). Sonnet 5.5 rejects `disabled` and
# takes `between_tools` (no extended thinking; between-tool notes still stream
# as thinking blocks). Opus 5.5, Fable and legacy models have no off-switch
# here, so the cross-provider `thinking: "off"` hint is dropped for them.
_THINKING_OFF_BY_FAMILY: tuple[tuple[str, str], ...] = (
    ("claude-haiku-5", "disabled"),
    ("claude-sonnet-5-5", "between_tools"),
)


def _thinking_off_type(model: str) -> Optional[str]:
    """The `thinking.type` that turns thinking off for this model family, or
    None when the family has no off-switch. Longest matching prefix wins, so
    the table's order never matters (a "claude-sonnet-5" entry could not
    shadow "claude-sonnet-5-5")."""
    lowered = model.lower()
    match = max((p for p, _ in _THINKING_OFF_BY_FAMILY if lowered.startswith(p)), key=len, default=None)
    return dict(_THINKING_OFF_BY_FAMILY)[match] if match is not None else None


def _gen_params(model: str, extra_body: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The generation params to add to a request body for `model`.

    Only the params the model family accepts are forwarded; the rest are
    silently dropped so a caller can pass a cross-provider superset. Effort-only
    models get `reasoning_effort` translated to `output_config.effort`.
    """
    if not extra_body:
        return {}
    effort_only = _effort_only_model(model)
    keys = ("stop_sequences",) if effort_only else ("temperature", "top_p", "top_k", "stop_sequences")
    params: dict[str, Any] = {k: extra_body[k] for k in keys if k in extra_body}
    if effort_only:
        effort = _REASONING_EFFORT_TO_OUTPUT_EFFORT.get(extra_body.get("reasoning_effort"))
        if effort is not None:
            params["output_config"] = {"effort": effort}
    return params


def _build_request_body(
    messages: list[Message],
    model: str,
    system: Optional[str],
    max_tokens: int,
    tools: Optional[list[dict[str, Any]]],
    extra_body: Optional[dict[str, Any]],
) -> dict[str, Any]:
    body = to_anthropic_request(
        messages,
        model=model,
        system=system,
        max_tokens=max_tokens,
        tools=tools,
    )
    body.update(_gen_params(model, extra_body))
    body["stream"] = True
    if (extra_body or {}).get("thinking") == "off" and (off_type := _thinking_off_type(model)) is not None:
        body["thinking"] = {"type": off_type}
        # Each off-switch rejects xhigh/max effort with HTTP 400.
        if body.get("output_config", {}).get("effort") in ("xhigh", "max"):
            body["output_config"] = {"effort": "high"}
    return body


def _normalize_anthropic_foundry_base(base: str) -> str:
    """Canonical Foundry Anthropic base: `<resource root>/anthropic`.

    Same root derivation as the Foundry chat transport, so any form
    FOUNDRY_ENDPOINT accepts (project endpoint, `/models`, `/anthropic`,
    `/anthropic/v1`) lands on the same Messages URL."""
    return f"{foundry_resource_root(base)}/anthropic"


def _anthropic_foundry_model_set(models_csv: str) -> frozenset[str]:
    return frozenset(m.strip() for m in models_csv.split(",") if m.strip())


def _resolve_messages_route(
    model: str,
    *,
    direct_api_key: Optional[str],
) -> tuple[str, Optional[str], Optional[str]]:
    """Return (messages_url, api_key, missing_setting_name).

    ``missing_setting_name`` is an env-var-style label when the chosen route
    has no key; no request should be sent in that case."""
    settings = config.Settings()
    base = (settings.anthropic_foundry_base_url or "").strip()
    if base and model in _anthropic_foundry_model_set(settings.anthropic_foundry_models):
        url = f"{_normalize_anthropic_foundry_base(base)}/v1/messages"
        key = settings.anthropic_foundry_api_key or settings.foundry_api_key
        if not key:
            return url, None, "ANTHROPIC_FOUNDRY_API_KEY"
        return url, key, None
    if not direct_api_key:
        return ANTHROPIC_DIRECT_URL, None, "ANTHROPIC_API_KEY"
    return ANTHROPIC_DIRECT_URL, direct_api_key, None


class AnthropicTransport:
    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self._api_key = api_key or config.Settings().anthropic_api_key
        self._timeout = timeout

    async def stream(
        self,
        messages: list[Message],
        model: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        tools: Optional[list[dict[str, Any]]] = None,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[TransportEvent]:
        body = _build_request_body(messages, model, system, max_tokens, tools, extra_body)

        post_url, api_key, missing_setting = _resolve_messages_route(
            model,
            direct_api_key=self._api_key,
        )
        if missing_setting:
            code = (
                "claude_unavailable:no_key"
                if missing_setting == "ANTHROPIC_API_KEY"
                else "missing_api_key"
            )
            if code == "claude_unavailable:no_key":
                claude_budget.warn_no_api_key_once()
            yield TransportError(
                message=f"{missing_setting} is not configured",
                code=code,
            )
            return

        skip = await claude_budget.pre_call_gate(has_api_key=True)
        if skip:
            yield TransportError(
                message=f"claude budget gate: {skip}",
                code=f"claude_unavailable:{skip}",
            )
            return

        headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        stream_error: Optional[str] = None
        live_usage: list[Usage] = [Usage()]
        http_attempted = False
        metered = False

        async def _meter(error: Optional[str], final_usage: Optional[Usage] = None) -> None:
            # Meter exactly once per request, BEFORE the terminal event reaches the
            # consumer: a consumer that stops reading on Done / TransportError must
            # already see the breaker open and the spend recorded (U-676f r3).
            nonlocal metered
            if metered or not http_attempted:
                return
            metered = True
            meter_usage = (
                final_usage
                if final_usage is not None and _has_tokens(final_usage)
                else live_usage[0]
            )
            try:
                await claude_budget.post_call_metering(
                    usage=meter_usage, model=model, error=error,
                )
            except Exception as exc:
                # Metering must never change what the transport returns: a
                # finished Claude answer stays an answer (no Foundry fallback).
                logger.warning(
                    "anthropic transport: metering failed: %s", type(exc).__name__,
                )

        async with httpx.AsyncClient(timeout=httpx.Timeout(
            connect=10.0, read=self._timeout, write=30.0, pool=10.0,
        )) as client:
            try:
                resp_ctx = None
                for attempt in range(_MAX_RETRIES + 1):
                    resp_ctx = client.stream(
                        "POST", post_url, headers=headers, json=body,
                    )
                    resp = await resp_ctx.__aenter__()
                    http_attempted = True
                    if resp.status_code == 200:
                        try:
                            async for ev in _sse_to_events(
                                _parse_sse(resp), model, live_usage,
                            ):
                                if isinstance(ev, Done):
                                    await _meter(None, ev.usage)
                                elif isinstance(ev, TransportError):
                                    stream_error = (
                                        f"{ev.code}: {ev.message}"
                                        if ev.code
                                        else ev.message
                                    )
                                    await _meter(stream_error)
                                yield ev
                        finally:
                            if resp_ctx is not None:
                                await resp_ctx.__aexit__(None, None, None)
                            resp_ctx = None
                        break

                    err_body = await resp.aread()
                    try:
                        retry_after_raw = resp.headers.get("retry-after")
                        retry_after = (
                            float(retry_after_raw) if retry_after_raw else None
                        )
                    except (TypeError, ValueError):
                        retry_after = None
                    status = resp.status_code
                    await resp_ctx.__aexit__(None, None, None)
                    resp_ctx = None

                    if (
                        status in _RETRYABLE_STATUSES
                        and attempt < _MAX_RETRIES
                    ):
                        delay = _retry_delay(attempt, retry_after)
                        logger.info(
                            "anthropic transport: retrying after HTTP %s "
                            "(attempt %d/%d, sleeping %.1fs)",
                            status, attempt + 1, _MAX_RETRIES + 1, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    code = f"http_{status}"
                    err_text = err_body.decode(errors="replace")[:500]
                    stream_error = f"{code}: {err_text}"
                    await _meter(stream_error)
                    yield TransportError(message=err_text, code=code)
                    return
            except Exception as exc:
                stream_error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                # Paths that never reached a terminal event (truncated stream,
                # exception, consumer aclose mid-stream) meter what was seen.
                await _meter(stream_error)


def _has_tokens(usage: Usage) -> bool:
    return bool(
        usage.input_tokens
        or usage.output_tokens
        or usage.cache_creation_input_tokens
        or usage.cache_read_input_tokens
    )


async def _parse_sse(resp: httpx.Response) -> AsyncIterator[Tuple[str, dict]]:
    """Yield (event_name, data) pairs from an SSE stream.

    Anthropic emits `event: <name>` followed by `data: <json>` and a blank
    line between records. We dispatch on blank line.
    """
    event_name: Optional[str] = None
    data_parts: list[str] = []
    async for line in resp.aiter_lines():
        if line == "":
            if event_name and data_parts:
                raw = "".join(data_parts)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {}
                yield event_name, data
            event_name = None
            data_parts = []
        elif line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_parts.append(line[len("data:"):].lstrip())
        # comments (":") and other fields are ignored


@dataclass
class _ToolBuf:
    id: str
    name: str
    json_parts: list[str] = field(default_factory=list)


@dataclass
class _ThinkingBuf:
    thinking: list[str]
    signature: list[str]


_OpenBlock = Union[_ToolBuf, _ThinkingBuf, RedactedThinking]


async def _sse_to_events(
    events: AsyncIterator[Tuple[str, dict]],
    model: str,
    live_usage: Optional[list[Usage]] = None,
) -> AsyncIterator[TransportEvent]:
    """Pure translation of parsed (event, data) pairs into TransportEvents.

    The mapping is the table in the module docstring. Each open block is kept
    under its wire index until its content_block_stop; buffered strings are
    joined exactly once there, so nothing is stripped or re-encoded.
    """
    usage = Usage()
    stop_reason: Optional[str] = None
    open_blocks: dict[int, _OpenBlock] = {}
    async for event_name, data in events:
        if event_name == "message_start":
            msg = data.get("message", {}) or {}
            u = msg.get("usage", {}) or {}
            usage = Usage(
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
                cache_creation_input_tokens=u.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=u.get("cache_read_input_tokens", 0),
            )
            # Before the yield: a consumer that stops right after TurnStart
            # must still have the billed input tokens metered.
            if live_usage is not None:
                live_usage[0] = usage
            yield TurnStart(model=msg.get("model", model))
        elif event_name == "content_block_start":
            idx = data.get("index", 0)
            block = data.get("content_block", {}) or {}
            btype = block.get("type")
            if btype == "tool_use":
                open_blocks[idx] = _ToolBuf(id=block.get("id", ""), name=block.get("name", ""))
                yield ToolUseStart(id=block.get("id", ""), name=block.get("name", ""))
            elif btype == "thinking":
                open_blocks[idx] = _ThinkingBuf(
                    thinking=[block.get("thinking") or ""],
                    signature=[block.get("signature") or ""],
                )
            elif btype == "redacted_thinking":
                open_blocks[idx] = RedactedThinking(data=block.get("data") or "")
            # text blocks emit via content_block_delta; no start event needed
        elif event_name == "content_block_delta":
            delta = data.get("delta", {}) or {}
            dtype = delta.get("type")
            blk = open_blocks.get(data.get("index", 0))
            if dtype == "text_delta":
                yield TextDelta(text=delta.get("text", ""))
            elif dtype == "input_json_delta" and isinstance(blk, _ToolBuf):
                blk.json_parts.append(delta.get("partial_json", ""))
            elif dtype == "thinking_delta" and isinstance(blk, _ThinkingBuf):
                blk.thinking.append(delta.get("thinking", ""))
            elif dtype == "signature_delta" and isinstance(blk, _ThinkingBuf):
                blk.signature.append(delta.get("signature", ""))
        elif event_name == "content_block_stop":
            blk = open_blocks.pop(data.get("index", 0), None)
            if isinstance(blk, _ToolBuf):
                raw = "".join(blk.json_parts)
                try:
                    tool_input = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    tool_input = {}
                yield ToolUseComplete(id=blk.id, name=blk.name, input=tool_input)
            elif isinstance(blk, _ThinkingBuf):
                yield ThinkingComplete(block=Thinking(
                    thinking="".join(blk.thinking), signature="".join(blk.signature),
                ))
            elif isinstance(blk, RedactedThinking):
                yield ThinkingComplete(block=blk)
        elif event_name == "message_delta":
            delta = data.get("delta", {}) or {}
            if "stop_reason" in delta:
                stop_reason = delta["stop_reason"]
            u = data.get("usage", {}) or {}
            if "output_tokens" in u:
                usage = usage.model_copy(update={"output_tokens": u["output_tokens"]})
                if live_usage is not None:
                    live_usage[0] = usage
        elif event_name == "message_stop":
            yield TurnEnd(stop_reason=stop_reason)
            yield Done(usage=usage)
        elif event_name == "error":
            err = data.get("error", {}) or {}
            yield TransportError(
                message=err.get("message", "unknown error"),
                code=err.get("type"),
            )
