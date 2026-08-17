"""
Pure-logic tests for the shared _format_datetime_for_qbo_query (U-254).

Was duplicated byte-identically across 9 qbo/*/external/client.py files with
ZERO test coverage before this unit; now collapsed into one home in
qbo/base/client.py. These tests pin the reachable formatting behavior so a
future edit to the sole copy can't silently drift for all 9 callers at once.
"""
from datetime import datetime, timezone

from integrations.intuit.qbo.base.client import _format_datetime_for_qbo_query


def test_none_returns_none():
    assert _format_datetime_for_qbo_query(None) is None


def test_datetime_object_with_tzinfo():
    dt = datetime(2026, 8, 17, 12, 30, 0, tzinfo=timezone.utc)
    assert _format_datetime_for_qbo_query(dt) == "2026-08-17T12:30:00+00:00"


def test_iso_string_with_z_suffix():
    assert _format_datetime_for_qbo_query("2026-08-17T12:30:00Z") == "2026-08-17T12:30:00+00:00"


def test_iso_string_with_explicit_offset():
    assert _format_datetime_for_qbo_query("2026-08-17T12:30:00+00:00") == "2026-08-17T12:30:00+00:00"


def test_date_only_string_gets_midnight_time():
    assert _format_datetime_for_qbo_query("2026-08-17") == "2026-08-17T00:00:00+00:00"


def test_hh_mm_without_seconds_gets_seconds_appended():
    assert _format_datetime_for_qbo_query("2026-08-17T12:30Z") == "2026-08-17T12:30:00+00:00"


def test_microseconds_are_stripped():
    assert _format_datetime_for_qbo_query("2026-08-17T12:30:00.123456Z") == "2026-08-17T12:30:00+00:00"
