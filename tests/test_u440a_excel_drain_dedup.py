"""
U-440a — the MS Excel drain is idempotent on column Z.

Before this unit, `_handle_insert_excel_row` / `_handle_append_excel_row` replayed
the writer's enqueue-time skip-vs-insert decision blindly. Anything that re-drove a
row — a retry after partial failure, or the U-440b stuck-claim reclaim — wrote a
SECOND copy of a line already in the sheet. That is the 2026-08-06 incident:
27 duplicate DETAILS rows across 8 client trackers.

Box never had the exposure (`apply_rows_to_details` re-reads column Z at drain).
These tests pin the MS side to the same contract:

  * a row whose column-Z key is already present is DROPPED
  * an unreadable sheet FAILS CLOSED (raise, never a blind write)
  * a row with no column-Z key is KEPT (can't prove a duplicate; dropping it
    would lose real ledger data)
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from integrations.ms.base.errors import MsGraphError, MsServerError
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.worker import (
    RECONCILIATION_KEY_COL_INDEX,
    MsOutboxWorker,
)

_CLIENT = "integrations.ms.sharepoint.external.client"

PID_PRESENT = "8D6E16A4-78F2-4CA3-89B9-5CA89C1A45B2"
PID_NEW = "339E02BB-8546-4690-AA9D-BE7A530B6A69"


def _sheet_row(public_id=None):
    """
    A 26-wide DETAILS row keyed by column Z. Amount (column N) is deliberately
    left blank: no monetary behaviour is under test here, and seeding binary
    floats into money-shaped fixtures invites the exact habit the house rule
    forbids (money is Decimal, never float).
    """
    row = [""] * 26
    if public_id is not None:
        row[RECONCILIATION_KEY_COL_INDEX] = public_id
    return row


def _payload(values, **overrides):
    payload = {
        "drive_id": "drive-1",
        "item_id": "item-1",
        "worksheet_name": "DETAILS",
        "row_index": 937,
        "values": values,
        "session_id": None,
    }
    payload.update(overrides)
    return payload


def _row(kind="insert_excel_row"):
    return MsOutbox(
        id=7505,
        public_id="outbox-7505",
        row_version="rv-1",
        kind=kind,
        entity_type="Bill",
        entity_public_id="bill-1",
        tenant_id="tenant-1",
        request_id="req-1",
        payload=json.dumps({}),
        status="in_progress",
        attempts=0,
        ready_after=None,
        correlation_id=None,
    )


def _used_range(rows, status_code=200):
    return {"status_code": status_code, "range": {"values": rows}, "message": "ok"}


# --------------------------------------------------------------------------- #
# insert_excel_row
# --------------------------------------------------------------------------- #


def test_insert_skips_row_already_in_worksheet():
    """The duplicate-row bug itself: a key already in column Z must not re-insert."""
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(PID_PRESENT)])

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.insert_excel_rows"
    ) as insert:
        worker._handle_insert_excel_row(
            _row(), _payload([_sheet_row(PID_PRESENT)])
        )

    insert.assert_not_called()


def test_insert_writes_only_the_rows_not_already_present():
    """A mixed batch inserts the new row and drops the present one."""
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(PID_PRESENT)])
    batch = [_sheet_row(PID_PRESENT), _sheet_row(PID_NEW)]

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
    ) as insert:
        worker._handle_insert_excel_row(_row(), _payload(batch))

    insert.assert_called_once()
    written = insert.call_args.kwargs["values"]
    assert len(written) == 1
    assert written[0][RECONCILIATION_KEY_COL_INDEX] == PID_NEW
    # row_index is the SubCostCode insertion point and must survive filtering.
    assert insert.call_args.kwargs["row_index"] == 937


def test_insert_key_match_is_case_and_whitespace_insensitive():
    """Excel round-trips can re-case a UUID; a re-cased key is still a duplicate."""
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(f"  {PID_PRESENT.lower()}  ")])

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.insert_excel_rows"
    ) as insert:
        worker._handle_insert_excel_row(
            _row(), _payload([_sheet_row(PID_PRESENT.upper())])
        )

    insert.assert_not_called()


def test_insert_proceeds_when_key_absent_from_sheet():
    """The happy path still writes — the guard must not block genuine work."""
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(PID_PRESENT)])

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
    ) as insert:
        worker._handle_insert_excel_row(
            _row(), _payload([_sheet_row(PID_NEW)])
        )

    insert.assert_called_once()
    assert len(insert.call_args.kwargs["values"]) == 1


# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


def test_insert_fails_closed_when_used_range_read_errors():
    """A non-200 read must raise, never fall through to a blind insert."""
    worker = MsOutboxWorker()
    bad = {"status_code": 500, "range": None, "message": "boom"}

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=bad), patch(
        f"{_CLIENT}.insert_excel_rows"
    ) as insert:
        with pytest.raises(MsGraphError):
            worker._handle_insert_excel_row(
                _row(), _payload([_sheet_row(PID_NEW)])
            )

    insert.assert_not_called()


def test_insert_fails_closed_on_200_with_no_range():
    """
    A rangeless 200 is the subtle one: status_code alone reads as healthy, so
    without an explicit check the empty key set would let every row through as
    if the sheet were empty — re-creating the duplicate bug on a bad read.
    """
    worker = MsOutboxWorker()
    rangeless = {"status_code": 200, "range": None, "message": "ok"}

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=rangeless), patch(
        f"{_CLIENT}.insert_excel_rows"
    ) as insert:
        with pytest.raises(MsGraphError) as caught:
            worker._handle_insert_excel_row(
                _row(), _payload([_sheet_row(PID_NEW)])
            )

    insert.assert_not_called()
    # Must be RETRYABLE: _handle_ms_error dead-letters a non-retryable on
    # attempt 1, which would permanently strand a legitimate ledger row on one
    # transient bad read.
    assert caught.value.is_retryable is True


# --------------------------------------------------------------------------- #
# Keyless rows are kept, never dropped
# --------------------------------------------------------------------------- #


def test_keyless_row_is_still_written():
    """
    A row with no column-Z key can't be proven a duplicate. Keeping it risks a
    duplicate; dropping it loses real ledger data — which is strictly worse.
    """
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(PID_PRESENT)])

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
    ) as insert:
        worker._handle_insert_excel_row(
            _row(), _payload([_sheet_row(None)])
        )

    insert.assert_called_once()
    assert len(insert.call_args.kwargs["values"]) == 1


def test_short_sheet_rows_do_not_crash_the_guard():
    """Header/short rows (<26 cols) must be tolerated, not IndexError."""
    worker = MsOutboxWorker()
    sheet = _used_range([["Cost Code", "Vendor"], _sheet_row(PID_PRESENT)])

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.insert_excel_rows"
    ) as insert:
        worker._handle_insert_excel_row(
            _row(), _payload([_sheet_row(PID_PRESENT)])
        )

    insert.assert_not_called()


# --------------------------------------------------------------------------- #
# append_excel_row — the writer's fallback path, same exposure
# --------------------------------------------------------------------------- #


def test_append_skips_row_already_in_worksheet():
    """
    append is the writer's fallback when its own dedup read fails, so it carries
    the identical exposure and must carry the identical guard.
    """
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(PID_PRESENT)])
    payload = _payload([_sheet_row(PID_PRESENT)])
    payload.pop("row_index")

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.append_excel_rows"
    ) as append:
        worker._handle_append_excel_row(_row(kind="append_excel_row"), payload)

    append.assert_not_called()


def test_append_writes_the_new_row():
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(PID_PRESENT)])
    payload = _payload([_sheet_row(PID_NEW)])
    payload.pop("row_index")

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.append_excel_rows", return_value={"status_code": 200}
    ) as append:
        worker._handle_append_excel_row(_row(kind="append_excel_row"), payload)

    append.assert_called_once()
    assert append.call_args.kwargs["values"][0][RECONCILIATION_KEY_COL_INDEX] == PID_NEW


# --------------------------------------------------------------------------- #
# Intra-batch duplicates (Codex P1, round 1)
# --------------------------------------------------------------------------- #


def test_insert_dedups_repeated_key_within_one_batch():
    """
    The sheet-only seen-set was not enough: a payload repeating the same key
    wrote the line twice, because retained keys never joined the set. Box's
    `apply_rows_to_details` adds as it applies; this pins MS to that parity.
    """
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(PID_PRESENT)])
    batch = [_sheet_row(PID_NEW), _sheet_row(PID_NEW)]

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
    ) as insert:
        worker._handle_insert_excel_row(_row(), _payload(batch))

    insert.assert_called_once()
    written = insert.call_args.kwargs["values"]
    assert len(written) == 1, "a repeated key in one batch must write exactly once"
    assert written[0][RECONCILIATION_KEY_COL_INDEX] == PID_NEW


def test_append_dedups_repeated_key_within_one_batch():
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(PID_PRESENT)])
    payload = _payload([_sheet_row(PID_NEW), _sheet_row(PID_NEW)])
    payload.pop("row_index")

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.append_excel_rows", return_value={"status_code": 200}
    ) as append:
        worker._handle_append_excel_row(_row(kind="append_excel_row"), payload)

    append.assert_called_once()
    assert len(append.call_args.kwargs["values"]) == 1


def test_keyless_rows_are_not_collapsed_together():
    """
    Dedup keys on column Z only — two DISTINCT keyless rows are two real ledger
    lines and must both survive. The seen-set must never swallow them.
    """
    worker = MsOutboxWorker()
    sheet = _used_range([_sheet_row(PID_PRESENT)])
    batch = [_sheet_row(None), _sheet_row(None)]

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=sheet), patch(
        f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
    ) as insert:
        worker._handle_insert_excel_row(_row(), _payload(batch))

    insert.assert_called_once()
    assert len(insert.call_args.kwargs["values"]) == 2


# --------------------------------------------------------------------------- #
# The retryable classification actually routes to retry, not dead-letter
# (Codex P2, round 1)
# --------------------------------------------------------------------------- #


def test_rangeless_read_error_retries_rather_than_dead_lettering():
    """
    Asserting `is_retryable` is only half the claim — this pins the routing:
    _handle_ms_error must mark_failed (retry) and NOT dead-letter on attempt 1.
    """
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)

    worker._handle_ms_error(
        _row(),
        MsServerError("used-range read returned no range; refusing to write blind"),
    )

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()
