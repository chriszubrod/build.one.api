"""Pure-logic tests for analyze_attachment_duplicates helpers (U-261 investigate)."""
from __future__ import annotations

from datetime import datetime

from scripts.analyze_attachment_duplicates import (
    _classify_duplicate_timing,
    _classify_filename_shape,
    _coerce_datetime,
    _group_duplicates,
    _summarize_groups,
)


def test_classify_filename_shape_uuid_shaped():
    assert _classify_filename_shape("bills/a1b2c3d4-e5f6-7890-abcd-ef1234567890.pdf") == "uuid-shaped"
    assert _classify_filename_shape("A1B2C3D4-E5F6-7890-ABCD-EF1234567890") == "uuid-shaped"


def test_classify_filename_shape_human_shaped():
    assert _classify_filename_shape("462953.pdf") == "human-shaped"
    assert _classify_filename_shape("TB3 - Vendor - 12345 - desc - 01.01 - 100 - 1-1-2026.pdf") == "human-shaped"
    assert _classify_filename_shape("") == "human-shaped"


def test_classify_filename_shape_embedded_uuid_is_human_shaped():
    assert (
        _classify_filename_shape("Invoice-a1b2c3d4-e5f6-7890-abcd-ef1234567890-FINAL.pdf")
        == "human-shaped"
    )


def test_group_duplicates_no_duplicates():
    rows = [
        {
            "bill_id": 1,
            "bill_number": "100",
            "file_hash": "abc",
            "file_size": 100,
            "attachment_id": 10,
            "filename": "a.pdf",
            "created_datetime": datetime(2026, 1, 1, 12, 0, 0),
        }
    ]
    assert _group_duplicates(rows) == []


def test_group_duplicates_four_member_group():
    """Mirror bill_id=17531 / hash=58fc1b304888 / size=113408 documented shape."""
    times = [
        datetime(2026, 1, 1, 12, 0, 0),
        datetime(2026, 1, 1, 12, 0, 1),
        datetime(2026, 1, 1, 12, 0, 2),
        datetime(2026, 1, 1, 12, 0, 3),
    ]
    rows = [
        {
            "bill_id": 17531,
            "bill_number": "CL-1",
            "file_hash": "58fc1b304888",
            "file_size": 113408,
            "attachment_id": attachment_id,
            "filename": "doc.pdf",
            "created_datetime": created,
        }
        for attachment_id, created in enumerate(times, start=1)
    ]
    groups = _group_duplicates(rows)
    assert len(groups) == 1
    group = groups[0]
    assert group.bill_id == 17531
    assert group.file_hash == "58fc1b304888"
    assert group.file_size == 113408
    assert len(group.members) == 4
    assert [m.attachment_id for m in group.members] == [1, 2, 3, 4]
    assert [m.created_datetime for m in group.members] == times
    summary = _summarize_groups(groups)
    assert summary["excess_attachment_rows"] == 3
    assert summary["reclaimable_blob_bytes"] == 340224


def test_group_duplicates_single_group_burst():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t1 = datetime(2026, 1, 1, 12, 0, 2)
    rows = [
        {
            "bill_id": 17531,
            "bill_number": "CL-1",
            "file_hash": "58fc1b304888",
            "file_size": 113408,
            "attachment_id": 1,
            "filename": "doc.pdf",
            "created_datetime": t0,
        },
        {
            "bill_id": 17531,
            "bill_number": "CL-1",
            "file_hash": "58fc1b304888",
            "file_size": 113408,
            "attachment_id": 2,
            "filename": "doc.pdf",
            "created_datetime": t1,
        },
    ]
    groups = _group_duplicates(rows)
    assert len(groups) == 1
    group = groups[0]
    assert group.bill_id == 17531
    assert group.timing == "burst"
    assert [m.attachment_id for m in group.members] == [1, 2]
    assert all(m.filename_shape == "human-shaped" for m in group.members)


def test_group_duplicates_single_group_spread():
    rows = [
        {
            "bill_id": 17337,
            "bill_number": "B-1",
            "file_hash": "deadbeef",
            "file_size": 5000,
            "attachment_id": 11,
            "filename": "bills/a1b2c3d4-e5f6-7890-abcd-ef1234567890.pdf",
            "created_datetime": datetime(2026, 1, 1, 8, 0, 0),
        },
        {
            "bill_id": 17337,
            "bill_number": "B-1",
            "file_hash": "deadbeef",
            "file_size": 5000,
            "attachment_id": 12,
            "filename": "vendor-invoice.pdf",
            "created_datetime": datetime(2026, 1, 2, 8, 0, 0),
        },
    ]
    groups = _group_duplicates(rows)
    assert len(groups) == 1
    assert groups[0].timing == "spread"
    shapes = {m.filename_shape for m in groups[0].members}
    assert shapes == {"uuid-shaped", "human-shaped"}


def test_group_duplicates_multi_group():
    rows = [
        {
            "bill_id": 1,
            "bill_number": "A",
            "file_hash": "h1",
            "file_size": 10,
            "attachment_id": 1,
            "filename": "a.pdf",
            "created_datetime": datetime(2026, 1, 1),
        },
        {
            "bill_id": 1,
            "bill_number": "A",
            "file_hash": "h1",
            "file_size": 10,
            "attachment_id": 2,
            "filename": "a.pdf",
            "created_datetime": datetime(2026, 1, 1),
        },
        {
            "bill_id": 2,
            "bill_number": "B",
            "file_hash": "h2",
            "file_size": 20,
            "attachment_id": 3,
            "filename": "b.pdf",
            "created_datetime": datetime(2026, 1, 1),
        },
        {
            "bill_id": 2,
            "bill_number": "B",
            "file_hash": "h2",
            "file_size": 20,
            "attachment_id": 4,
            "filename": "b.pdf",
            "created_datetime": datetime(2026, 1, 1),
        },
    ]
    groups = _group_duplicates(rows)
    assert len(groups) == 2
    assert {(g.bill_id, g.file_hash) for g in groups} == {(1, "h1"), (2, "h2")}


def test_classify_duplicate_timing_burst_and_spread():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    assert _classify_duplicate_timing([t0, t0]) == "burst"
    assert _classify_duplicate_timing([t0, t0.replace(second=4)]) == "burst"
    assert _classify_duplicate_timing([t0, t0.replace(minute=1)]) == "spread"


def test_coerce_datetime_passes_through_datetime():
    dt = datetime(2026, 1, 1, 12, 0, 0)
    assert _coerce_datetime(dt) is dt


def test_coerce_datetime_non_datetime_returns_none():
    assert _coerce_datetime("2026-01-01 12:00:00") is None
    assert _coerce_datetime(None) is None
    assert _coerce_datetime(12345) is None


def test_summarize_groups():
    member_rows = [
        {
            "bill_id": 1,
            "bill_number": "A",
            "file_hash": "h",
            "file_size": 1000,
            "attachment_id": 1,
            "filename": "a.pdf",
            "created_datetime": datetime(2026, 1, 1),
        },
        {
            "bill_id": 1,
            "bill_number": "A",
            "file_hash": "h",
            "file_size": 1000,
            "attachment_id": 2,
            "filename": "a.pdf",
            "created_datetime": datetime(2026, 1, 1),
        },
    ]
    groups = _group_duplicates(member_rows)
    summary = _summarize_groups(groups)
    assert summary["duplicate_groups"] == 1
    assert summary["excess_attachment_rows"] == 1
    assert summary["reclaimable_blob_bytes"] == 1000
