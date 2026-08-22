"""
Regression tests for BillLineItemConnector.sync_from_qbo_bill_line's create_mapping()
exception handling (U-228 Pass-1 hunt).

dbo.BillLineItem carries no uniqueness constraint of any kind (unlike dbo.Bill, which is
protected by UQ_Bill_VendorId_BillNumber_BillDate), so a concurrent-pull race that loses the
qbo.BillLineItemBillLine mapping insert must propagate — not be silently swallowed — so the
caller's per-line loop in BillBillConnector._sync_line_items can turn it into a RuntimeError
that either triggers rollback_orphan_header (new-bill CREATE path) or holds the watermark
(existing-bill UPDATE path). Only a plain ValueError (the pre-check "already mapped" case) is
the sanctioned swallow-and-continue outcome.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from shared.database import DatabaseConstraintError, map_database_error
from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector


def _unique_violation() -> DatabaseConstraintError:
    """A realistic race-loser error, built the same way production code produces one."""
    raw = (
        "('23000', \"[23000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"
        "Violation of UNIQUE KEY constraint 'UQ_BillLineItemBillLine_QboBillLineId'. "
        "Cannot insert duplicate key in object 'qbo.BillLineItemBillLine'. (2627)\")"
    )
    error = map_database_error(Exception(raw))
    assert isinstance(error, DatabaseConstraintError), f"fixture drifted: got {type(error)}"
    return error


def _make_qbo_bill_line(*, line_id=1, qbo_line_id="QBO-LINE-1", amount=Decimal("108.82")):
    return SimpleNamespace(
        id=line_id,
        qbo_line_id=qbo_line_id,
        description="Service Charge",
        amount=amount,
        qty=None,
        unit_price=None,
        markup_percent=None,
        billable_status="NotBillable",
        item_ref_value=None,
        customer_ref_value=None,
    )


def _build_connector():
    mapping_repo = Mock()
    mapping_repo.read_by_qbo_bill_line_id.return_value = None
    # create_mapping()'s own pre-checks — no existing mapping either side.
    mapping_repo.read_by_bill_line_item_id.return_value = None
    mapping_repo.read_by_qbo_bill_line_id.return_value = None

    bill_service = Mock()
    bill_service.read_by_id.return_value = SimpleNamespace(id=100, public_id="bill-pub-1")

    bill_line_item_service = Mock()
    # U-293: the fast path is tried FIRST — these tests exercise the legacy
    # 2-hop/create path, so the dbo-native direct lookup must explicitly miss
    # (an unstubbed Mock() would return a truthy sentinel and divert every
    # test below into the fast path instead of the legacy path they test).
    bill_line_item_service.read_by_qbo_identity.return_value = None
    bill_line_item_service.read_by_bill_id.return_value = []  # no unmapped lines (Shape B miss)
    bill_line_item_service.create.return_value = SimpleNamespace(id=200, public_id="bli-pub-1")
    bill_line_item_service.repo = Mock()

    connector = BillLineItemConnector(
        mapping_repo=mapping_repo,
        bill_line_item_service=bill_line_item_service,
        bill_service=bill_service,
        bill_bill_repo=Mock(),
        qbo_item_repo=Mock(),
        qbo_bill_line_repo=Mock(),
        item_sub_cost_code_repo=Mock(),
        qbo_customer_repo=Mock(),
        customer_project_repo=Mock(),
        project_service=Mock(),
    )
    return connector, mapping_repo


def test_sync_from_qbo_bill_line_value_error_from_create_mapping_is_swallowed():
    """The sanctioned case: a plain ValueError (pre-check 'already mapped') logs and returns."""
    connector, mapping_repo = _build_connector()
    mapping_repo.create.side_effect = ValueError("QboBillLine 1 is already mapped to BillLineItem 999")

    result = connector.sync_from_qbo_bill_line(100, _make_qbo_bill_line())

    assert result.id == 200  # returns the (now-unmapped) line_item; does not raise


def test_sync_from_qbo_bill_line_database_constraint_error_from_create_mapping_propagates():
    """
    Regression for U-228 fix-round: a concurrent-pull race that loses the mapping INSERT must
    raise, not be swallowed — dbo.BillLineItem has no unique constraint to fall back on, so a
    swallowed race here would leave a permanent, undetectable duplicate line item.
    """
    connector, mapping_repo = _build_connector()
    mapping_repo.create.side_effect = _unique_violation()

    with pytest.raises(DatabaseConstraintError):
        connector.sync_from_qbo_bill_line(100, _make_qbo_bill_line())


def test_sync_from_qbo_bill_line_create_path_dual_writes_identity():
    """U-238b: create branch stamps dbo QboId with the real QboLineId string, not staging surrogate id."""
    connector, mapping_repo = _build_connector()
    mapping_repo.create.return_value = SimpleNamespace(id=1)

    connector.sync_from_qbo_bill_line(
        100,
        _make_qbo_bill_line(line_id=1, qbo_line_id="QBO-LINE-REAL"),
        realm_id="realm-create",
    )

    connector.bill_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=200,
        qbo_id="QBO-LINE-REAL",
        realm_id="realm-create",
    )


def test_sync_from_qbo_bill_line_identity_stamp_failure_does_not_propagate():
    """
    U-238b fix-round 2 regression: a set_qbo_identity failure on the CREATE path must not
    propagate out of sync_from_qbo_bill_line — create_mapping already committed by that point,
    and an uncaught DatabaseError here (set_qbo_identity never raises ValueError) would reach
    BillBillConnector._sync_line_items' per-line RuntimeError aggregation, which on the
    NEW-BILL path triggers rollback_orphan_header and deletes the just-created, already-mapped
    Bill + BillLineItem over a purely cosmetic identity-cache write failure.
    """
    connector, mapping_repo = _build_connector()
    mapping_repo.create.return_value = SimpleNamespace(id=1)
    connector.bill_line_item_service.repo.set_qbo_identity.side_effect = _unique_violation()

    result = connector.sync_from_qbo_bill_line(
        100,
        _make_qbo_bill_line(line_id=1, qbo_line_id="QBO-LINE-REAL"),
        realm_id="realm-create",
    )

    assert result.id == 200  # returns normally; stamp failure is swallowed-and-warned, not raised


def test_sync_from_qbo_bill_line_update_path_dual_writes_identity():
    """U-238b: update branch re-stamps dbo identity on every pull."""
    connector, mapping_repo = _build_connector()
    mapping = SimpleNamespace(id=10, bill_line_item_id=200)
    line_item = SimpleNamespace(id=200, public_id="bli-pub-1", row_version="rv")
    mapping_repo.read_by_qbo_bill_line_id.return_value = mapping
    connector.bill_line_item_service.read_by_id.return_value = line_item
    connector.bill_line_item_service.update_by_public_id.return_value = line_item

    connector.sync_from_qbo_bill_line(
        100,
        _make_qbo_bill_line(line_id=1, qbo_line_id="QBO-LINE-UPD"),
        realm_id="realm-update",
    )

    connector.bill_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=200,
        qbo_id="QBO-LINE-UPD",
        realm_id="realm-update",
    )


# ---------------------------------------------------------------------------
# U-293-dw: write-side dual-write gap (QboId stamped, RealmId left NULL)
# ---------------------------------------------------------------------------
#
# Reproduces the live-prod shape found at U-293's Gate-2 (dbo.BillLineItem
# Ids 24621/24668: QboId stamped, RealmId NULL) — a caller of
# sync_from_qbo_bill_line that has qbo_line_id but not realm_id in hand. The
# fix (stamp_line_identity_or_warn's atomic-pair guard) means such a call must
# skip the stamp entirely rather than partial-stamp; a caller that already has
# realm_id on the existing row must still succeed via the update path's own
# fallback.


def test_sync_from_qbo_bill_line_create_path_skips_stamp_when_realm_id_missing():
    """Reproduces BillLineItem 24668: CREATE path called with no realm_id (the
    connector-level default). Before the fix this stamped QboId with RealmId
    left NULL; after the fix it must not stamp at all — the row stays a
    pending_backfill candidate rather than landing in the half-identified
    state found live in prod."""
    connector, mapping_repo = _build_connector()
    mapping_repo.create.return_value = SimpleNamespace(id=1)

    result = connector.sync_from_qbo_bill_line(
        100, _make_qbo_bill_line(line_id=1, qbo_line_id="QBO-LINE-NOREALM")
    )

    assert result.id == 200  # create still succeeds — only the identity stamp is skipped
    connector.bill_line_item_service.repo.set_qbo_identity.assert_not_called()


def test_sync_from_qbo_bill_line_update_path_skips_stamp_when_realm_unknown_both_ways():
    """Reproduces BillLineItem 24621's shape at the UPDATE/self-heal path: this
    call has no realm_id AND the existing row has never been stamped with one
    either (dbo.BillLineItem.RealmId IS NULL) — the atomic-pair guard must
    still block the stamp, not just on CREATE."""
    connector, mapping_repo = _build_connector()
    mapping = SimpleNamespace(id=10, bill_line_item_id=200)
    line_item = SimpleNamespace(id=200, public_id="bli-pub-1", row_version="rv", realm_id=None)
    mapping_repo.read_by_qbo_bill_line_id.return_value = mapping
    connector.bill_line_item_service.read_by_id.return_value = line_item
    connector.bill_line_item_service.update_by_public_id.return_value = line_item

    connector.sync_from_qbo_bill_line(
        100, _make_qbo_bill_line(line_id=1, qbo_line_id="QBO-LINE-NOREALM")
    )

    connector.bill_line_item_service.repo.set_qbo_identity.assert_not_called()


def test_sync_from_qbo_bill_line_update_path_falls_back_to_existing_realm_id():
    """A line that's already realm-complete must still self-heal its QboId on
    every touch (e.g. QBO recycled the line id) even when THIS call's
    realm_id is empty — the update path threads the row's own already-stamped
    realm_id through as a fallback so the new atomic-pair guard doesn't wrongly
    block a legitimate re-stamp on an already-good row."""
    connector, mapping_repo = _build_connector()
    mapping = SimpleNamespace(id=10, bill_line_item_id=200)
    line_item = SimpleNamespace(
        id=200, public_id="bli-pub-1", row_version="rv", realm_id="realm-existing"
    )
    mapping_repo.read_by_qbo_bill_line_id.return_value = mapping
    connector.bill_line_item_service.read_by_id.return_value = line_item
    connector.bill_line_item_service.update_by_public_id.return_value = line_item

    connector.sync_from_qbo_bill_line(
        100, _make_qbo_bill_line(line_id=1, qbo_line_id="QBO-LINE-RECYCLED")
    )

    connector.bill_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=200,
        qbo_id="QBO-LINE-RECYCLED",
        realm_id="realm-existing",
    )
