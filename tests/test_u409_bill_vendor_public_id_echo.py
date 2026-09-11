"""U-409 — bill reads echo `vendor_public_id`.

`BillUpdate.vendor_public_id` is a REQUIRED field, but every bill read returned
only the integer `vendor_id`, so iOS (which has no Vendor surface of its own and
may not hold the VENDORS module grant) could not construct a legal update body.
The server now echoes the identity it already knows.

This is money-routing, not cosmetics: `BillService.update` resolves
`vendor_public_id -> vendor_id` and overwrites, so a WRONG echo re-points the
bill to the WRONG vendor on the next PUT. Hence the emphasis below on each bill
getting its OWN vendor's id, and on every failure mode degrading to `null`
rather than to some other vendor.
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from entities.bill.api.router import (
    create_bill_router,
    get_bill_by_id_router,
    get_bill_by_public_id_router,
    get_bills_router,
    update_bill_by_public_id_router,
)
from entities.bill.business.model import Bill
from entities.vendor.persistence.repo import VendorRepository

USER = {"id": 1, "username": "tester"}

# `status` is a Bill COLUMN since U-445, so it appears in to_dict() — but the
# lifecycle stamp also owns it on the way out (stored value when present, the
# U-443 derivation otherwise). It is therefore not "pre-existing data this
# endpoint must echo unchanged", and is asserted in
# tests/test_bill_lifecycle_attach.py instead.
_LIFECYCLE_OWNED = {"status"}

# Distinct, non-substring UUIDs so a swapped/constant echo can't accidentally pass.
PID_10 = "aaaaaaaa-0000-0000-0000-00000000000a"
PID_20 = "bbbbbbbb-0000-0000-0000-00000000000b"
PID_30 = "cccccccc-0000-0000-0000-00000000000c"
PID_99 = "dddddddd-0000-0000-0000-00000000000d"


def _vendor_rows():
    """`ReadVendors` result set. Vendor 99 is not on any bill under test — it
    proves the seam filters to the requested ids instead of returning the table.
    Vendor 44 is deliberately ABSENT: `ReadVendors` filters `IsDeleted = 0`, so
    a soft-deleted vendor looks exactly like a missing one."""
    return [
        SimpleNamespace(Id=10, PublicId=PID_10),
        SimpleNamespace(Id=20, PublicId=PID_20),
        SimpleNamespace(Id=30, PublicId=PID_30),
        SimpleNamespace(Id=99, PublicId=PID_99),
    ]


def _mock_single_conn(row):
    """A connection double for the indexed single-vendor read (fetchone)."""
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


def _mock_conn():
    """A connection double whose cursor replays the vendor rows from fetchall()."""
    cursor = MagicMock()
    cursor.fetchall.return_value = _vendor_rows()
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


def _bill(id, vendor_id, public_id=None):
    return Bill(
        id=id,
        public_id=public_id or f"bill-{id}",
        row_version="AAAAAAAAB9E=",
        created_datetime="2026-09-01 10:00:00",
        modified_datetime="2026-09-01 10:00:00",
        vendor_id=vendor_id,
        payment_term_id=7,
        bill_date="2026-09-01",
        due_date="2026-09-01",
        bill_number=f"INV-{id}",
        total_amount=Decimal("100.00"),
        memo=None,
        is_draft=True,
    )


# --------------------------------------------------------------------------
# The seam
# --------------------------------------------------------------------------

def test_seam_maps_each_requested_id_to_its_own_public_id():
    conn = _mock_conn()
    result = VendorRepository().read_public_ids_by_ids([10, 20, 30], conn=conn)
    assert result == {10: PID_10, 20: PID_20, 30: PID_30}


def test_seam_omits_unrequested_vendors():
    """Vendor 99 is in the table but not asked for — it must not leak into the map."""
    conn = _mock_conn()
    assert 99 not in VendorRepository().read_public_ids_by_ids([10], conn=conn)


def test_seam_omits_missing_or_deleted_vendor_rather_than_guessing():
    """44 has no row (missing, or soft-deleted — ReadVendors filters IsDeleted=0).
    It must be ABSENT, so callers `.get()` it to None. Substituting any other
    vendor here would re-point the bill on the next PUT."""
    conn = _mock_conn()
    result = VendorRepository().read_public_ids_by_ids([10, 44], conn=conn)
    assert result.get(44) is None
    assert result == {10: PID_10}


def test_seam_uses_the_caller_supplied_connection():
    """Shares the router's connection (the `_conn_ctx(conn)` precedent) instead
    of opening a second one mid-request."""
    conn = _mock_conn()
    with patch("shared.database.get_connection") as fresh:
        VendorRepository().read_public_ids_by_ids([10], conn=conn)
        fresh.assert_not_called()
    conn.cursor.assert_called_once()


def test_seam_calls_the_existing_readvendors_sproc():
    """No new sproc: U-409 explicitly rides the sproc that already serves the
    web vendor picker."""
    conn = _mock_conn()
    with patch("entities.vendor.persistence.repo.call_procedure") as call_proc:
        VendorRepository().read_public_ids_by_ids([10], conn=conn)
    assert call_proc.call_args.kwargs["name"] == "ReadVendors"


@pytest.mark.parametrize("ids", [[], [None], [None, None]])
def test_seam_short_circuits_without_touching_the_db(ids):
    conn = _mock_conn()
    with patch("shared.database.get_connection") as fresh:
        assert VendorRepository().read_public_ids_by_ids(ids, conn=conn) == {}
        fresh.assert_not_called()
    conn.cursor.assert_not_called()


def test_seam_degrades_to_empty_on_uncoercible_id():
    """Regression (review finding, P3): the `int()` coercion must sit inside the
    try, so the documented fail-closed contract holds for every input — a bad id
    degrades this enrichment to `null` instead of 500-ing the bill read."""
    conn = _mock_conn()
    assert VendorRepository().read_public_ids_by_ids(["not-an-id"], conn=conn) == {}


def test_seam_degrades_to_empty_on_db_error():
    """Fail-closed: an enrichment failure must yield `null`, never a stale or
    substituted id, and must not break the bill read itself."""
    conn = MagicMock()
    conn.cursor.side_effect = RuntimeError("connection reset")
    assert VendorRepository().read_public_ids_by_ids([10], conn=conn) == {}


# --------------------------------------------------------------------------
# GET /get/bills
# --------------------------------------------------------------------------

def _lifecycle_patches(review=None):
    """Patch the review lookup the bill reads consult (U-443).

    Since U-444 that is the ONLY lifecycle collaborator — `submitted` keys on a
    flag carried by the Review row, so there is no per-request boundary lookup
    left to patch. Without this the lookup would attempt a real pyodbc connect,
    which conftest blocks, and the router now (correctly) lets that raise.
    """
    review_repo = MagicMock()
    review_repo.read_current_by_bill_id.return_value = review
    return [
        patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo),
    ]


def _call_get_bills(bills, vendor_repo=None, conn=None):
    """Drive GET /get/bills with the router's collaborators mocked out.

    By default the REAL VendorRepository runs against `conn`'s fake rows, so
    these tests exercise the seam and the wiring together. Pass `vendor_repo` to
    substitute a double and assert on how the router calls it instead.
    """
    conn = conn or _mock_conn()
    service = MagicMock()
    # U-447: one call returns (page, total) — the total no longer comes from a
    # second sproc, which is what let it describe a different snapshot.
    service.read_paginated.return_value = (bills, len(bills))
    repo = MagicMock()
    repo.read_first_line_item_projects.return_value = {}
    review_repo = MagicMock()
    review_repo.read_current_by_bill_ids.return_value = {}

    patches = [
        patch("entities.bill.api.router.get_connection", return_value=conn),
        patch("entities.bill.api.router.BillService", return_value=service),
        patch("entities.bill.api.router.BillRepository", return_value=repo),
        patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo),
    ]
    if vendor_repo is not None:
        patches.append(patch("entities.bill.api.router.VendorRepository", return_value=vendor_repo))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return asyncio.run(get_bills_router(
            page=1, page_size=50, search=None, vendor_id=None,
            is_draft=None, current_user=USER,
        ))


def test_list_maps_each_bill_to_its_own_vendors_public_id():
    """(a) The core anti-mutation assertion: three bills on three different
    vendors must each carry THEIR OWN id — not a constant, not a neighbour's."""
    response = _call_get_bills([_bill(1, 10), _bill(2, 20), _bill(3, 30)])
    echoed = {bd["id"]: bd["vendor_public_id"] for bd in response["data"]}
    assert echoed == {1: PID_10, 2: PID_20, 3: PID_30}


