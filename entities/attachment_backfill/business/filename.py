"""U-535b: parse a Box invoice filename (pure, no I/O).

Convention: ``PROJ - Vendor - Inv# - Description - CostCode - $Amount - M-D-YYYY.pdf``.
Parsed right to left: optional ``(n)`` duplicate suffix, our own Box-push identity suffix
(``-xxxxxxxx`` = 8 lowercase hex, see integrations/box/file/business/naming.py) which marks the
file as already linked (``pushed=True``), date, amount (``$1,234.56``, ``($1,234.56)`` and
``-$1,234.56`` credits are negative), optional cost code (``\\d{1,3}\\.\\d{1,2}``). At least three
head segments (project, vendor, invoice #[, description...]) must remain, else ``None``. The invoice is always the third head segment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

_PDF_EXT = re.compile(r"\.pdf$", re.IGNORECASE)
_DUP_SUFFIX = re.compile(r"\s*\(\d+\)$")
_PUSH_SUFFIX = re.compile(r"-[0-9a-f]{8}$")
_SEPARATOR = re.compile(r"\s+-\s+")
_DATE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_AMOUNT = re.compile(r"^(\()?\s*(-)?\s*\$\s*(-)?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?\s*(\))?$")
_COST_CODE = re.compile(r"^\d{1,3}\.\d{1,2}$")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")

MIN_HEAD_SEGMENTS = 3


@dataclass(frozen=True)
class ParsedFilename:
    project: str
    vendor: str
    invoice: str
    invoice_norm: str
    description: str
    amount: Decimal  # signed: credits are negative
    bill_date: Optional[date]
    cost_code: Optional[str]
    pushed: bool


def norm_invoice(value: Optional[str]) -> str:
    """Alphanumerics only, upper case, leading zeros stripped ("0" kept when all zeros)."""
    squashed = _NON_ALNUM.sub("", value or "").upper()
    if not squashed:
        return ""
    stripped = squashed.lstrip("0")
    return stripped or "0"


def norm_vendor(value: Optional[str]) -> str:
    """Upper case, non-alphanumerics collapsed to single spaces."""
    return " ".join(_NON_ALNUM.sub(" ", value or "").upper().split())


def parse_amount(segment: str) -> Optional[Decimal]:
    m = _AMOUNT.match(segment.strip())
    if not m:
        return None
    open_paren, minus_before, minus_after, whole, frac, close_paren = m.groups()
    if bool(open_paren) != bool(close_paren):
        return None
    try:
        value = Decimal(whole.replace(",", "") + (frac or ""))
    except InvalidOperation:
        return None
    if open_paren or minus_before or minus_after:
        value = -value
    return value


def _parse_date(segment: str) -> Optional[date]:
    m = _DATE.match(segment.strip())
    if not m:
        return None
    month, day, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_filename(name: str) -> Optional[ParsedFilename]:
    if not name or not _PDF_EXT.search(name):
        return None
    stem = _PDF_EXT.sub("", name.strip())
    stem = _DUP_SUFFIX.sub("", stem)
    pushed = bool(_PUSH_SUFFIX.search(stem))
    if pushed:
        stem = _PUSH_SUFFIX.sub("", stem)

    parts = [p.strip() for p in _SEPARATOR.split(stem.strip())]
    if not parts:
        return None

    bill_date = _parse_date(parts[-1])
    if bill_date is not None:
        parts = parts[:-1]
    if not parts:
        return None

    amount = parse_amount(parts[-1])
    if amount is None:
        return None
    parts = parts[:-1]

    cost_code: Optional[str] = None
    if parts and _COST_CODE.match(parts[-1]):
        cost_code = parts[-1]
        parts = parts[:-1]

    if len(parts) < MIN_HEAD_SEGMENTS:
        return None
    project, vendor, invoice = parts[0], parts[1], parts[2]
    description = " - ".join(parts[3:])
    if not project or not invoice:
        return None
    return ParsedFilename(
        project=project,
        vendor=vendor,
        invoice=invoice,
        invoice_norm=norm_invoice(invoice),
        description=description,
        amount=amount,
        bill_date=bill_date,
        cost_code=cost_code,
        pushed=pushed,
    )
