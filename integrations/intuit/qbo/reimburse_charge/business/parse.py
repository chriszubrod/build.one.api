# Python Standard Library Imports
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# Third-party Imports

# Local Imports


def _as_decimal(value: Any) -> Optional[Decimal]:
    """Money -> Decimal(str(value)); never float (avoids binary-fp drift)."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


from integrations.intuit.qbo.base.ids import normalize_qbo_id


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def parse_reimburse_charge(raw: dict) -> dict:
    """
    PURE parse of a raw QBO ReimburseCharge dict into the staging field set.
    """
    raw = raw or {}

    customer_ref = raw.get("CustomerRef") or {}
    if not isinstance(customer_ref, dict):
        customer_ref = {}

    has_been_invoiced = raw.get("HasBeenInvoiced")
    if has_been_invoiced is not None:
        has_been_invoiced = bool(has_been_invoiced)

    return {
        # U-507 round 2: normalize, do NOT just stringify. `_as_str` is a bare
        # str(), so a whitespace-only Id ("\t", NBSP, " 900 ") stayed TRUTHY and
        # sailed through `if not parsed.get("qbo_id")` in the service -- staging a
        # garbage identity, recording SUCCESS, and ADVANCING the watermark past
        # itself. That is the exact silent-loss shape U-507 exists to close, and
        # reimburse_charge is watermarked. `normalize_qbo_id` returns None for a
        # blank, so the existing guard fires and records a staging FAILURE (hold).
        "qbo_id": normalize_qbo_id(raw.get("Id")),
        "customer_ref_value": _as_str(customer_ref.get("value")),
        "customer_ref_name": _as_str(customer_ref.get("name")),
        "txn_date": _as_str(raw.get("TxnDate")),
        "amount": _as_decimal(raw.get("Amount")),
        "has_been_invoiced": has_been_invoiced,
    }
