"""Canonical message format for the intelligence layer.

Every provider adapter converts to and from these types. This is the single
source of truth for the shape of a conversation anywhere in the system.

Content blocks split into two overlapping sets:

- ContentBlock — anything that can appear in a Message's content:
    Text | ToolUse | ToolResult | Image | Document

- OutputBlock — anything that can appear inside ToolResult.content when the
  tool returns a block list (image-returning tools, document previews):
    Text | Image | Document
"""
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


Role = Literal["system", "user", "assistant", "tool"]


# ─── Source references for media blocks ──────────────────────────────────

class Base64Source(BaseModel):
    type: Literal["base64"] = "base64"
    media_type: str  # e.g. "image/png", "application/pdf"
    data: str        # base64-encoded content


class UrlSource(BaseModel):
    type: Literal["url"] = "url"
    url: str


Source = Annotated[
    Union[Base64Source, UrlSource],
    Field(discriminator="type"),
]


# ─── Content block variants ──────────────────────────────────────────────

class Text(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUse(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class Image(BaseModel):
    type: Literal["image"] = "image"
    source: Source


class Document(BaseModel):
    type: Literal["document"] = "document"
    source: Source


OutputBlock = Annotated[
    Union[Text, Image, Document],
    Field(discriminator="type"),
]


class ToolResult(BaseModel):
    """Tool-result content block. Lives in user messages after a tool call.

    `content` is either a plain string (simple text result) or a list of
    OutputBlocks (text/image/document). The block-list form supports tools
    that return vision-relevant output — chart renderers, PDF previews,
    OCR results, etc.
    """
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: Union[str, list[OutputBlock]]
    is_error: bool = False


ContentBlock = Annotated[
    Union[Text, ToolUse, ToolResult, Image, Document],
    Field(discriminator="type"),
]


class Message(BaseModel):
    role: Role
    content: list[ContentBlock]