def test_list_echo_is_consistent_with_each_rows_own_vendor_id():
    """Same guard stated as an invariant rather than a fixture — a swap that
    happened to be symmetric would still be caught here."""
    expected = {10: PID_10, 20: PID_20, 30: PID_30}
    response = _call_get_bills([_bill(1, 30), _bill(2, 10), _bill(3, 20)])
    for bd in response["data"]:
        assert bd["vendor_public_id"] == expected[bd["vendor_id"]]


def test_list_emits_null_for_missing_deleted_or_unset_vendor():
    """(b) 44 is absent from ReadVendors (missing/soft-deleted); bill 3 has no
    vendor at all. Both must be null while their siblings still resolve."""
    response = _call_get_bills([_bill(1, 10), _bill(2, 44), _bill(3, None)])
    echoed = {bd["id"]: bd["vendor_public_id"] for bd in response["data"]}
    assert echoed == {1: PID_10, 2: None, 3: None}


def test_list_resolves_vendors_on_the_shared_request_connection():
    """One batch call on the connection the route already holds — not N+1, and
    not a second connection."""
    conn = _mock_conn()
    vendor_repo = MagicMock()
    vendor_repo.read_public_ids_by_ids.return_value = {}
    _call_get_bills([_bill(1, 10), _bill(2, 20)], vendor_repo=vendor_repo, conn=conn)

    vendor_repo.read_public_ids_by_ids.assert_called_once()
    args, kwargs = vendor_repo.read_public_ids_by_ids.call_args
    assert sorted(args[0]) == [10, 20]
    assert kwargs["conn"] is conn


