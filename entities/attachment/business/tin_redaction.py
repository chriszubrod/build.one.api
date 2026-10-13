"""Pure TIN (SSN/EIN) redaction for attachment extraction text.

Contract:
- ``mask_tins`` and ``mask_tins_in_fields`` are side-effect free: no I/O, no logging,
  never emit input. Masking is length-preserving: it replaces single characters with
  ``*`` and never inserts or removes one.
- ``aggressive=True`` (taxpayer documents, or unknown): EVERY numeric character is
  masked (``str.isnumeric`` — ASCII and Unicode digits, superscripts, circled and
  fullwidth digits, numerals), and so is every character this interpreter's Unicode
  table does not know (category ``Cn``, since a digit added in a newer Unicode version
  is unassigned here) and every lone surrogate (category ``Cs``, since a high/low pair
  decodes to a character such as a mathematical digit once the JSON is read back).
  All of these fail closed. A W-9's TIN can be split by box separators, odd
  whitespace or interleaved labels, so no shape rule is a safe boundary; nothing reads
  digits from W-9 extraction text (W-9 ingest calls Document Intelligence directly).
- ``aggressive=False`` (all other documents): SSN/ITIN shapes ``ddd-dd-dddd`` and EIN
  shapes ``dd-ddddddd`` are masked, keeping the last four digits. A digit is any
  ``str.isdigit`` character; a dash is any Unicode dash punctuation (category Pd) or the
  minus sign U+2212; any whitespace may sit around each dash; the shape must not touch
  another digit. An invoice or reference number shaped like an EIN is masked too
  (accepted, Chris 2026-10-13); vendor invoice-number parsing runs before masking.
- ``mask_tins_in_fields`` joins fields with ``\\n``, masks once (so a shape split across
  fields is caught), then splits back by the original field lengths.
"""
from __future__ import annotations

import re
import unicodedata

# Aggressive mode masks these besides numerics: unassigned here, or a lone surrogate.
_FAIL_CLOSED_CATEGORIES = frozenset({"Cn", "Cs"})

# SSN/EIN shapes are matched on a same-length "class string" ('d' digit, '-' dash,
# ' ' whitespace, 'x' anything else), so Unicode digit/dash variants need no giant
# character classes and match spans map 1:1 back onto the original text.
_TIN_CLASS_RE = re.compile(r"(?<!d)(?:ddd *- *dd *- *dddd|dd *- *ddddddd)(?!d)")


def _char_class(ch: str) -> str:
    if ch.isdigit():
        return "d"
    if ch == "−" or unicodedata.category(ch) == "Pd":
        return "-"
    if ch.isspace():
        return " "
    return "x"


def _mask_keep_last4(span: str) -> str:
    digit_indices = [i for i, c in enumerate(span) if c.isdigit()]
    chars = list(span)
    for i in digit_indices[:-4]:
        chars[i] = "*"
    return "".join(chars)


def _mask_every_numeric(text: str) -> tuple[str, int]:
    out: list[str] = []
    runs = 0
    in_run = False
    for ch in text:
        if ch.isnumeric() or unicodedata.category(ch) in _FAIL_CLOSED_CATEGORIES:
            out.append("*")
            if not in_run:
                runs += 1
            in_run = True
        else:
            out.append(ch)
            in_run = False
    return "".join(out), runs


def mask_tins(text: str | None, *, aggressive: bool) -> tuple[str, int]:
    """Return (masked_text, count). count = runs of masked characters (aggressive) or SSN/EIN shapes masked."""
    if not text:
        return "", 0
    if aggressive:
        return _mask_every_numeric(text)
    classes = "".join(_char_class(ch) for ch in text)
    spans = [m.span() for m in _TIN_CLASS_RE.finditer(classes)]
    if not spans:
        return text, 0
    chars = list(text)
    for start, end in spans:
        chars[start:end] = _mask_keep_last4(text[start:end])
    return "".join(chars), len(spans)


def mask_tins_in_fields(fields: list[str], *, aggressive: bool) -> tuple[list[str], int]:
    if not fields:
        return [], 0
    joined = "\n".join(fields)
    masked, count = mask_tins(joined, aggressive=aggressive)
    if len(masked) != len(joined):  # never strip-able under -O, unlike assert
        raise RuntimeError("TIN masking must be length-preserving")
    out: list[str] = []
    pos = 0
    for field in fields:
        out.append(masked[pos : pos + len(field)])
        pos += len(field) + 1  # the "\n" joiner
    return out, count


def mask_tins_in_kvps(kvps: list[dict], *, aggressive: bool) -> tuple[list[dict], int]:
    """Mask a DI key/value list. Taxpayer (aggressive) documents keep NO pairs: a TIN split
    across fields or labels has no safe shape rule, and nothing reads W-9 extraction KVPs.
    Otherwise keys and values are masked as one joined text; None stays None."""
    if aggressive:
        return [], 0
    flat = ["" if kvp.get(f) is None else kvp[f] for kvp in kvps for f in ("key", "value")]
    masked, count = mask_tins_in_fields(flat, aggressive=False)
    return [
        {
            **kvp,
            "key": None if kvp.get("key") is None else mk,
            "value": None if kvp.get("value") is None else mv,
        }
        for kvp, mk, mv in zip(kvps, masked[0::2], masked[1::2])
    ], count
