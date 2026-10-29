"""Cascade task: bill header fields from masked invoice text (U-676a arm B).

Payload keys (only these may appear on the dict passed to build_user_message):
    bill_text: str — truncated to BILL_HEADER_TEXT_CHAR_BUDGET (default 6000)

Output JSON:
    vendor_name, bill_number, bill_date (YYYY-MM-DD|null), total_amount (decimal str|null),
    short_description (<=60|null), long_description (<=200|null), reference (<=60|null),
    confidence 0..1. Legacy key ``description`` accepted (maps to long_description when absent).
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from entities.bill.business.description_format import truncate_at_word_boundary
from intelligence.cascade.bill_coding import truncate_bill_text
from intelligence.cascade.core import CascadeResult, Rung, StructuredTask, run_cascade
from intelligence.cascade.ladder_config import (
    DEFAULT_BILL_HEADER_EXTRACTION_LADDER,
    DEFAULT_BILL_HEADER_EXTRACTION_THRESHOLD,
    resolve_ladder,
    resolve_threshold,
)

BILL_HEADER_TEXT_CHAR_BUDGET = 6000
BILL_HEADER_THRESHOLD = DEFAULT_BILL_HEADER_EXTRACTION_THRESHOLD
MAX_SHORT_DESCRIPTION_LEN = 60
MAX_LONG_DESCRIPTION_LEN = 200
MAX_REFERENCE_LEN = 60
MAX_DESCRIPTION_LEN = MAX_LONG_DESCRIPTION_LEN  # legacy key alias for tests/backtest
MAX_VENDOR_NAME_LEN = 200
MAX_BILL_NUMBER_LEN = 80

ALLOWED_PAYLOAD_KEYS = frozenset({"bill_text"})

_RESULT_KEYS = frozenset(
    {
        "vendor_name",
        "bill_number",
        "bill_date",
        "total_amount",
        "short_description",
        "long_description",
        "reference",
        "description",
        "confidence",
    }
)

_SYSTEM_PROMPT = (
    "You extract header fields from a vendor invoice for a construction bookkeeping system.\n\n"
    "Rules:\n"
    "- Extract ONLY what is explicitly printed on the invoice; never guess.\n"
    "- vendor_name is the company ISSUING the invoice (seller/remit-to), NOT the "
    "bill-to or ship-to customer.\n"
    "- total_amount is the amount due for THIS invoice (balance due / invoice total).\n"
    "- bill_date is the invoice or document date on the invoice.\n"
    "- short_description names the THING bought or the work done in at most 5 words "
    '(e.g. "Poplar S4S Lumber", "Biweekly Mowing", "Drywall Repair"); do not add '
    "generic words such as Purchase, Order, Invoice, Bill, Payment, Services, "
    "Materials, Supplies, Charge, or Fee unless that word IS the item "
    '(e.g. "Delivery Fee", "Equipment Rental").\n'
    "- long_description is a fuller summary (<=200 chars) with key specifics, dates, "
    "or site when printed on the invoice.\n"
    "- reference is a PO / Job / work-order / customer reference printed on the invoice "
    "(NOT the invoice number itself); null when absent.\n"
    "- Use null for any field that is absent or unclear.\n\n"
    "Respond with ONLY a JSON object:\n"
    '{"vendor_name": "<str or null>", "bill_number": "<str or null>", '
    '"bill_date": "YYYY-MM-DD or null", "total_amount": "<decimal string or null>", '
    '"short_description": "<=60 chars or null>", "long_description": "<=200 chars or null>", '
    '"reference": "<=60 chars or null>", "confidence": <0..1>}\n'
    "confidence is your calibrated probability that the extracted header is correct overall."
)


def _normalize_vendor_name(val: Any) -> tuple[str | None, str | None]:
    if val is None:
        return None, None
    if not isinstance(val, str):
        return None, "vendor_name must be string or null"
    stripped = val.strip()
    if not stripped:
        return None, None
    if len(stripped) > MAX_VENDOR_NAME_LEN:
        return None, "vendor_name exceeds max length"
    return stripped, None


def _coerce_lenient_capped_string(
    val: Any,
    *,
    max_len: int,
    coerce_non_negative_int: bool = False,
    null_if_over: bool = False,
) -> str | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if coerce_non_negative_int and isinstance(val, int):
        if val < 0:
            return None
        stripped = str(val)
    elif isinstance(val, str):
        stripped = val.strip()
        if not stripped:
            return None
    else:
        return None
    if len(stripped) > max_len:
        if null_if_over:
            # An identifier cut short is a WRONG identifier; drop it instead.
            return None
        stripped = truncate_at_word_boundary(stripped, max_len)
    return stripped


def _apply_legacy_description(result: dict[str, Any]) -> None:
    """Map legacy ``description`` to ``long_description`` when the new key is absent."""
    legacy = result.get("description")
    if legacy is None:
        return
    if result.get("long_description") is None and isinstance(legacy, str):
        result["long_description"] = legacy


def _parse_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value.strip())
        return True
    except (TypeError, ValueError):
        return False


def _coerce_bill_number(val: Any) -> tuple[str | None, str | None]:
    if val is None:
        return None, None
    if isinstance(val, bool):
        return None, "bill_number must be string or null"
    if isinstance(val, float):
        return None, "bill_number must be string or null"
    if isinstance(val, int):
        if val < 0:
            return None, "bill_number must be string or null"
        return str(val), None
    if isinstance(val, str):
        return val, None
    return None, "bill_number must be string or null"


def _coerce_total_amount(val: Any) -> tuple[str | None, str | None]:
    if val is None:
        return None, None
    if isinstance(val, bool):
        return None, "total_amount must be decimal string or null"
    try:
        if isinstance(val, str):
            dec = Decimal(val.strip())
        elif isinstance(val, (int, float)):
            dec = Decimal(str(val))
        else:
            return None, "total_amount must be decimal string or null"
    except (InvalidOperation, AttributeError):
        return None, "total_amount not numeric"
    if not dec.is_finite():
        return None, "total_amount not numeric"
    if dec < 0 or dec >= Decimal("10000000000"):
        return None, "total_amount negative" if dec < 0 else "total_amount too large"
    return format(dec.normalize(), "f"), None


def validate_bill_header_result(result: dict[str, Any]) -> tuple[bool, str]:
    """Deterministic gate for parsed model JSON."""
    conf = result.get("confidence")
    if not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
        return False, "confidence out of range"

    _apply_legacy_description(result)

    vn, vn_err = _normalize_vendor_name(result.get("vendor_name"))
    if vn_err:
        return False, vn_err
    result["vendor_name"] = vn

    for key, max_len in (
        ("short_description", MAX_SHORT_DESCRIPTION_LEN),
        ("long_description", MAX_LONG_DESCRIPTION_LEN),
        ("description", MAX_LONG_DESCRIPTION_LEN),
    ):
        result[key] = _coerce_lenient_capped_string(
            result.get(key),
            max_len=max_len,
        )

    result["reference"] = _coerce_lenient_capped_string(
        result.get("reference"),
        max_len=MAX_REFERENCE_LEN,
        coerce_non_negative_int=True,
        null_if_over=True,
    )

    bn, bn_err = _coerce_bill_number(result.get("bill_number"))
    if bn_err:
        return False, bn_err
    if bn is not None:
        result["bill_number"] = bn
        if len(bn) > MAX_BILL_NUMBER_LEN:
            return False, "bill_number exceeds max length"

    bd = result.get("bill_date")
    if bd is not None:
        if not isinstance(bd, str) or not _parse_iso_date(bd):
            return False, "invalid bill_date"

    amt, amt_err = _coerce_total_amount(result.get("total_amount"))
    if amt_err:
        return False, amt_err
    if amt is not None:
        result["total_amount"] = amt

    extra = set(result.keys()) - _RESULT_KEYS
    if extra:
        return False, f"unexpected keys: {sorted(extra)}"

    return True, "ok"


def _build_user_message(payload: dict[str, Any]) -> str:
    extra = set(payload.keys()) - ALLOWED_PAYLOAD_KEYS
    if extra:
        raise ValueError(f"unexpected payload keys: {sorted(extra)}")
    safe = {
        "bill_text": truncate_bill_text(
            payload.get("bill_text") or "",
            budget=BILL_HEADER_TEXT_CHAR_BUDGET,
        ),
    }
    return json.dumps(safe, ensure_ascii=False, indent=2)


BILL_HEADER_EXTRACTION_TASK = StructuredTask(
    name="bill_header_extraction",
    system_prompt=_SYSTEM_PROMPT,
    threshold=BILL_HEADER_THRESHOLD,
    build_user_message=_build_user_message,
    validate=validate_bill_header_result,
    ladder=DEFAULT_BILL_HEADER_EXTRACTION_LADDER,
)


async def run_bill_header_extraction(
    payload: dict[str, Any],
    *,
    transport_for=None,
    ladder: tuple[Rung, ...] | None = None,
    fallback_on_daily_cap: bool = False,
) -> CascadeResult:
    """Run the bill header extraction ladder.

    ``fallback_on_daily_cap`` (U-701c) lets the Foundry fallback rungs run when
    Claude's daily cap is hit; BILL_HEADER_EXTRACTION_TASK itself stays False.
    """
    task = BILL_HEADER_EXTRACTION_TASK
    if ladder is None:
        task = dataclasses.replace(
            task,
            ladder=resolve_ladder(
                "bill_header_extraction", DEFAULT_BILL_HEADER_EXTRACTION_LADDER,
            ),
            threshold=resolve_threshold(
                "bill_header_extraction", BILL_HEADER_THRESHOLD,
            ),
        )
    else:
        task = dataclasses.replace(task, ladder=ladder)
    if fallback_on_daily_cap:
        task = dataclasses.replace(task, fallback_on_daily_cap=True)
    return await run_cascade(task, payload, transport_for=transport_for)
