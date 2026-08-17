# Python Standard Library Imports
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# Third-party Imports

# Local Imports

# If QBO ever exposes a reverse LinkedTxn to the source Bill/Purchase, only a
# Bill/Purchase entry is a usable source pointer (an Invoice entry, if present,
# is the forward consumption link, not the source). Measured 2026-08-16: no
# Bill/Purchase LinkedTxn observed at any lifecycle stage — see
# docs/rc_source_linking_signal_2026_08_16.md.
_SOURCE_TXN_TYPES = ("Bill", "Purchase")

# Public alias — the one place other modules (e.g. the U-242 measurement
# script) should import this rule from, rather than re-declaring it.
SOURCE_TXN_TYPES = _SOURCE_TXN_TYPES


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

    QBO returns the RC's reverse LinkedTxn as either a single object or a list
    (single-element results are collapsed to an object) — both shapes are
    normalized here. The first Bill/Purchase LinkedTxn entry is the source
    pointer:
        SourceTxnType    = LinkedTxn.TxnType   ('Bill' | 'Purchase')
        SourceTxnId      = LinkedTxn.TxnId     (source Bill/Purchase QBO id)
        SourceTxnLineId  = LinkedTxn.TxnLineId (source line id; may be absent)

    Measured 2026-08-16 (U-242): QBO never exposes a reverse Bill/Purchase
    LinkedTxn — un-invoiced RCs carry no LinkedTxn; invoiced RCs carry a forward
    Invoice pointer only. Source fields therefore parse as None today. The
    caller's merge still preserves any previously-stored pointer defensively
    (forward-compatible if QBO ever adds the reverse link) — see
    docs/rc_source_linking_signal_2026_08_16.md.
    """
    raw = raw or {}

    customer_ref = raw.get("CustomerRef") or {}
    if not isinstance(customer_ref, dict):
        customer_ref = {}

    has_been_invoiced = raw.get("HasBeenInvoiced")
    if has_been_invoiced is not None:
        has_been_invoiced = bool(has_been_invoiced)

    source_txn_type: Optional[str] = None
    source_txn_id: Optional[str] = None
    source_txn_line_id: Optional[str] = None

    linked = raw.get("LinkedTxn")
    if isinstance(linked, dict):
        linked = [linked]
    if isinstance(linked, list):
        for lt in linked:
            if not isinstance(lt, dict):
                continue
            if lt.get("TxnType") in _SOURCE_TXN_TYPES and lt.get("TxnId") is not None:
                source_txn_type = _as_str(lt.get("TxnType"))
                source_txn_id = _as_str(lt.get("TxnId"))
                source_txn_line_id = _as_str(lt.get("TxnLineId"))
                break

    return {
        "qbo_id": _as_str(raw.get("Id")),
        "customer_ref_value": _as_str(customer_ref.get("value")),
        "customer_ref_name": _as_str(customer_ref.get("name")),
        "txn_date": _as_str(raw.get("TxnDate")),
        "amount": _as_decimal(raw.get("Amount")),
        "has_been_invoiced": has_been_invoiced,
        "source_txn_type": source_txn_type,
        "source_txn_id": source_txn_id,
        "source_txn_line_id": source_txn_line_id,
    }


def merge_reimburse_charge(stored: dict, incoming: dict) -> dict:
    """
    PURE merge of a stored staging dict with a freshly-parsed incoming dict.

    Non-source fields take the incoming value (HasBeenInvoiced flips to true,
    amount / date / customer refs refresh). The source pointer is PRESERVED when
    the incoming re-pull carries NULL for it — mirrors the SQL CASE-WHEN-preserve
    defensively (nothing is captured from QBO today, but a stored pointer must
    not be nulled on re-pull) — see docs/rc_source_linking_signal_2026_08_16.md.
    """
    stored = stored or {}
    merged = dict(incoming or {})
    for key in ("source_txn_type", "source_txn_id", "source_txn_line_id"):
        if merged.get(key) is None and stored.get(key) is not None:
            merged[key] = stored.get(key)
    return merged
