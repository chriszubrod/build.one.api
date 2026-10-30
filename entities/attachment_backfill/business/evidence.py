"""U-535b: neutral per-PDF evidence over extracted text (pure, no I/O).

The check never presents a conclusion (Chris, round 3). It returns counts and flags that the
reviewer weighs while looking at the highlighted PDF; b3 highlights the hits live. The text is
only read here: it is never returned, stored or logged, and no matched value is kept.

Normalization (shared by document tokens and the bill number): alphanumerics, upper case,
leading zeros stripped, after removing an allowlisted invoice-label prefix (INV, INVOICE, INVNO,
NO, NUM, NBR, optional ``#``/``.``/``-``/``:``) that is directly followed by a digit, so
``INV-1001`` and ``1001`` are the same number. No other prefix is stripped; there is no substring
containment.

- ``invoice_seen_count``: whitespace tokens equal to the bill number (a 5-digit token right after
  a US state code is a ZIP and is skipped); a bill number holding whitespace also matches that many
  consecutive tokens joined. ``None`` when the bill number has < 3 normalized chars.
- ``amount_seen_count``: standalone ``1,234.50`` / ``1234.50`` occurrences with the bill's sign
  (``-x`` or ``(x)`` is negative). ``None`` for a zero or missing bill amount.
- ``vendor_seen``: a vendor-name token of >= 4 chars (generic words excluded); ``None`` when the
  vendor name has no usable token.
- ``other_invoice_label_count``: distinct values printed after an invoice label
  (``INVOICE``/``INV``, optional ``.``, then ``#``, ``NO``, ``NUMBER``, ``NUM``, ``ID`` or ``:``)
  that differ from the bill number. The value is the first invoice-shaped token (has a digit, not
  a date or money) on the label's line, else on the next line. "Previous / Original / Prior
  Invoice" labels are ignored.
- ``statement_like``: >= 3 distinct labelled values; or a statement title (or a line that is
  exactly STATEMENT) with >= 2 distinct labelled values, or with >= 3 lines that each carry a
  distinct invoice-shaped token and a money amount.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from entities.attachment_backfill.business.filename import _NON_ALNUM, norm_invoice, norm_vendor

MIN_CHECKABLE_INVOICE_LEN = 3
MIN_VENDOR_TOKEN_LEN = 4
STATEMENT_LABELLED_MIN = 3
STATEMENT_TITLED_LABELLED_MIN = 2
STATEMENT_TITLED_ROWS_MIN = 3

_VENDOR_STOPWORDS = frozenset(
    {
        "COMPANY", "CORP", "CORPORATION", "GROUP", "SUPPLY", "SUPPLIES", "SERVICE", "SERVICES",
        "CONSTRUCTION", "CONTRACTING", "CONTRACTORS", "HOME", "HOMES",
        "AND", "SALES", "SOLUTIONS", "ENTERPRISES", "INDUSTRIES", "BUILDING", "BUILDERS",
    }
)
_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)

_STATEMENT_TITLE = re.compile(
    r"\b(?:STATEMENT\s+OF\s+ACCOUNT|ACCOUNT\s+STATEMENT|ACCOUNT\s+ACTIVITY|AGING|BALANCE\s+FORWARD"
    r"|OPEN\s+INVOICES|INVOICE\s+REGISTER)\b"
)
_INVOICE_LABEL = re.compile(r"\b(?:INVOICE|INV)\b\.?\s*(?:#|NO\b\.?|NUMBER\b|NUM\b|ID\b|:)")
_IGNORED_LABEL_LEAD = re.compile(r"\b(?:PREVIOUS|ORIGINAL|PRIOR)\s*$")
_LABEL_PREFIX = re.compile(r"^(?:INVOICE|INVNO|INV|NO|NUM|NBR)[#.\-:\s]*(\d.*)$")
_DATE_TOKEN = re.compile(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}$")
_MONEY_TOKEN = re.compile(r"^\(?-?\$?\d[\d,]*\.\d{2}\)?$")
_STRIP = ".,;:()[]{}'\""


@dataclass(frozen=True)
class Evidence:
    invoice_seen_count: Optional[int]
    amount_seen_count: Optional[int]
    vendor_seen: Optional[bool]
    other_invoice_label_count: int
    statement_like: bool


def _invoice_core(value: Optional[str]) -> str:
    core = (value or "").strip(_STRIP).upper()
    m = _LABEL_PREFIX.match(core.lstrip("#"))
    return m.group(1) if m else core


def invoice_key(value: Optional[str]) -> str:
    """The shared normalized form of a bill number or a document token."""
    return norm_invoice(_invoice_core(value))


def _invoice_shaped(raw: str) -> bool:
    core = raw.strip(_STRIP)
    return bool(re.search(r"\d", core)) and not _DATE_TOKEN.match(core) and not _MONEY_TOKEN.match(core)


def _is_zip(tokens: list[str], i: int) -> bool:
    if i == 0 or not re.fullmatch(r"\d{5}(?:-\d{4})?", tokens[i].strip(_STRIP)):
        return False
    return tokens[i - 1].strip(_STRIP).upper() in _US_STATES


def _invoice_seen(text: str, bill_number: Optional[str], target: str) -> Optional[int]:
    if len(target) < MIN_CHECKABLE_INVOICE_LEN:
        return None
    tokens = text.split()
    seen = sum(1 for i, tok in enumerate(tokens) if invoice_key(tok) == target and not _is_zip(tokens, i))
    # A bill number holding whitespace ("AB 1234") also counts as that many consecutive tokens joined.
    width = len(_invoice_core(bill_number).split())
    if width > 1:
        # A window counts only when every token in it carries a letter or digit and none of them is
        # already the whole number on its own -- else "Invoice # AB1234" would be counted again.
        seen += sum(
            1
            for i in range(len(tokens) - width + 1)
            if all(_NON_ALNUM.sub("", t) and invoice_key(t) != target for t in tokens[i : i + width])
            and invoice_key("".join(tokens[i : i + width])) == target
        )
    return seen


def _amount_seen(text: str, total_amount) -> Optional[int]:
    if total_amount is None:
        return None
    try:
        amount = Decimal(str(total_amount))
    except InvalidOperation:
        return None
    if amount == 0:
        return None
    negative_bill = amount < 0
    magnitude = abs(amount)
    collapsed = " ".join(text.split())
    count = 0
    for rendered in {f"{magnitude:,.2f}", f"{magnitude:.2f}"}:
        for m in re.finditer(rf"(?<![\d,.]){re.escape(rendered)}(?!\d)", collapsed):
            left = collapsed[: m.start()].rstrip(" $")
            right = collapsed[m.end():].lstrip()
            negative = left.endswith("-") or (left.endswith("(") and right.startswith(")"))
            if negative == negative_bill:
                count += 1
    return count


def _vendor_seen(upper_text: str, vendor_name: Optional[str]) -> Optional[bool]:
    tokens = [
        tok
        for tok in norm_vendor(vendor_name).split()
        if len(tok) >= MIN_VENDOR_TOKEN_LEN and tok not in _VENDOR_STOPWORDS
    ]
    if not tokens:
        return None
    spaced = " " + " ".join(_NON_ALNUM.sub(" ", upper_text).split()) + " "
    return any(f" {tok} " in spaced for tok in tokens)


def _first_shaped(fragment: str) -> Optional[str]:
    for tok in fragment.split():
        if _invoice_shaped(tok):
            return tok
    return None


def labelled_invoice_keys(upper_text: str) -> set[str]:
    """Distinct normalized values printed after invoice labels (Previous/Original/Prior ignored)."""
    keys: set[str] = set()
    lines = upper_text.splitlines()
    for idx, line in enumerate(lines):
        for m in _INVOICE_LABEL.finditer(line):
            if _IGNORED_LABEL_LEAD.search(line[: m.start()]):
                continue
            nxt = _INVOICE_LABEL.search(line, m.end())
            value = _first_shaped(line[m.end(): nxt.start() if nxt else len(line)])
            if value is None and idx + 1 < len(lines):
                value = _first_shaped(lines[idx + 1])
            key = invoice_key(value) if value else ""
            if key:
                keys.add(key)
    return keys


def _money_rows(upper_text: str) -> int:
    """Lines carrying a distinct invoice-shaped token AND a money amount (statement tables)."""
    seen: set[str] = set()
    for line in upper_text.splitlines():
        tokens = line.split()
        if not any(_MONEY_TOKEN.match(tok.strip(".,;:")) for tok in tokens):
            continue
        value = _first_shaped(line)
        key = invoice_key(value) if value else ""
        if key:
            seen.add(key)
    return len(seen)


def _statement_like(upper_text: str, labelled: int) -> bool:
    if labelled >= STATEMENT_LABELLED_MIN:
        return True
    titled = bool(_STATEMENT_TITLE.search(" ".join(upper_text.split())))
    titled = titled or any(line.strip() == "STATEMENT" for line in upper_text.splitlines())
    if not titled:
        return False
    return labelled >= STATEMENT_TITLED_LABELLED_MIN or _money_rows(upper_text) >= STATEMENT_TITLED_ROWS_MIN


def collect_evidence(
    text: Optional[str],
    *,
    bill_number: Optional[str],
    total_amount,
    vendor_name: Optional[str],
) -> Evidence:
    body = text or ""
    upper = body.upper()
    target = invoice_key(bill_number)
    labelled = labelled_invoice_keys(upper)
    return Evidence(
        invoice_seen_count=_invoice_seen(body, bill_number, target),
        amount_seen_count=_amount_seen(body, total_amount),
        vendor_seen=_vendor_seen(upper, vendor_name),
        other_invoice_label_count=len({key for key in labelled if key != target}),
        statement_like=_statement_like(upper, len(labelled)),
    )