def test_list_preserves_every_pre_existing_field():
    """(d) Additive only — web's BillList reads this same payload."""
    bill = _bill(1, 10)
    original = bill.to_dict()
    response = _call_get_bills([bill])
    bd = response["data"][0]
    for key, value in original.items():
        if key in _LIFECYCLE_OWNED:
            continue
        assert bd[key] == value, f"pre-existing field {key!r} changed"
    assert set(bd) - set(original) == {
        "project_id", "review_status", "review_status_is_final",
        "review_status_is_declined", "vendor_public_id",
        # `review_status_kind` is still derived from the Review row. `status`
        # is NOT in this set any more: U-443 added it as a derived field with
        # no column behind it, and U-445 gave it a real `Bill.Status` column,
        # so it now arrives through `to_dict()` like any other column.
        "review_status_kind",
    }
    assert set(response) == {"data", "count", "page", "page_size"}


# --------------------------------------------------------------------------
# GET /get/bill/{public_id}  and  GET /get/bill/id/{id}
# --------------------------------------------------------------------------

def _single_vendor_conn(bill):
    """ReadVendorById result for this bill's vendor: the row, or None when the
    vendor is missing/soft-deleted (the sproc filters IsDeleted = 0)."""
    row = next((r for r in _vendor_rows() if r.Id == bill.vendor_id), None)
    return _mock_single_conn(row)


