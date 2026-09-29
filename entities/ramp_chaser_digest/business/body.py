"""
Pure renderer for the Ramp chaser weekly digest (U-549 §6.2).

Callers enqueue via MS outbox ``send_mail`` with ``body_type=RAMP_CHASER_DIGEST_BODY_TYPE``
(plain ``Text``). HTML default collapses whitespace and would destroy the fixed-column layout
in Outlook; plain text preserves spaces and newlines.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

# Outbox payloads default to HTML; this digest must use Text (see module docstring).
RAMP_CHASER_DIGEST_BODY_TYPE = "Text"

_SECTION_RECEIPT_AND_MEMO = "Receipt & Memo"
_SECTION_RECEIPT_ONLY = "Receipt Only"
_SECTION_MEMO_ONLY = "Memo Only"

# Aged marker (← N days) only when chase age exceeds this; matches the 14-day deferred-escalation
# threshold — keep both in sync if either changes.
AGED_MARKER_THRESHOLD_DAYS = 14

_INTRO = (
    "When you have a moment, will you please jump into Ramp and complete the following items?"
)


def render_digest(
    *,
    first_name: str,
    card_holder: str,
    items: Sequence[Mapping[str, Any] | Any],
    now: datetime,
    tz: ZoneInfo,
) -> tuple[str, str]:
    """
    Build (subject, body) for one cardholder digest.

    Parameters
    ----------
    first_name:
        Greeting name (unescaped; trusted from Ramp user roster).
    card_holder:
        Full display name for the subject line (e.g. ``Pat Cardholder``).
    items:
        Open follow-up rows with keys/attrs: ``merchant_name``, ``amount`` (``Decimal`` or
        ``None``), ``transaction_date``, ``needs_memo``, ``needs_receipt``, ``first_seen_at``.
    now:
        Current instant, timezone-aware (typically ``datetime.now(tz)``). Used for subject date
        and aged markers so they match.
    tz:
        Business timezone for calendar dates and day counts.

    Returns
    -------
    tuple[str, str]
        Subject and plain-text body. Pass ``body_type=RAMP_CHASER_DIGEST_BODY_TYPE`` to outbox.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    subject_date = now.astimezone(tz).date().isoformat()
    subject = f"Ramp Action Needed - {subject_date} - {card_holder}"

    partitioned = _partition(items)
    all_rendered = (
        partitioned["receipt_and_memo"]
        + partitioned["receipt_only"]
        + partitioned["memo_only"]
    )
    amount_field_width = _section_amount_field_width(all_rendered)
    amount_start = _section_amount_start(all_rendered, tz=tz)

    body_parts: list[str] = [
        f"{first_name.strip() or 'there'},",
        "",
        _INTRO,
        "",
    ]

    for heading, bucket in (
        (_SECTION_RECEIPT_AND_MEMO, partitioned["receipt_and_memo"]),
        (_SECTION_RECEIPT_ONLY, partitioned["receipt_only"]),
        (_SECTION_MEMO_ONLY, partitioned["memo_only"]),
    ):
        if not bucket:
            continue
        body_parts.append(heading)
        for row in bucket:
            body_parts.append(
                _format_item_line(
                    row,
                    now=now,
                    tz=tz,
                    amount_field_width=amount_field_width,
                    amount_start=amount_start,
                )
            )
        body_parts.append("")

    while body_parts and body_parts[-1] == "":
        body_parts.pop()

    body_parts.extend(["", "Thanks,", "Chris"])
    body = "\n".join(body_parts)
    return subject, body


def _partition(
    items: Sequence[Mapping[str, Any] | Any],
) -> dict[str, list[Mapping[str, Any] | Any]]:
    receipt_and_memo: list[Any] = []
    receipt_only: list[Any] = []
    memo_only: list[Any] = []

    for item in items:
        needs_memo = bool(_field(item, "needs_memo"))
        needs_receipt = bool(_field(item, "needs_receipt"))
        if needs_memo and needs_receipt:
            receipt_and_memo.append(item)
        elif needs_receipt:
            receipt_only.append(item)
        elif needs_memo:
            memo_only.append(item)

    for bucket in (receipt_and_memo, receipt_only, memo_only):
        bucket.sort(key=_transaction_sort_key_for_sort, reverse=True)

    return {
        "receipt_and_memo": receipt_and_memo,
        "receipt_only": receipt_only,
        "memo_only": memo_only,
    }


