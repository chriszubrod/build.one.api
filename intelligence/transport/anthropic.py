"""Anthropic Messages API transport — direct HTTPX, no vendor SDK.

Uses the `/v1/messages` endpoint with `stream: true` and parses the SSE
response into canonical TransportEvents.

SSE events consumed:
  message_start                              → TurnStart, seed Usage
  content_block_start   (text)               → (no event; deltas arrive next)
  content_block_start   (tool_use)           → ToolUseStart, begin accumulating input JSON
  content_block_delta   (text_delta)         → TextDelta
  content_block_delta   (input_json_delta)   → append to active tool_use's JSON buffer
  content_block_stop    (tool_use)           → parse JSON, emit ToolUseComplete
  message_delta                              → update stop_reason + Usage.output_tokens
  message_stop                               → TurnEnd, then Done
  error                                      → TransportError

ping and other events are ignored.
"""
import asyncio
import json
import logging
import random
from typing import Any, AsyncIterator, Optional, Tuple

import httpx

import config
from intelligence.messages.convert import to_anthropic_request
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

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
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


class AnthropicTransport:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 120.0):
        self._api_key = api_key or config.Settings().anthropic_api_key
        self._timeout = timeout

    async def stream(
        self,
        messages: list[Message],
        model: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> AsyncIterator[TransportEvent]:
        if not self._api_key:
            yield TransportError(
                message="ANTHROPIC_API_KEY is not configured",
                code="missing_api_key",
            )
            return

        body = to_anthropic_request(
            messages,
            model=model,
            system=system,
            max_tokens=max_tokens,
            tools=tools,
        )
        body["stream"] = True

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        usage = Usage()
        stop_reason: Optional[str] = None
        # index -> {"id": str, "name": str, "json_buf": str} for in-flight tool_use blocks
        active_tool_blocks: dict[int, dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp_ctx = None
            for attempt in range(_MAX_RETRIES + 1):
                resp_ctx = client.stream(
                    "POST", ANTHROPIC_URL, headers=headers, json=body
                )
                resp = await resp_ctx.__aenter__()
                if resp.status_code == 200:
                    break
                # Non-200 — decide: retry or surface.
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

                yield TransportError(
                    message=f"HTTP {status}: {err_body.decode(errors='replace')[:500]}",
                    code=f"http_{status}",
                )
                return

            # resp_ctx is the successful (200) context; process + close.
            try:
                async for event_name, data in _parse_sse(resp):
                    if event_name == "message_start":
                        msg = data.get("message", {}) or {}
                        yield TurnStart(model=msg.get("model", model))
                        u = msg.get("usage", {}) or {}
                        usage = Usage(
                            input_tokens=u.get("input_tokens", 0),
                            output_tokens=u.get("output_tokens", 0),
                            cache_creation_input_tokens=u.get(
                                "cache_creation_input_tokens", 0
                            ),
                            cache_read_input_tokens=u.get(
                                "cache_read_input_tokens", 0
                            ),
                        )
                    elif event_name == "content_block_start":
                        idx = data.get("index", 0)
                        block = data.get("content_block", {}) or {}
                        if block.get("type") == "tool_use":
                            active_tool_blocks[idx] = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "json_buf": "",
                            }
                            yield ToolUseStart(
                                id=block.get("id", ""),
                                name=block.get("name", ""),
                            )
                        # text blocks emit via content_block_delta; no start event needed
                    elif event_name == "content_block_delta":
                        idx = data.get("index", 0)
                        delta = data.get("delta", {}) or {}
                        dtype = delta.get("type")
                        if dtype == "text_delta":
                            yield TextDelta(text=delta.get("text", ""))
                        elif dtype == "input_json_delta":
                            blk = active_tool_blocks.get(idx)
                            if blk is not None:
                                blk["json_buf"] += delta.get("partial_json", "")
                    elif event_name == "content_block_stop":
                        idx = data.get("index", 0)
                        blk = active_tool_blocks.pop(idx, None)
                        if blk is not None:
                            raw = blk["json_buf"]
                            try:
                                tool_input = json.loads(raw) if raw else {}
                            except json.JSONDecodeError:
                                tool_input = {}
                            yield ToolUseComplete(
                                id=blk["id"],
                                name=blk["name"],
                                input=tool_input,
                            )
                    elif event_name == "message_delta":
                        delta = data.get("delta", {}) or {}
                        if "stop_reason" in delta:
                            stop_reason = delta["stop_reason"]
                        u = data.get("usage", {}) or {}
                        if "output_tokens" in u:
                            usage = Usage(
                                input_tokens=usage.input_tokens,
                                output_tokens=u["output_tokens"],
                                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                                cache_read_input_tokens=usage.cache_read_input_tokens,
                            )
                    elif event_name == "message_stop":
                        yield TurnEnd(stop_reason=stop_reason)
                        yield Done(usage=usage)
                    elif event_name == "error":
                        err = data.get("error", {}) or {}
                        yield TransportError(
                            message=err.get("message", "unknown error"),
                            code=err.get("type"),
                        )
            finally:
                if resp_ctx is not None:
                    await resp_ctx.__aexit__(None, None, None)


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