def _call_get_by_public_id(bill):
    service = MagicMock()
    service.read_by_public_id.return_value = bill
    service.get_qbo_bill_url.return_value = None
    with ExitStack() as stack:
        for p in (
            patch("entities.bill.api.router.BillService", return_value=service),
            patch("shared.database.get_connection",
                  return_value=_single_vendor_conn(bill)),
            *_lifecycle_patches(),
        ):
            stack.enter_context(p)
        return asyncio.run(get_bill_by_public_id_router(
            public_id=bill.public_id, current_user=USER))


def _call_get_by_id(bill):
    service = MagicMock()
    service.read_by_id.return_value = bill
    with ExitStack() as stack:
        for p in (
            patch("entities.bill.api.router.BillService", return_value=service),
            patch("shared.database.get_connection",
                  return_value=_single_vendor_conn(bill)),
            *_lifecycle_patches(),
        ):
            stack.enter_context(p)
        return get_bill_by_id_router(id=bill.id, current_user=USER)


@pytest.mark.parametrize("call", [_call_get_by_public_id, _call_get_by_id])
def test_single_reads_carry_the_vendor_public_id(call):
    """(c) Both single-bill reads — /get/bill/id/{id} is the one iOS calls
    before a PUT."""
    assert call(_bill(2, 20))["data"]["vendor_public_id"] == PID_20


@pytest.mark.parametrize("call", [_call_get_by_public_id, _call_get_by_id])
def test_single_reads_echo_the_bills_own_vendor(call):
    """A constant echo passes the test above; this one it cannot."""
    assert call(_bill(1, 10))["data"]["vendor_public_id"] == PID_10
    assert call(_bill(3, 30))["data"]["vendor_public_id"] == PID_30


@pytest.mark.parametrize("call", [_call_get_by_public_id, _call_get_by_id])
@pytest.mark.parametrize("vendor_id", [44, None])
def test_single_reads_emit_null_for_missing_deleted_or_unset_vendor(call, vendor_id):
    assert call(_bill(1, vendor_id))["data"]["vendor_public_id"] is None


@pytest.mark.parametrize("call", [_call_get_by_public_id, _call_get_by_id])
def test_single_reads_preserve_every_pre_existing_field(call):
    """(d) for the single reads — web's BillEdit/BillView read these."""
    bill = _bill(1, 10)
    original = bill.to_dict()
    payload = call(bill)["data"]
    for key, value in original.items():
        if key in _LIFECYCLE_OWNED:
            continue
        assert payload[key] == value, f"pre-existing field {key!r} changed"
    assert "vendor_public_id" in payload


def test_unset_vendor_skips_the_database_entirely():
    """No point reading the whole vendor table to resolve nothing."""
    bill = _bill(1, None)
    service = MagicMock()
    service.read_by_id.return_value = bill
    with ExitStack() as stack:
        for p in _lifecycle_patches():
            stack.enter_context(p)
        stack.enter_context(patch("entities.bill.api.router.BillService", return_value=service))
        fresh = stack.enter_context(patch("shared.database.get_connection"))
        result = get_bill_by_id_router(id=1, current_user=USER)
    fresh.assert_not_called()
    assert result["data"]["vendor_public_id"] is None


# --------------------------------------------------------------------------
# The N=1 seam — indexed sproc, same contract as the batch one
# --------------------------------------------------------------------------

def test_single_seam_uses_the_indexed_sproc_not_a_full_table_scan():
    """/get/bill/id/{id} is called before every iOS PUT — resolving one UUID
    must not scan the ~1.1k-row vendor catalogue."""
    conn = _mock_single_conn(SimpleNamespace(Id=20, PublicId=PID_20))
    with patch("shared.database.get_connection", return_value=conn), \
         patch("entities.vendor.persistence.repo.call_procedure") as call_proc:
        VendorRepository().read_public_id_by_id(20)
    assert call_proc.call_args.kwargs["name"] == "ReadVendorById"
    assert call_proc.call_args.kwargs["params"] == {"Id": 20}


