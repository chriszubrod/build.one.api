"""U-778a: read a line's ReimburseCharge link out of QBO's line-level ``LinkedTxn``.

QBO stamps ``"LinkedTxn": [{"TxnId": "68056", "TxnType": "ReimburseCharge"}]`` on every Billable /
HasBeenBilled Bill / Purchase / VendorCredit line. Exactly one distinct ReimburseCharge id is a link;
none is "no link"; two or more distinct ids is ambiguous and is never guessed.
"""
from __future__ import annotations

import logging
from typing import Any, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

REIMBURSE_CHARGE_TXN_TYPE = "ReimburseCharge"


class MissingChargeLine(NamedTuple):
    """A staged Billable/HasBeenBilled line with no stored ReimburseCharge link yet."""
    id: int
    qbo_line_id: str
    header_qbo_id: str


MAX_QBO_ID_LENGTH = 50  # the NVARCHAR(50) width of every staged QBO id column: never store a truncated id


def strict_qbo_txn_id(value: Any) -> Optional[str]:
    """A LinkedTxn ``TxnId`` as an ASCII digit string of 1..50 characters, or None.

    Deliberately STRICTER than ``integrations.intuit.qbo.base.ids.normalize_qbo_id`` (a lenient keying helper for
    reconcile diffs): this one decides whether a value may be STORED as a charge link, so anything malformed —
    bool, float, container, non-digit or over-long string, negative or oversized int — is "no link" (None). A missing
    link is acceptable; a wrong or truncated one is not (U-778a fix rounds 2-4).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value < 0 or value >= 10 ** MAX_QBO_ID_LENGTH:  # bound BEFORE str(): huge ints raise on conversion
            return None
        return str(value)
    if isinstance(value, str):
        value = value.strip()
        if 0 < len(value) <= MAX_QBO_ID_LENGTH and value.isascii() and value.isdigit():
            return value
    return None


def _field(item: Any, snake: str, camel: str) -> Any:
    if isinstance(item, dict):
        return item.get(camel, item.get(snake))
    return getattr(item, snake, None)


def reimburse_charge_link(linked_txn: Any) -> Tuple[Optional[str], bool]:
    """Return ``(charge_id, ambiguous)``; logs a warning when ambiguous."""
    ids: set = set()
    for item in linked_txn or []:
        if _field(item, "txn_type", "TxnType") != REIMBURSE_CHARGE_TXN_TYPE:
            continue
        txn_id = strict_qbo_txn_id(_field(item, "txn_id", "TxnId"))
        if txn_id:
            ids.add(txn_id)
    if len(ids) == 1:
        return next(iter(ids)), False
    if len(ids) > 1:
        logger.warning("Line carries %d distinct ReimburseCharge links %s; not guessing", len(ids), sorted(ids))
        return None, True
    return None, False


def reimburse_charge_id_from_linked_txn(linked_txn: Any) -> Optional[str]:
    """The line's single distinct ReimburseCharge TxnId, or None (none, or ambiguous)."""
    return reimburse_charge_link(linked_txn)[0]
