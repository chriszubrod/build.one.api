"""Canonical-to-provider message conversion.

Each provider has one `to_<provider>_request` function. Keeping them side by
side makes it obvious when adding a variant (e.g. a new content block type)
requires updating every provider.
"""
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
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    if tools:
        body["tools"] = tools
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
