"""U-535b b2: where the per-PDF evidence occurs in the text, as short context snippets (pure, no I/O).

Snippets are computed live for the reviewer on every open of a candidate and are never stored or
logged. Each hit is an occurrence that ``evidence.collect_evidence`` would count, found with the SAME
primitives (``invoice_key``, ``_is_zip``, ``_invoice_core``, ``_invoice_shaped``, the label patterns,
the vendor-token filter), so the uncapped hits agree with the stored counts:

- ``invoice_number``: one hit per token (or whitespace-split window) counted by ``invoice_seen_count``;
- ``amount``: one hit per occurrence counted by ``amount_seen_count``;
- ``vendor``: one hit per vendor-token occurrence (``vendor_seen`` is True iff there is at least one);
- ``other_invoice_label``: the first labelled occurrence of each distinct value counted by
  ``other_invoice_label_count``.

A snippet is ``{kind, before, hit, after}``: the hit as printed (whitespace collapsed) plus ~60 chars of context each
side (whitespace runs collapsed). A snippet carries text, never a judgement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from entities.attachment_backfill.business.evidence import (
    MIN_CHECKABLE_INVOICE_LEN,
    MIN_VENDOR_TOKEN_LEN,
    _IGNORED_LABEL_LEAD,
    _INVOICE_LABEL,
    _VENDOR_STOPWORDS,
    _invoice_core,
    _invoice_shaped,
    _is_zip,
    invoice_key,
)
from entities.attachment_backfill.business.filename import _NON_ALNUM, norm_vendor

KIND_INVOICE = "invoice_number"
KIND_AMOUNT = "amount"
KIND_VENDOR = "vendor"
KIND_OTHER_LABEL = "other_invoice_label"
KINDS = (KIND_INVOICE, KIND_AMOUNT, KIND_VENDOR, KIND_OTHER_LABEL)

CONTEXT_CHARS = 60
MAX_SNIPPETS = 20
MAX_PER_KIND = 5

_ALNUM_RUN = re.compile(r"[A-Za-z0-9]+")
_WS_RUN = re.compile(r"\s+")


@dataclass(frozen=True)
class Hit:
    kind: str
    haystack: str  # the string start/end index into
    start: int
    end: int


# -- shared primitives ------------------------------------------------------------------------- #


def token_spans(text: str) -> list[tuple[str, int, int]]:
    """(token, start, end) of every ``text.split()`` token, in order -- the same tokens evidence counts."""
    spans: list[tuple[str, int, int]] = []
    pos = 0
    for tok in text.split():
        start = text.find(tok, pos)  # only whitespace lies between pos and the next token
        spans.append((tok, start, start + len(tok)))
        pos = start + len(tok)
    return spans


def amount_renderings(total_amount) -> Optional[tuple[bool, frozenset[str]]]:
    """(bill is negative, rendered magnitudes) exactly as ``evidence._amount_seen`` renders them; None = not checkable."""
    if total_amount is None:
        return None
    try:
        amount = Decimal(str(total_amount))
    except InvalidOperation:
        return None
    if amount == 0:
        return None
    magnitude = abs(amount)
    return amount < 0, frozenset({f"{magnitude:,.2f}", f"{magnitude:.2f}"})


def vendor_tokens(vendor_name: Optional[str]) -> list[str]:
    """The vendor-name tokens ``evidence._vendor_seen`` looks for."""
    return [
        tok
        for tok in norm_vendor(vendor_name).split()
        if len(tok) >= MIN_VENDOR_TOKEN_LEN and tok not in _VENDOR_STOPWORDS
    ]


# -- per-kind hits (uncapped) ------------------------------------------------------------------ #


def invoice_hits(text: str, bill_number: Optional[str]) -> list[Hit]:
    target = invoice_key(bill_number)
    if len(target) < MIN_CHECKABLE_INVOICE_LEN:
        return []
    spans = token_spans(text)
    tokens = [tok for tok, _s, _e in spans]
    hits = [
        Hit(KIND_INVOICE, text, start, end)
        for i, (tok, start, end) in enumerate(spans)
        if invoice_key(tok) == target and not _is_zip(tokens, i)
    ]
    width = len(_invoice_core(bill_number).split())
    if width > 1:
        for i in range(len(tokens) - width + 1):
            window = tokens[i : i + width]
            if all(_NON_ALNUM.sub("", t) and invoice_key(t) != target for t in window) and (
                invoice_key("".join(window)) == target
            ):
                hits.append(Hit(KIND_INVOICE, text, spans[i][1], spans[i + width - 1][2]))
    hits.sort(key=lambda h: h.start)
    return hits


def amount_hits(text: str, total_amount) -> list[Hit]:
    rendered = amount_renderings(total_amount)
    if rendered is None:
        return []
    negative_bill, forms = rendered
    collapsed = " ".join(text.split())
    hits: list[Hit] = []
    for form in forms:
        for m in re.finditer(rf"(?<![\d,.]){re.escape(form)}(?!\d)", collapsed):
            left = collapsed[: m.start()].rstrip(" $")
            right = collapsed[m.end():].lstrip()
            negative = left.endswith("-") or (left.endswith("(") and right.startswith(")"))
            if negative == negative_bill:
                hits.append(Hit(KIND_AMOUNT, collapsed, m.start(), m.end()))
    hits.sort(key=lambda h: h.start)
    return hits


def vendor_hits(upper_text: str, vendor_name: Optional[str]) -> list[Hit]:
    wanted = set(vendor_tokens(vendor_name))
    if not wanted:
        return []
    # A token is seen when it is a whole maximal alphanumeric run (evidence pads the de-punctuated text with spaces).
    return [Hit(KIND_VENDOR, upper_text, m.start(), m.end()) for m in _ALNUM_RUN.finditer(upper_text) if m.group(0) in wanted]


def _first_shaped_span(line: str, lo: int, hi: int) -> Optional[tuple[int, int]]:
    fragment = line[lo:hi]
    for tok, s, e in token_spans(fragment):
        if _invoice_shaped(tok):
            return lo + s, lo + e
    return None


def other_label_hits(upper_text: str, bill_number: Optional[str]) -> list[Hit]:
    """First occurrence of each distinct labelled value != the bill number (``evidence.labelled_invoice_keys``)."""
    target = invoice_key(bill_number)
    lines = upper_text.splitlines(keepends=True)
    offsets, pos = [], 0
    for raw in lines:
        offsets.append(pos)
        pos += len(raw)
    bare = upper_text.splitlines()
    seen: set[str] = set()
    hits: list[Hit] = []
    for idx, line in enumerate(bare):
        for m in _INVOICE_LABEL.finditer(line):
            if _IGNORED_LABEL_LEAD.search(line[: m.start()]):
                continue
            nxt = _INVOICE_LABEL.search(line, m.end())
            span = _first_shaped_span(line, m.end(), nxt.start() if nxt else len(line))
            where = idx
            if span is None and idx + 1 < len(bare):
                span = _first_shaped_span(bare[idx + 1], 0, len(bare[idx + 1]))
                where = idx + 1
            if span is None:
                continue
            value = bare[where][span[0] : span[1]]
            key = invoice_key(value)
            if not key or key == target or key in seen:
                continue
            seen.add(key)
            hits.append(Hit(KIND_OTHER_LABEL, upper_text, offsets[where] + span[0], offsets[where] + span[1]))
    return hits


def find_hits(
    text: Optional[str],
    *,
    bill_number: Optional[str],
    total_amount,
    vendor_name: Optional[str],
) -> dict[str, list[Hit]]:
    """Every hit, by kind, uncapped."""
    body = text or ""
    return _hits(body, body.upper(), bill_number=bill_number, total_amount=total_amount, vendor_name=vendor_name)


def _hits(
    body: str, upper: str, *, bill_number: Optional[str], total_amount, vendor_name: Optional[str]
) -> dict[str, list[Hit]]:
    return {
        KIND_INVOICE: invoice_hits(body, bill_number),
        KIND_AMOUNT: amount_hits(body, total_amount),
        KIND_VENDOR: vendor_hits(upper, vendor_name),
        KIND_OTHER_LABEL: other_label_hits(upper, bill_number),
    }


# -- snippets ---------------------------------------------------------------------------------- #


def _squash(value: str) -> str:
    return _WS_RUN.sub(" ", value)


def _snippet(hit: Hit, body: str, upper: str, context: int) -> dict:
    hay = hit.haystack
    # Hits found on the upper-cased text are shown from the original text when the two align char-for-char.
    if hit.kind in (KIND_VENDOR, KIND_OTHER_LABEL) and len(upper) == len(body):
        hay = body
    return {
        "kind": hit.kind,
        "before": _squash(hay[max(0, hit.start - context) : hit.start]),
        "hit": _squash(hay[hit.start : hit.end]),
        "after": _squash(hay[hit.end : hit.end + context]),
    }


def collect_snippets(
    text: Optional[str],
    *,
    bill_number: Optional[str],
    total_amount,
    vendor_name: Optional[str],
    max_total: int = MAX_SNIPPETS,
    max_per_kind: int = MAX_PER_KIND,
    context: int = CONTEXT_CHARS,
) -> list[dict]:
    """At most ``max_per_kind`` snippets of each kind, ``max_total`` overall, in KINDS order then text order."""
    body = text or ""
    upper = body.upper()
    by_kind = _hits(body, upper, bill_number=bill_number, total_amount=total_amount, vendor_name=vendor_name)
    out: list[dict] = []
    for kind in KINDS:
        for hit in by_kind[kind][:max_per_kind]:
            if len(out) >= max_total:
                return out
            out.append(_snippet(hit, body, upper, context))
    return out
