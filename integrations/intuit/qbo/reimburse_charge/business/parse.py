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
        "qbo_id": _as_str(raw.get("Id")),
        "customer_ref_value": _as_str(customer_ref.get("value")),
        "customer_ref_name": _as_str(customer_ref.get("name")),
        "txn_date": _as_str(raw.get("TxnDate")),
        "amount": _as_decimal(raw.get("Amount")),
        "has_been_invoiced": has_been_invoiced,
    }
