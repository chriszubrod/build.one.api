"""Canonical-to-provider message conversion.

Each provider has one `to_<provider>_request` function. Keeping them side by
side makes it obvious when adding a variant (e.g. a new content block type)
requires updating every provider.
"""
import json
from typing import Any, Optional

from intelligence.messages.types import (
    Base64Source,
    ContentBlock,
    Document,
    Image,
    Message,
    OutputBlock,
    Text,
    ToolResult,
    ToolUse,
    UrlSource,
)


def to_anthropic_request(
    messages: list[Message],
    *,
    model: str,
    system: Optional[str] = None,
    max_tokens: int = 4096,
    tools: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Convert canonical messages + params to an Anthropic /v1/messages body.

    Anthropic separates the system prompt from the message list. If any
    role=="system" messages appear, their text is folded into the `system`
    field; an explicit `system` kwarg takes precedence and is prepended.

    Tool results in the canonical format live in user messages with
    ToolResult content blocks, matching Anthropic's wire format.
    """
    api_messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    if system:
        system_parts.append(system)

    for msg in messages:
        if msg.role == "system":
            for block in msg.content:
                if isinstance(block, Text):
                    system_parts.append(block.text)
            continue
        if msg.role not in ("user", "assistant"):
            continue  # OpenAI-style "tool" role is not used on the Anthropic wire
        api_messages.append({
            "role": msg.role,
            "content": [_block_to_anthropic(b) for b in msg.content],
        })

    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": api_messages,
    }
    # System prompt and tool schemas are the stable prefix of every request
    # in a conversation, so we mark them as cacheable via `cache_control`
    # (ephemeral, 5-min TTL). First turn writes the cache (small premium);
    # every subsequent turn hits it (~90% discount on those tokens).
    # Provider-neutral at the canonical layer — other transports (OpenAI
    # automatic, Gemini via its own API) implement their own strategies.
    if system_parts:
        body["system"] = [
            {
                "type": "text",
                "text": "\n\n".join(system_parts),
                "cache_control": {"type": "ephemeral"},
            }
        ]
    if tools:
        # cache_control on the LAST tool caches the entire tools block.
        cached_tools: list[dict[str, Any]] = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
        body["tools"] = cached_tools
    return body


def _block_to_anthropic(block: ContentBlock) -> dict[str, Any]:
    if isinstance(block, Text):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUse):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ToolResult):
        # content may be a string OR a list of OutputBlocks; both are valid
        # Anthropic tool_result shapes.
        if isinstance(block.content, str):
            content: Any = block.content
        else:
            content = [_output_block_to_anthropic(b) for b in block.content]
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": content,
            "is_error": block.is_error,
        }
    if isinstance(block, Image):
        return {"type": "image", "source": _source_to_anthropic(block.source)}
    if isinstance(block, Document):
        return {"type": "document", "source": _source_to_anthropic(block.source)}
    raise ValueError(f"Unsupported content block: {type(block).__name__}")


def _output_block_to_anthropic(block: OutputBlock) -> dict[str, Any]:
    """Blocks valid inside a tool_result's content list."""
    if isinstance(block, Text):
        return {"type": "text", "text": block.text}
    if isinstance(block, Image):
        return {"type": "image", "source": _source_to_anthropic(block.source)}
    if isinstance(block, Document):
        return {"type": "document", "source": _source_to_anthropic(block.source)}
    raise ValueError(f"Unsupported output block: {type(block).__name__}")


def _source_to_anthropic(source) -> dict[str, Any]:
    if isinstance(source, Base64Source):
        return {
            "type": "base64",
            "media_type": source.media_type,
            "data": source.data,
        }
    if isinstance(source, UrlSource):
        return {"type": "url", "url": source.url}
    raise ValueError(f"Unsupported source: {type(source).__name__}")


# ─── OpenAI / Azure-Foundry chat-completions request ─────────────────────
#
# Azure AI Foundry serves both the GPT-5.4 family (Azure OpenAI) and DeepSeek
# (Azure AI model inference) behind an OpenAI-compatible /chat/completions
# surface. The wire shape differs from Anthropic in three ways the converter
# handles:
#   1. The system prompt is a `role:"system"` message (not a top-level field).
#   2. Assistant tool calls live in `tool_calls` (function name + JSON-string
#      arguments), not inline `tool_use` content blocks.
#   3. Tool RESULTS are their own `role:"tool"` messages keyed by
#      `tool_call_id` — so a canonical user message carrying ToolResult blocks
#      expands into one `tool` message per result (+ a `user` message for any
#      accompanying text/media).
#
# No prompt-cache markers are emitted here — OpenAI/Azure cache automatically
# (or via their own headers), unlike Anthropic's explicit `cache_control`.

def to_openai_request(
    messages: list[Message],
    *,
    model: str,
    system: Optional[str] = None,
    max_tokens: int = 4096,
    tools: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Convert canonical messages + params to an OpenAI-compatible body.

    `model` is the provider's deployment/model name. `tools`, when present,
    are canonical Anthropic-shaped tool dicts (`{name, description,
    input_schema}`) and are translated to OpenAI function-tool shape.
    """
    system_parts: list[str] = []
    if system:
        system_parts.append(system)
    convo: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            for block in msg.content:
                if isinstance(block, Text):
                    system_parts.append(block.text)
            continue

        if msg.role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in msg.content:
                if isinstance(block, Text):
                    text_parts.append(block.text)
                elif isinstance(block, ToolUse):
                    tool_calls.append({
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input),
                        },
                    })
                # Images/documents in assistant output are not part of this
                # fleet's flows; ignore if they ever appear.
            content = "".join(text_parts)
            out: dict[str, Any] = {"role": "assistant"}
            # OpenAI accepts null content when tool_calls are present.
            out["content"] = content if content else None
            if tool_calls:
                out["tool_calls"] = tool_calls
            convo.append(out)
            continue

        # role == "user" (or a canonical "tool" role, treated the same): may
        # carry ToolResult blocks + text/media. Expand each ToolResult into a
        # standalone OpenAI tool message; collect the rest into a user message.
        user_parts: list[dict[str, Any]] = []
        for block in msg.content:
            if isinstance(block, ToolResult):
                convo.append({
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": _tool_result_content_to_openai(block),
                })
            elif isinstance(block, Text):
                user_parts.append({"type": "text", "text": block.text})
            elif isinstance(block, Image):
                user_parts.append(_image_to_openai(block))
            elif isinstance(block, Document):
                # OpenAI chat-completions has no document part; note it so the
                # model knows an attachment existed without crashing the call.
                user_parts.append({"type": "text", "text": "[document attachment omitted]"})
        if user_parts:
            if len(user_parts) == 1 and user_parts[0]["type"] == "text":
                convo.append({"role": "user", "content": user_parts[0]["text"]})
            else:
                convo.append({"role": "user", "content": user_parts})

    api_messages: list[dict[str, Any]] = []
    if system_parts:
        api_messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    api_messages.extend(convo)

    body: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = [_tool_to_openai(t) for t in tools]
    return body


def _tool_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    """Anthropic-shaped tool dict -> OpenAI function-tool shape."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}) or {"type": "object", "properties": {}},
        },
    }


def _tool_result_content_to_openai(block: ToolResult) -> str:
    """OpenAI tool messages take a string. Flatten block-list results to text."""
    if isinstance(block.content, str):
        return block.content
    texts: list[str] = []
    for b in block.content:
        if isinstance(b, Text):
            texts.append(b.text)
        elif isinstance(b, Image):
            texts.append("[image output omitted]")
        elif isinstance(b, Document):
            texts.append("[document output omitted]")
    return "\n".join(texts)


def _image_to_openai(block: Image) -> dict[str, Any]:
    src = block.source
    if isinstance(src, Base64Source):
        url = f"data:{src.media_type};base64,{src.data}"
    elif isinstance(src, UrlSource):
        url = src.url
    else:  # pragma: no cover - exhaustive
        raise ValueError(f"Unsupported source: {type(src).__name__}")
    return {"type": "image_url", "image_url": {"url": url}}
