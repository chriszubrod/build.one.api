from datetime import date
from typing import Optional

EXPIRING_SOON_DAYS = 30


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    # ExpiryDate is surfaced as 'YYYY-MM-DD'; tolerate a full datetime string too.
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def days_until_expiry(expiry_date: Optional[str], today: date) -> Optional[int]:
    d = _parse_iso_date(expiry_date)
    return None if d is None else (d - today).days


def compute_doc_status(expiry_date: Optional[str], today: date) -> str:
    """Status of a doc that EXISTS. 'missing' is set by the caller when no doc row exists."""
    d = _parse_iso_date(expiry_date)
    if d is None:
        return "incomplete"
    if d < today:
        return "expired"
    if (d - today).days <= EXPIRING_SOON_DAYS:
        return "expiring"
    return "valid"
