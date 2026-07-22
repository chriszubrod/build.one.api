"""Pure-logic tests for vendor-compliance derived validity (U-089).

Validity is computed on read from a document's ExpiryDate vs. today. These tests
pin the boundary behavior of `compute_doc_status` / `days_until_expiry` so a future
change can't silently move the expiring-soon window or the expired/valid edges.
"""
from datetime import date

from entities.vendor_compliance.business.validity import (
    EXPIRING_SOON_DAYS,
    compute_doc_status,
    days_until_expiry,
)

TODAY = date(2026, 7, 19)


def test_expiring_soon_window_is_30_days():
    assert EXPIRING_SOON_DAYS == 30


def test_none_or_blank_or_garbage_expiry_is_incomplete():
    # A present doc row with no resolved expiry (COI w/ zero policies, or a
    # license saved without an expiry) reads as 'incomplete', never 'valid'.
    assert compute_doc_status(None, TODAY) == "incomplete"
    assert compute_doc_status("", TODAY) == "incomplete"
    assert compute_doc_status("not-a-date", TODAY) == "incomplete"


def test_past_expiry_is_expired():
    assert compute_doc_status("2026-07-18", TODAY) == "expired"  # yesterday
    assert compute_doc_status("2020-01-01", TODAY) == "expired"


def test_today_and_within_window_is_expiring():
    assert compute_doc_status("2026-07-19", TODAY) == "expiring"  # today, days=0
    assert compute_doc_status("2026-07-20", TODAY) == "expiring"  # tomorrow
    assert compute_doc_status("2026-08-18", TODAY) == "expiring"  # today + 30 (edge, inclusive)


def test_beyond_window_is_valid():
    assert compute_doc_status("2026-08-19", TODAY) == "valid"  # today + 31 (just past the edge)
    assert compute_doc_status("2027-01-01", TODAY) == "valid"


def test_tolerates_full_datetime_string():
    # ExpiryDate is surfaced as 'YYYY-MM-DD', but be defensive about a datetime prefix.
    assert compute_doc_status("2027-01-01T00:00:00", TODAY) == "valid"


def test_days_until_expiry():
    assert days_until_expiry("2026-08-18", TODAY) == 30
    assert days_until_expiry("2026-07-19", TODAY) == 0
    assert days_until_expiry("2026-07-18", TODAY) == -1
    assert days_until_expiry(None, TODAY) is None
    assert days_until_expiry("garbage", TODAY) is None
