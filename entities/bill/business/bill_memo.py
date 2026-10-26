"""Ramp-style bill memo composed from PDF read fields + entry context (QBO private note)."""
from __future__ import annotations

from typing import Optional


def _non_empty(value: Optional[str]) -> Optional[str]:
    if value is None:
        t = ""
    else:
        t = str(value).strip()
    return None if t == "" else t


def _memo_head(entered_by: Optional[str], method: str) -> str:
    if entered_by:
        return f"{entered_by} via {method}"
    return method


def _doc_number_segment(
    read_bill_number: Optional[str],
    current_bill_number: Optional[str],
) -> Optional[str]:
    read = _non_empty(read_bill_number)
    current = _non_empty(current_bill_number)
    if not read:
        return None
    if read == current:
        return None
    return f"DOC#: {read}"


def entered_by_display_name(
    firstname: Optional[str],
    lastname: Optional[str],
    username: Optional[str],
) -> str:
    name = f"{firstname or ''} {lastname or ''}".strip()
    if name:
        return name
    return (username or "").strip()


def compose_bill_memo(
    *,
    entered_by: str,
    method: str,
    long_description: Optional[str],
    reference: Optional[str],
    read_bill_number: Optional[str],
    current_bill_number: Optional[str],
) -> str:
    entered = _non_empty(entered_by)
    long_desc = _non_empty(long_description)
    ref = _non_empty(reference)
    doc_number = _doc_number_segment(read_bill_number, current_bill_number)

    segments: list[str] = []
    head = _memo_head(entered, method)
    if head != "":
        segments.append(head)
    if long_desc:
        segments.append(long_desc)
    if ref:
        segments.append(f"Ref: {ref}")
    if doc_number:
        segments.append(doc_number)

    return " | ".join(segments)
