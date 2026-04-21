"""JSON schema generation for tool inputs.

Pydantic produces JSON Schema that Anthropic's input_schema field accepts
with only minor cleanup. Keeping the adapter here (rather than inline in
every tool definition) means schema quirks get fixed in one place.
"""
from typing import Any

from pydantic import BaseModel


def input_schema_from(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON schema dict suitable for Anthropic's `input_schema`.

    Pydantic emits `$defs` for nested models. Anthropic accepts `$defs` for
    references, so we leave the schema mostly alone — we only ensure the
    top-level `type` is present (pydantic omits it for empty models).
    """
    schema = model.model_json_schema()
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema
