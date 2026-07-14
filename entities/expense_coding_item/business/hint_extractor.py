"""Deterministic Ramp memo hint extraction for expense coding suggestions.

Phase B.2 will swap ``extract_hints`` for a Haiku-backed implementation that
returns the same dict shape — callers must not depend on extraction internals.
"""

from __future__ import annotations

import re
from typing import Optional

# Project abbreviations carried in Ramp memos (PrivateNote), e.g. HE12, MR2-SITE, HP2.
_PROJECT_CODE_RE = re.compile(r"^[A-Za-z]{1,4}\d{0,3}(?:-[A-Za-z0-9]+)?$")

# PM cost-code shorthand, e.g. 2.01.
_SCC_HINT_RE = re.compile(r"^\d+\.\d+$")

# Leading street number + name fragment, e.g. 1577 Moran Rd.
_ADDRESS_RE = re.compile(
    r"(\d+\s+[A-Za-z][A-Za-z0-9\s\-]*"
    r"(?:\b(?:Rd|Road|St|Street|Ln|Lane|Blvd|Ave|Avenue|Dr|Drive|Way|Ct|Court|Pl|Place)\b)?)",
    re.IGNORECASE,
)

# Card transaction tail segments, e.g. 3892.
_CARD_TAIL_RE = re.compile(r"^\d{3,6}$")


def extract_hints(memo_text: str | None) -> dict:
    """Parse a Ramp/QBO PrivateNote memo into structured coding hints.

    Returns:
        {
            "project_hint": str | None,
            "address_hint": str | None,
            "scc_hint": str | None,
            "clean_description": str | None,
        }

    Phase B.2 replaces this pure function with a Haiku call returning the same keys.
    """
    empty = {
        "project_hint": None,
        "address_hint": None,
        "scc_hint": None,
        "clean_description": None,
    }
    if not memo_text or not str(memo_text).strip():
        return empty

    segments = [segment.strip() for segment in str(memo_text).split(" - ") if segment.strip()]
    if not segments:
        return empty

    project_hint: Optional[str] = None
    scc_hint: Optional[str] = None
    address_hint: Optional[str] = None
    description_segments: list[str] = []

    # Skip the leading cardholder-name segment when the memo has multiple segments.
    scan_segments = segments[1:] if len(segments) > 1 else segments

    for segment in scan_segments:
        if _CARD_TAIL_RE.match(segment):
            continue

        if project_hint is None and _PROJECT_CODE_RE.match(segment):
            project_hint = segment
            continue

        if scc_hint is None and _SCC_HINT_RE.match(segment):
            scc_hint = segment
            continue

        address_match = _ADDRESS_RE.search(segment)
        if address_hint is None and address_match:
            address_hint = address_match.group(1).strip()
            address_hint = re.sub(r"\bproject\b\.?$", "", address_hint, flags=re.IGNORECASE).strip()

        cleaned_segment = segment
        if address_match:
            cleaned_segment = _ADDRESS_RE.sub("", cleaned_segment)
            cleaned_segment = re.sub(r"\bfor\b", "", cleaned_segment, flags=re.IGNORECASE)
            cleaned_segment = re.sub(r"\bproject\b", "", cleaned_segment, flags=re.IGNORECASE)

        cleaned_segment = re.sub(r"\s+", " ", cleaned_segment).strip(" ,;-")
        if cleaned_segment:
            description_segments.append(cleaned_segment)

    # Each segment was already whitespace-collapsed + edge-trimmed above, so a
    # single-space join needs no further normalization.
    clean_description = " ".join(description_segments) or None

    return {
        "project_hint": project_hint,
        "address_hint": address_hint,
        "scc_hint": scc_hint,
        "clean_description": clean_description,
    }