def test_single_seam_returns_the_requested_vendors_public_id():
    conn = _mock_single_conn(SimpleNamespace(Id=30, PublicId=PID_30))
    with patch("shared.database.get_connection", return_value=conn):
        assert VendorRepository().read_public_id_by_id(30) == PID_30


def test_single_seam_returns_none_for_missing_or_deleted_vendor():
    """ReadVendorById filters IsDeleted = 0, so both look like no row."""
    with patch("shared.database.get_connection", return_value=_mock_single_conn(None)):
        assert VendorRepository().read_public_id_by_id(44) is None


@pytest.mark.parametrize("vendor_id", [0, None])
def test_single_seam_skips_the_db_for_an_unset_id(vendor_id):
    with patch("shared.database.get_connection") as fresh:
        assert VendorRepository().read_public_id_by_id(vendor_id) is None
        fresh.assert_not_called()


def test_single_seam_degrades_to_none_on_db_error():
    conn = MagicMock()
    conn.cursor.side_effect = RuntimeError("connection reset")
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    with patch("shared.database.get_connection", return_value=conn):
        assert VendorRepository().read_public_id_by_id(10) is None


# --------------------------------------------------------------------------
# Write responses — POST /create/bill and PUT /update/bill
# --------------------------------------------------------------------------
# iOS upserts these straight into Core Data, overwriting every field, then
# rebuilds its next queued PUT from that cached row. A write response missing
# vendor_public_id would nil the cached value and leave the next offline edit
# unable to send a REQUIRED field -- reintroducing the exact bug U-409 fixes.

def _call_write(router_fn, bill, **kwargs):
    engine = MagicMock()
    engine.execute_synchronous.return_value = {"success": True, "data": bill.to_dict()}
    body = MagicMock()
    body.line_rate = body.line_amount = body.line_markup = body.line_price = None
    body.total_amount = None
    with patch("entities.bill.api.router.ProcessEngine", return_value=engine), \
         patch("entities.bill.api.router.resolve_user_id", return_value=1), \
         patch("shared.database.get_connection",
               return_value=_single_vendor_conn(bill)):
        return asyncio.run(router_fn(body=body, current_user=USER, **kwargs))


def test_create_response_carries_the_vendor_public_id():
    result = _call_write(create_bill_router, _bill(1, 10))
    assert result["data"]["vendor_public_id"] == PID_10


def test_update_response_carries_the_vendor_public_id():
    result = _call_write(update_bill_by_public_id_router, _bill(2, 20), public_id="bill-2")
    assert result["data"]["vendor_public_id"] == PID_20


def test_write_responses_echo_the_persisted_vendor_not_a_constant():
    """A constant echo would pass the two tests above."""
    assert _call_write(create_bill_router, _bill(3, 30))["data"]["vendor_public_id"] == PID_30
    assert _call_write(
        update_bill_by_public_id_router, _bill(1, 10), public_id="bill-1"
    )["data"]["vendor_public_id"] == PID_10


def test_write_responses_emit_null_for_missing_or_deleted_vendor():
    result = _call_write(update_bill_by_public_id_router, _bill(1, 44), public_id="bill-1")
    assert result["data"]["vendor_public_id"] is None


def test_write_enrichment_tolerates_a_non_dict_payload():
    """The engine's serializer can return None or a list; enrichment must not
    turn a successful write into a 500."""
    from entities.bill.api.router import _with_vendor_public_id
    assert _with_vendor_public_id(None) is None
    assert _with_vendor_public_id([1, 2]) == [1, 2]
    assert _with_vendor_public_id({"no_vendor_key": 1}) == {"no_vendor_key": 1}


def test_write_responses_preserve_every_pre_existing_field():
    bill = _bill(1, 10)
    original = bill.to_dict()
    payload = _call_write(create_bill_router, bill)["data"]
    for key, value in original.items():
        assert payload[key] == value, f"pre-existing field {key!r} changed"