def _field(item: Mapping[str, Any] | Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _transaction_sort_key_for_sort(item: Mapping[str, Any] | Any) -> tuple[int, str]:
    """Sort newest transaction first; unparseable dates sink to the bottom."""
    raw = _field(item, "transaction_date")
    if raw is None:
        return (0, "")
    text = str(raw).strip()
    if not text:
        return (0, "")
    parsed = _parse_transaction_instant(text)
    if parsed is None:
        parsed_date = _parse_transaction_date_only(text)
        if parsed_date is None:
            return (0, text)
        return (1, parsed_date.isoformat())
    return (2, parsed.astimezone(timezone.utc).isoformat())


def _section_amount_field_width(bucket: Sequence[Mapping[str, Any] | Any]) -> int:
    width = 0
    for row in bucket:
        if _field(row, "amount") is None:
            continue
        width = max(width, len(_format_amount(_field(row, "amount"))))
    return width


def _section_amount_start(bucket: Sequence[Mapping[str, Any] | Any], *, tz: ZoneInfo) -> int:
    start = 0
    for row in bucket:
        date_label = _format_transaction_date_label(_field(row, "transaction_date"), tz)
        merchant = str(_field(row, "merchant_name") or "").strip() or "(unknown merchant)"
        prefix = f"  {date_label:<6}   "
        start = max(start, len(prefix) + len(merchant) + 2)
    return start


def _format_item_line(
    item: Mapping[str, Any] | Any,
    *,
    now: datetime,
    tz: ZoneInfo,
    amount_field_width: int,
    amount_start: int,
) -> str:
    date_label = _format_transaction_date_label(_field(item, "transaction_date"), tz)
    merchant = str(_field(item, "merchant_name") or "").strip() or "(unknown merchant)"
    amount = _field(item, "amount")
    amount_str = _format_amount(amount)

    prefix = f"  {date_label:<6}   "
    if amount_field_width:
        amount_display = amount_str.rjust(amount_field_width)
    else:
        amount_display = amount_str

    left = f"{prefix}{merchant}"
    pad = max(2, amount_start - len(left))
    line = f"{left}{' ' * pad}{amount_display}"

    days = _days_since_first_seen(_field(item, "first_seen_at"), now=now, tz=tz)
    if days is not None and days > AGED_MARKER_THRESHOLD_DAYS:
        line = f"{line}   ← {days} days"
    return line.rstrip()


def _format_transaction_date_label(raw: Any, tz: ZoneInfo) -> str:
    if raw is None:
        return "?"
    text = str(raw).strip()
    if not text:
        return "?"

    if "T" not in text and "+" not in text and not text.endswith("Z"):
        as_date = _parse_transaction_date_only(text)
        if as_date is not None:
            return as_date.strftime("%b %d")

    instant = _parse_transaction_instant(text)
    if instant is None:
        return "?"
    return instant.astimezone(tz).strftime("%b %d")


def _parse_transaction_date_only(text: str) -> date | None:
    try:
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return date.fromisoformat(text[:10])
    except ValueError:
        return None
    return None


def _parse_transaction_instant(text: str) -> datetime | None:
    normalized = text.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # SQL CONVERT style on transaction_time would be rare; treat as UTC.
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_first_seen_at(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _days_since_first_seen(
    raw: Any,
    *,
    now: datetime,
    tz: ZoneInfo,
) -> int | None:
    first_seen = _parse_first_seen_at(raw)
    if first_seen is None:
        return None
    start = first_seen.astimezone(tz).date()
    end = now.astimezone(tz).date()
    return (end - start).days


def _format_amount(amount: Any) -> str:
    if amount is None:
        return ""
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    sign = "-" if amount < 0 else ""
    abs_part = abs(amount)
    return f"{sign}${abs_part:,.2f}"
