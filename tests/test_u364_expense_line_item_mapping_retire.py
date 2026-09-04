"""
U-364 — retire qbo.PurchaseLineExpenseLineItem (U-349 program family 11/11,
LAST family) and repoint PurchaseLineExpenseLineItemConnector.
sync_from_qbo_purchase_line onto the shared dbo-only line primitive
(base/identity_fastpath.py::run_line_identity_fastpath_dbo_only).

A CLONE of U-363's shape (tests/test_u363_bill_line_item_mapping_retire.py) —
see that file's own docstring for the fuller design rationale, which applies
here unchanged. Unlike bill_line_item, this connector keeps two
expense-specific field-decision helpers (default_amount_only_line /
preserve_stored_value, for Ramp amount-only card-spend lines on 58999) —
unaffected by the identity repoint, still pure-logic-tested in
tests/test_qbo_purchase_line_defaults.py.

Covers:
  * HIT: update in place (fields routed through preserve_stored_value as
    before), no identity re-stamp (except the one-off realm self-heal for a
    legacy QboId-without-RealmId row), ROWVERSION race -> RuntimeError.
  * MISS: create, then the BARE `set_qbo_identity` stamp + re-read. A missing
    realm refuses BEFORE creating; a stamp failure rolls the fresh line back
    and re-raises; a rollback that itself fails records an `orphan_eli_line_
    item` ReconciliationIssue. A readopt (stale-identity orphan, content-
    fingerprint match on the DEFAULTED qty/rate) is tried before ever
    creating; a non-matching orphan is never adopted (money bug #1 guard).
  * `_sync_line_items` (header connector): computes `live_qbo_line_ids` once
    per expense and threads it through to every line.
  * The 3 executed consumers this unit's deploy-gap bridge protects: the
    entity delete path, `_upsert_purchase_lines`' stale QboPurchaseLine
    cleanup, and `_reconcile_deleted_purchases`' Step 1.
  * The entity concurrent-delete race fix:
    ExpenseLineItemService.update_by_public_id returns a clean None on a
    concurrent delete instead of falling through to an AttributeError.
  * The dbo-native reconciliation re-expression: PURCHASE_BILLABLE_STATUS_
    DRIFT_ROWS_SQL returns the SAME rows as the retired mapping-hop query on
    a shared SQLite fixture (characterization / equivalence), and never
    cross-matches a QBO line id reused across different parent Purchases
    (money bug #3 guard).
  * Regression: the 4 sprocs (Read-ById/-ByPublicId/-sByExpenseId/Update) now
    project QboId/RealmId — none of the four did before this unit.

Supersedes tests/test_u293b_expense_line_item_qbo_identity_repoint.py (the
with-mapping U-293b wiring, deleted in this unit).
"""
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from entities.expense_line_item.business.service import ExpenseLineItemService
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
    PurchaseLineExpenseLineItemConnector,
)
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from integrations.intuit.qbo.reconciliation.business.service import (
    PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL,
    ReconciliationService,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_u304_rollback_lock import _recording_lock_factory

LOCK_PATCH_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
LINE_SERVICE = "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"

# The MISS branch runs under run_line_identity_fastpath_dbo_only's create lock —
# grant it for every test in this pure-logic module (tests that need to OBSERVE
# lock traffic patch a tracking lock over this grant explicitly).
pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


def _make_qbo_purchase_line(**overrides):
    defaults = dict(
        id=42,
        qbo_purchase_id=4,
        qbo_line_id="1",
        description="Lumber",
        amount=Decimal("50"),
        qty=Decimal("1"),
        unit_price=Decimal("50"),
        markup_percent=None,
        billable_status=None,
        customer_ref_value=None,
        item_ref_value=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector():
    connector = PurchaseLineExpenseLineItemConnector()
    line_svc = Mock()
    # U-361b-shape readopt step (run before every create) reads this — default
    # to "nothing to adopt" so tests unrelated to readopt exercise a clean
    # MISS/HIT without also needing to stub it themselves.
    line_svc.read_by_expense_id.return_value = []
    reconciliation_repo = Mock()
    connector.expense_line_item_service = line_svc
    connector.reconciliation_repo = reconciliation_repo
    connector._get_sub_cost_code_id = Mock(return_value=None)
    connector._get_project_public_id = Mock(return_value=None)
    return connector, line_svc, reconciliation_repo


def _stamped_row(line_id, qbo_line_id, realm_id="realm-1", *, quantity=None, rate=None, markup=None,
                  description="Lumber", amount=Decimal("50")):
    return SimpleNamespace(
        id=line_id, public_id=f"pub-{line_id}", row_version=f"rv-{line_id}",
        qbo_id=qbo_line_id, realm_id=realm_id,
        quantity=quantity, rate=rate, markup=markup, description=description, amount=amount,
    )


# --- the connector no longer knows about a mapping table ----------------------


def test_connector_has_no_mapping_attributes():
    connector = PurchaseLineExpenseLineItemConnector()
    assert not hasattr(connector, "mapping_repo")
    assert not hasattr(connector, "create_mapping")
    assert not hasattr(connector, "_find_and_match_by_fingerprint")
    assert not hasattr(connector, "_fingerprint_tuple")
    assert not hasattr(connector, "get_mapping_by_expense_line_item_id")
    assert not hasattr(connector, "get_mapping_by_qbo_purchase_line_id")
    assert not hasattr(connector, "_record_line_identity_mapping_conflict_issue")


# --- HIT ----------------------------------------------------------------------


def test_hit_updates_in_place_and_does_not_restamp_a_realm_complete_row():
    connector, line_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    direct = _stamped_row(55, "1")
    line_svc.read_by_qbo_identity.return_value = direct
    updated = SimpleNamespace(id=55, public_id="pub-55")
    line_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is updated
    line_svc.read_by_qbo_identity.assert_called_once_with(10, "1")
    line_svc.update_by_public_id.assert_called_once()
    assert line_svc.update_by_public_id.call_args.args == ("pub-55",)
    kw = line_svc.update_by_public_id.call_args.kwargs
    assert kw["row_version"] == "rv-55"
    assert kw["is_draft"] is False
    assert kw["quantity"] == Decimal("1")
    assert kw["rate"] == Decimal("50")
    assert kw["markup"] == Decimal("0")
    line_svc.create.assert_not_called()
    line_svc.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_not_called()


def test_hit_heals_a_legacy_row_missing_its_realm_half():
    """U-293-dw's atomic-pair gap: a row found by QboId but stamped without a
    RealmId gets the realm written once (best-effort, enforce_realm_pairing)."""
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1", realm_id=None)
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    with patch(f"{LINE_SERVICE}.stamp_line_identity_or_warn") as mock_stamp:
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    mock_stamp.assert_called_once()
    assert mock_stamp.call_args.kwargs["id"] == 55
    assert mock_stamp.call_args.kwargs["qbo_id"] == "1"
    assert mock_stamp.call_args.kwargs["realm_id"] == "realm-1"
    assert mock_stamp.call_args.kwargs["enforce_realm_pairing"] is True


def test_hit_without_a_call_realm_does_not_try_to_heal():
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1", realm_id=None)
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    with patch(f"{LINE_SERVICE}.stamp_line_identity_or_warn") as mock_stamp:
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}))

    mock_stamp.assert_not_called()


def test_hit_update_returning_none_raises_runtime_error_and_never_stamps():
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1")
    line_svc.update_by_public_id.return_value = None

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.repo.set_qbo_identity.assert_not_called()
    line_svc.create.assert_not_called()


# --- MISS ---------------------------------------------------------------------


def test_miss_creates_then_bare_stamps_then_returns_the_reread():
    connector, line_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1", description="Materials", amount=Decimal("500"),
                                        qty=Decimal("1"), unit_price=Decimal("500"))
    line_svc.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.create.return_value = created
    reread = _stamped_row(77, "1")
    line_svc.read_by_id.return_value = reread

    result = connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is reread  # the re-read, not the stale in-memory candidate
    line_svc.create.assert_called_once()
    assert line_svc.create.call_args.kwargs["expense_public_id"] == "exp-pub"
    assert line_svc.create.call_args.kwargs["description"] == "Materials"
    assert line_svc.create.call_args.kwargs["amount"] == Decimal("500")
    assert line_svc.create.call_args.kwargs["quantity"] == Decimal("1")
    assert line_svc.create.call_args.kwargs["rate"] == Decimal("500")
    assert line_svc.create.call_args.kwargs["is_draft"] is False
    line_svc.repo.set_qbo_identity.assert_called_once_with(id=77, qbo_id="1", realm_id="realm-1")
    line_svc.read_by_id.assert_called_once_with(77)
    line_svc.update_by_public_id.assert_not_called()
    line_svc.delete_by_public_id.assert_not_called()
    reconciliation_repo.create.assert_not_called()


def test_miss_amount_only_line_defaults_quantity_and_rate_on_create():
    """The Ramp AccountBasedExpenseLineDetail case: no Qty/UnitPrice at all ->
    quantity=1, rate=amount, so quantity*rate == amount."""
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1", amount=Decimal("300"), qty=None, unit_price=None)
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.read_by_id.return_value = _stamped_row(77, "1")

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    kw = line_svc.create.call_args.kwargs
    assert kw["quantity"] == Decimal("1")
    assert kw["rate"] == Decimal("300")
    assert kw["markup"] == Decimal("0")
    assert kw["price"] == Decimal("300")


def test_miss_never_writes_a_mapping_but_does_check_for_a_readopt():
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.read_by_id.return_value = _stamped_row(77, "1")

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.read_by_expense_id.assert_called_once_with(expense_id=10)
    assert line_svc.read_by_qbo_identity.call_count == 2  # outer miss + re-read under lock


def test_miss_with_missing_realm_refuses_before_creating():
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None

    with pytest.raises(RuntimeError, match="realm_id is missing"):
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}))  # no realm_id

    line_svc.create.assert_not_called()
    line_svc.repo.set_qbo_identity.assert_not_called()
    line_svc.delete_by_public_id.assert_not_called()


def test_miss_stamp_failure_rolls_back_the_fresh_line_and_reraises():
    connector, line_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.repo.set_qbo_identity.side_effect = RuntimeError("stamp db error")

    with pytest.raises(RuntimeError, match="stamp db error"):
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.delete_by_public_id.assert_called_once_with("pub-77")
    reconciliation_repo.create.assert_not_called()  # rollback succeeded: nothing to record


def test_miss_stamp_that_did_not_land_rolls_back_and_reraises():
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.read_by_id.return_value = _stamped_row(77, None, realm_id=None)  # sproc declined

    with pytest.raises(RuntimeError, match="identity stamp did not land"):
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.delete_by_public_id.assert_called_once_with("pub-77")


def test_miss_rollback_failure_records_an_orphan_line_issue_and_reraises_the_original():
    connector, line_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.repo.set_qbo_identity.side_effect = RuntimeError("stamp db error")
    line_svc.delete_by_public_id.side_effect = RuntimeError("delete also failed")

    with pytest.raises(RuntimeError, match="stamp db error"):
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "orphan_eli_line_item"
    assert kwargs["entity_type"] == "ExpenseLineItem"
    assert kwargs["entity_public_id"] == "pub-77"
    assert kwargs["qbo_id"] == "1"
    assert kwargs["realm_id"] == "realm-1"
    assert "Expense 10" in kwargs["details"]
    assert "delete also failed" in kwargs["details"]


def test_miss_create_failure_propagates_with_nothing_to_roll_back():
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.side_effect = RuntimeError("create failed")

    with pytest.raises(RuntimeError, match="create failed"):
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.repo.set_qbo_identity.assert_not_called()
    line_svc.delete_by_public_id.assert_not_called()


def test_miss_racer_under_lock_is_updated_not_duplicated():
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    racer = _stamped_row(90, "1")
    line_svc.read_by_qbo_identity.side_effect = [None, racer]
    updated = SimpleNamespace(id=90, public_id="pub-90")
    line_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is updated
    line_svc.create.assert_not_called()
    line_svc.repo.set_qbo_identity.assert_not_called()


# --- readopt content-match ------------------------------------------------------


def test_readopt_refuses_to_adopt_a_stale_line_whose_fingerprint_does_not_match():
    """Money bug #1 guard: a stale-identity orphan whose content does NOT
    match the incoming QBO line must never be blindly rebound — the MISS
    falls through to a fresh create instead."""
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="2", description="Materials", amount=Decimal("500"),
                                        qty=Decimal("1"), unit_price=Decimal("500"))
    stale_orphan = _stamped_row(
        61, "9", quantity=Decimal("1"), rate=Decimal("999"), description="Different", amount=Decimal("999"),
    )
    line_svc.read_by_expense_id.return_value = [stale_orphan]
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.read_by_id.return_value = _stamped_row(77, "2")

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1", "2"}), realm_id="realm-1")

    line_svc.create.assert_called_once()  # readopt found nothing -> fresh create
    line_svc.update_by_public_id.assert_not_called()


def test_readopt_adopts_an_amount_only_stale_line_matching_defaulted_fingerprint():
    """An amount-only Ramp line's stale orphan carries the DEFAULTED
    (quantity=1, rate=amount) shape `create()` persisted — still matches
    under the single-tier primitive fingerprint."""
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(
        qbo_line_id="2", description="Ramp spend", amount=Decimal("300"), qty=None, unit_price=None,
    )
    stale_orphan = _stamped_row(
        61, "9", quantity=Decimal("1"), rate=Decimal("300"), description="Ramp spend", amount=Decimal("300"),
    )
    line_svc.read_by_expense_id.return_value = [stale_orphan]
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=61, public_id="pub-61")
    line_svc.read_by_id.return_value = _stamped_row(61, "2")

    result = connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1", "2"}), realm_id="realm-1")

    line_svc.create.assert_not_called()
    line_svc.update_by_public_id.assert_called_once()
    assert line_svc.update_by_public_id.call_args.args == ("pub-61",)
    line_svc.repo.set_qbo_identity.assert_called_once_with(id=61, qbo_id="2", realm_id="realm-1")
    assert result.id == 61


def test_readopt_adopts_a_legacy_pre_u098_stale_line_with_null_quantity_and_rate():
    """Codex Gate-2 round-2 P2: a stale orphan created BEFORE the U-098
    amount-only default existed still carries NULL quantity/rate (never
    healed in place because it went stale before its next successful
    re-sync). The single-tier fingerprint must normalize the CANDIDATE side
    through default_amount_only_line too, not just the target, or this
    legacy row is missed and a duplicate ExpenseLineItem is minted."""
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(
        qbo_line_id="2", description="Ramp spend", amount=Decimal("300"), qty=None, unit_price=None,
    )
    legacy_orphan = _stamped_row(
        61, "9", quantity=None, rate=None, description="Ramp spend", amount=Decimal("300"),
    )
    line_svc.read_by_expense_id.return_value = [legacy_orphan]
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=61, public_id="pub-61")
    line_svc.read_by_id.return_value = _stamped_row(61, "2")

    result = connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1", "2"}), realm_id="realm-1")

    line_svc.create.assert_not_called()  # readopted the legacy row, not a fresh duplicate
    line_svc.update_by_public_id.assert_called_once()
    assert line_svc.update_by_public_id.call_args.args == ("pub-61",)
    line_svc.repo.set_qbo_identity.assert_called_once_with(id=61, qbo_id="2", realm_id="realm-1")
    assert result.id == 61


# --- guards ---------------------------------------------------------------------


def test_missing_qbo_line_id_fails_closed_without_creating():
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id=None)

    with pytest.raises(ValueError, match="has no QBO Line.Id"):
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.read_by_qbo_identity.assert_not_called()
    line_svc.create.assert_not_called()


def test_staging_pk_is_not_part_of_identity():
    """qbo_line.id (the qbo.PurchaseLine staging PK) used to key the mapping;
    it is log-only now — a line with no staging id still resolves by Line.Id."""
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(id=None, qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1")
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.read_by_qbo_identity.assert_called_once_with(10, "1")


def test_create_lock_key_is_parent_and_line_scoped():
    connector, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.read_by_id.return_value = _stamped_row(77, "1")
    recorded, recording_lock = _recording_lock_factory()

    with patch(LOCK_PATCH_TARGET, side_effect=recording_lock):
        connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert recorded == ["qbo_dbo_line_identity_create:ExpenseLineItem:10:1"]


# --- entity service: update_by_public_id concurrent-delete contract -------------


def test_update_by_public_id_returns_none_on_concurrent_delete_not_attribute_error():
    """Regression: pre-fix, `existing.row_version = ...` and every field-set
    below it sat OUTSIDE any `if not existing: return None` guard — a `None`
    `existing` (row deleted between read and this call) fell through to
    `self.repo.update_by_id(existing)`, which immediately raises AttributeError
    on `existing.id` deep in the persistence layer, re-wrapped as an opaque
    DatabaseOperationError. The connector's own `_apply_line_fields` (this
    file's HIT-branch tests) documents and relies on a clean `None` return
    here to raise the well-classified `raise_concurrent_write_race` instead —
    that contract only holds if this method actually returns None. Also
    fixes the purchase connector's own concurrent-write-race handling
    (integrations/intuit/qbo/purchase/connector/expense_line_item/business/
    service.py's `_apply_line_fields`), which relies on exactly this clean
    None to raise its own well-classified error instead of 500ing."""
    repo = Mock()
    svc = ExpenseLineItemService(repo=repo)
    with patch.object(svc, "read_by_public_id", return_value=None):
        result = svc.update_by_public_id("eli-pub", row_version="rv", description="x")

    assert result is None
    repo.update_by_id.assert_not_called()


# --- header connector: live_qbo_line_ids computation + threading ----------------


def test_sync_line_items_computes_live_qbo_line_ids_once_and_threads_to_every_line():
    connector = PurchaseExpenseConnector(expense_service=Mock())
    line_connector = Mock()
    seen_calls = []
    line_connector.sync_from_qbo_purchase_line.side_effect = (
        lambda expense_id, expense_public_id, line, live_ids, realm_id=None:
        seen_calls.append((expense_id, line.qbo_line_id, live_ids, realm_id))
    )
    connector._line_connector = line_connector

    lines = [
        _make_qbo_purchase_line(qbo_line_id="1"),
        _make_qbo_purchase_line(qbo_line_id="2"),
        _make_qbo_purchase_line(qbo_line_id=None),  # excluded from the live set, still synced
    ]

    connector._sync_line_items(10, "exp-pub", lines, "realm-1")

    assert len(seen_calls) == 3
    for expense_id, _qbo_line_id, live_ids, realm_id in seen_calls:
        assert expense_id == 10
        assert live_ids == frozenset({"1", "2"})
        assert realm_id == "realm-1"


def test_sync_line_items_raises_when_any_line_fails():
    connector = PurchaseExpenseConnector(expense_service=Mock())
    line_connector = Mock()
    line_connector.sync_from_qbo_purchase_line.side_effect = [None, RuntimeError("boom")]
    connector._line_connector = line_connector

    with pytest.raises(RuntimeError, match=r"1 of 2"):
        connector._sync_line_items(
            10, "exp-pub", [_make_qbo_purchase_line(qbo_line_id="1"), _make_qbo_purchase_line(qbo_line_id="2")],
            "realm-1",
        )


# --- deploy-gap bridge (3 call sites) --------------------------------------------


def test_bridge_is_object_id_guarded_and_scoped_by_qbo_purchase_line_id():
    from integrations.intuit.qbo.purchase.business.service import (
        _clear_legacy_purchase_line_expense_line_item_mapping_by_qbo_line_id,
    )

    mock_cursor = Mock()
    mock_conn = Mock()
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    with patch("shared.database.get_connection", return_value=mock_conn):
        _clear_legacy_purchase_line_expense_line_item_mapping_by_qbo_line_id(600)

    sql_text = mock_cursor.execute.call_args.args[0]
    assert "OBJECT_ID" in sql_text
    assert "qbo.PurchaseLineExpenseLineItem" in sql_text or "[PurchaseLineExpenseLineItem]" in sql_text
    assert "QboPurchaseLineId" in sql_text
    assert mock_cursor.execute.call_args.args[1] == (600,)


def test_bridge_failure_is_swallowed_best_effort():
    from integrations.intuit.qbo.purchase.business.service import (
        _clear_legacy_purchase_line_expense_line_item_mapping_by_qbo_line_id,
    )

    with patch("shared.database.get_connection", side_effect=RuntimeError("connection reset")):
        _clear_legacy_purchase_line_expense_line_item_mapping_by_qbo_line_id(600)  # must not raise


def test_purchase_router_no_longer_directly_constructs_the_retired_mapping_repo():
    """/em Gate-2 (Codex P2 finding): `cancel_expense_from_qbo_purchase_router`
    used to pre-clear qbo.PurchaseLineExpenseLineItem via a direct,
    unguarded `PurchaseLineExpenseLineItemRepository()` construction, ahead
    of `ExpenseService().delete_by_public_id`'s own cascade — which already
    clears the SAME mapping per line via this unit's OBJECT_ID-guarded
    bridge (ExpenseLineItemService.delete_by_public_id). Left as-is, the
    router's direct construction would start hard-failing before ever
    reaching the guarded cascade once /em applies the eventual table/sproc
    DROP. Static guard: the route module must carry no reference to the
    retired repo class at all."""
    import inspect

    from integrations.intuit.qbo.purchase.api import router as purchase_router

    source = inspect.getsource(purchase_router)
    assert "PurchaseLineExpenseLineItemRepository" not in source


def test_upsert_purchase_lines_stale_cleanup_clears_bridge_before_deleting_staging_line():
    repo = Mock()
    line_repo = Mock()
    line_repo.read_by_qbo_purchase_id_and_qbo_line_id.return_value = None
    line_repo.read_by_qbo_purchase_id.return_value = [
        SimpleNamespace(id=501, qbo_line_id="1"),
        SimpleNamespace(id=502, qbo_line_id="9"),  # no longer in the QBO response
    ]
    svc = QboPurchaseService(repo=repo, line_repo=line_repo)

    call_order = []
    with patch(
        "integrations.intuit.qbo.purchase.business.service."
        "_clear_legacy_purchase_line_expense_line_item_mapping_by_qbo_line_id",
        side_effect=lambda qbo_purchase_line_id: call_order.append(("bridge", qbo_purchase_line_id)),
    ):
        line_repo.delete_by_id.side_effect = lambda lid: call_order.append(("delete", lid))
        svc._upsert_purchase_lines(88, [
            SimpleNamespace(
                id="1", line_num=1, description="d", amount=Decimal("1"), detail_type="AccountBasedExpenseLineDetail",
                item_based_expense_line_detail=None, account_based_expense_line_detail=None,
            )
        ])

    assert call_order == [("bridge", 502), ("delete", 502)]


def test_reconcile_deleted_purchases_step1_clears_bridge_per_staging_line():
    repo = Mock()
    local = SimpleNamespace(qbo_id="42", id=1, realm_id="realm-1")
    repo.read_by_realm_id.return_value = [local]
    line_repo = Mock()
    line_repo.read_by_qbo_purchase_id.return_value = [
        SimpleNamespace(id=901, qbo_line_id="1"),
        SimpleNamespace(id=902, qbo_line_id="2"),
    ]
    svc = QboPurchaseService(repo=repo, line_repo=line_repo)

    bridge_calls = []
    with patch(
        "integrations.intuit.qbo.purchase.business.service.QboPurchaseClient"
    ), patch(
        "integrations.intuit.qbo.base.delete_reconcile.strict_confirmed_deleted_ids",
        return_value={"42"},
    ), patch(
        "integrations.intuit.qbo.purchase.business.service."
        "_clear_legacy_purchase_line_expense_line_item_mapping_by_qbo_line_id",
        side_effect=lambda qbo_purchase_line_id: bridge_calls.append(qbo_purchase_line_id),
    ), patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls:
        expense_svc_cls.return_value.read_by_qbo_identity.return_value = None
        deleted = svc._reconcile_deleted_purchases("realm-1")

    assert deleted == 1
    assert bridge_calls == [901, 902]


# ---------------------------------------------------------------------------
# Reconciliation re-expression: PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL vs
# the retired qbo.PurchaseLineExpenseLineItem mapping-hop query, on a shared
# SQLite fixture (characterization / equivalence) — mirrors U-363's
# BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL precedent exactly.
# ---------------------------------------------------------------------------

_LEGACY_MAPPING_HOP_SQL = """
    SELECT
        e.Id AS ExpenseId,
        CAST(e.PublicId AS NVARCHAR(50)) AS ExpensePublicId,
        e.QboId AS QboPurchaseId,
        e.ReferenceNumber,
        eli.Id AS ExpenseLineItemId,
        eli.Amount AS LineAmount,
        inv.InvoiceNumber AS InvoiceNumber
    FROM dbo.ExpenseLineItem eli
    JOIN dbo.Expense e ON e.Id = eli.ExpenseId
    JOIN qbo.PurchaseLineExpenseLineItem map ON map.ExpenseLineItemId = eli.Id
    JOIN qbo.PurchaseLine ql ON ql.Id = map.QboPurchaseLineId
    JOIN qbo.Purchase qp ON qp.Id = ql.QboPurchaseId AND qp.RealmId = e.RealmId
    LEFT JOIN dbo.InvoiceLineItem ili ON ili.ExpenseLineItemId = eli.Id
    LEFT JOIN dbo.Invoice inv ON inv.Id = ili.InvoiceId
    WHERE eli.IsBilled = 1
      AND ql.BillableStatus = 'Billable'
      AND e.RealmId = ?
      AND e.QboId IS NOT NULL
"""


def _fixture_db():
    """SQLite stand-in with `dbo`/`qbo` ATTACHed as schema names so the
    production SQL text runs unmodified. Representative corpus (mirrors
    U-363's own bill fixture, one entity family down):

      eli 11  Expense 1 (Q1,R1), IsBilled=1, QboId='1' -> matches ql 5001 (Billable)
      eli 12  Expense 2 (Q2,R1), IsBilled=1, QboId='2' -> matches ql 5002 (Billable)
      eli 13  Expense 3 (Q3,R2), IsBilled=1, QboId='1' -> OTHER realm; matches its
              OWN expense's ql 5003 (Billable) when queried AT realm R2
      eli 14  Expense 4 (Q4,R1), IsBilled=0            -> excluded (not billed)
      eli 15  Expense 5 (Q5,R1), IsBilled=1, QboId='1' -> its OWN match, ql 5005,
              is NotBillable, so it's excluded — but ql 5001/5004/5006 (Expenses
              1/4/6, ALL realm R1) also carry QboLineId='1' and ARE Billable;
              a join that isn't scoped to eli 15's OWN parent qbo.Purchase (105)
              would incorrectly pick one of those up and flip this row IN.
      eli 16  Expense 6 (Q6,R1), IsBilled=1, QboId=NULL -> excluded (dbo-native:
              no identity to resolve the QBO line through at all)
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS dbo")
    conn.execute("ATTACH DATABASE ':memory:' AS qbo")
    conn.executescript(
        """
        CREATE TABLE dbo.Expense(Id INTEGER PRIMARY KEY, PublicId TEXT, QboId TEXT, RealmId TEXT, ReferenceNumber TEXT);
        CREATE TABLE dbo.ExpenseLineItem(Id INTEGER PRIMARY KEY, ExpenseId INTEGER, Amount REAL, IsBilled INTEGER, QboId TEXT);
        CREATE TABLE qbo.Purchase(Id INTEGER PRIMARY KEY, QboId TEXT, RealmId TEXT);
        CREATE TABLE qbo.PurchaseLine(Id INTEGER PRIMARY KEY, QboPurchaseId INTEGER, QboLineId TEXT, BillableStatus TEXT);
        CREATE TABLE qbo.PurchaseLineExpenseLineItem(Id INTEGER PRIMARY KEY, ExpenseLineItemId INTEGER, QboPurchaseLineId INTEGER);
        CREATE TABLE dbo.InvoiceLineItem(Id INTEGER PRIMARY KEY, ExpenseLineItemId INTEGER, InvoiceId INTEGER);
        CREATE TABLE dbo.Invoice(Id INTEGER PRIMARY KEY, InvoiceNumber TEXT);

        INSERT INTO dbo.Expense VALUES
          (1, 'p1', 'Q1', 'R1', 'E-1'),
          (2, 'p2', 'Q2', 'R1', 'E-2'),
          (3, 'p3', 'Q3', 'R2', 'E-3'),
          (4, 'p4', 'Q4', 'R1', 'E-4'),
          (5, 'p5', 'Q5', 'R1', 'E-5'),
          (6, 'p6', 'Q6', 'R1', 'E-6');
        INSERT INTO dbo.ExpenseLineItem VALUES
          (11, 1, 100.00, 1, '1'),
          (12, 2, 200.00, 1, '2'),
          (13, 3, 300.00, 1, '1'),
          (14, 4, 400.00, 0, '1'),
          (15, 5, 500.00, 1, '1'),
          (16, 6, 600.00, 1, NULL);
        INSERT INTO qbo.Purchase VALUES (101, 'Q1', 'R1'), (102, 'Q2', 'R1'), (103, 'Q3', 'R2'),
                                        (104, 'Q4', 'R1'), (105, 'Q5', 'R1'), (106, 'Q6', 'R1');
        INSERT INTO qbo.PurchaseLine VALUES
          (5001, 101, '1', 'Billable'),
          (5002, 102, '2', 'Billable'),
          (5003, 103, '1', 'Billable'),
          (5004, 104, '1', 'Billable'),
          (5005, 105, '1', 'NotBillable'),
          (5006, 106, '1', 'Billable');
        INSERT INTO qbo.PurchaseLineExpenseLineItem VALUES (1, 11, 5001), (2, 12, 5002), (3, 13, 5003),
                                                            (4, 14, 5004), (5, 15, 5005);
        INSERT INTO dbo.Invoice VALUES (900, 'INV-900');
        INSERT INTO dbo.InvoiceLineItem VALUES (9001, 11, 900);
        """
    )
    return conn


def _rows(conn, sql, realm_id):
    return sorted(conn.execute(sql, (realm_id,)).fetchall())


def test_dbo_native_drift_rows_match_legacy_mapping_hop_on_representative_fixture():
    conn = _fixture_db()
    for realm in ("R1", "R2", "R-NONE"):
        assert _rows(conn, PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL, realm) == _rows(conn, _LEGACY_MAPPING_HOP_SQL, realm)

    rows = _rows(conn, PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL, "R1")
    # (ExpenseId, ExpensePublicId, QboPurchaseId, ReferenceNumber, ExpenseLineItemId, LineAmount, InvoiceNumber)
    assert (1, "p1", "Q1", "E-1", 11, 100.0, "INV-900") in rows
    assert (2, "p2", "Q2", "E-2", 12, 200.0, None) in rows
    assert [r[0] for r in rows] == [1, 2]
    assert len(rows) == 2  # eli 14 (unbilled), 15 (see next test), 16 (no QboId) all excluded


def test_dbo_native_drift_rows_never_cross_matches_reused_line_id_across_purchases():
    """The parent-scoping guard this re-expression's own design note calls
    out: qbo.PurchaseLine's QboLineId is only unique WITHIN its parent
    qbo.Purchase — 4 different qbo.Purchase rows in realm R1 (101, 104, 105,
    106) all carry a line with QboLineId='1'. eli 15's OWN match (ql 5005,
    under its actual parent qp 105) is NotBillable, so it must NOT appear in
    the drift result — but 3 of those 4 SAME-QboLineId siblings (ql 5001/
    5004/5006, under DIFFERENT purchases) ARE Billable. A join missing the
    `ql.QboPurchaseId = qp.Id` parent-scope (bare `ql.QboLineId = eli.QboId`)
    would pick one of those up instead and incorrectly flip eli 15 into the
    drift result."""
    conn = _fixture_db()
    rows_r1 = _rows(conn, PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL, "R1")
    assert 5 not in {r[0] for r in rows_r1}  # Expense 5 / eli 15 must not appear
    assert rows_r1 == _rows(conn, _LEGACY_MAPPING_HOP_SQL, "R1")  # legacy hop agrees


def test_purchase_billable_status_drift_sql_is_mapping_free_and_parent_scoped():
    assert "qbo.PurchaseLineExpenseLineItem" not in PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL
    assert "JOIN qbo.Purchase qp ON qp.QboId = e.QboId AND qp.RealmId = e.RealmId" in PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL
    assert "JOIN qbo.PurchaseLine ql ON ql.QboPurchaseId = qp.Id AND ql.QboLineId = eli.QboId" in PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL
    assert "WHERE eli.IsBilled = 1" in PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL
    assert "AND eli.QboId IS NOT NULL" in PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL


def test_reconcile_purchase_billable_status_drift_executes_the_module_level_row_source():
    """The constant the equivalence test exercises must be what production
    actually runs — guards against the two drifting apart."""
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value.cursor.return_value = cursor
    service = ReconciliationService(repo=Mock())

    with patch("shared.database.get_connection", return_value=conn_ctx):
        result = service._reconcile_purchase_billable_status_drift(realm_id="realm-1", run_id="run-1")

    cursor.execute.assert_called_once_with(PURCHASE_BILLABLE_STATUS_DRIFT_ROWS_SQL, "realm-1")
    assert result["errors"] == 0 and result["flagged"] == 0


# --- regression: the sproc identity-projection gap the map found ---------------
#
# Static, no-DB-required guard: ReadExpenseLineItemById / -ByPublicId /
# -sByExpenseId / UpdateExpenseLineItemById MUST project QboId/RealmId, or
# any future caller that trusts their return value's identity fields
# silently gets None instead of the row's real (already-stamped) identity —
# the exact bug class U-361/U-362/U-363's own code review caught elsewhere
# in this program, found here by inspection before any code shipped.
# Mutation-proven: reverting any projection below makes this RED.

_BASE_SQL = Path("entities/expense_line_item/sql/dbo.expense_line_item.sql").read_text()


def _sproc_body(sql_text: str, name: str) -> str:
    start = sql_text.index(f"PROCEDURE {name}")
    end = sql_text.index("\nGO", start)
    return sql_text[start:end]


def _eli_sproc_body(name: str) -> str:
    return _sproc_body(_BASE_SQL, name)


def test_read_by_id_sproc_projects_qbo_identity_columns():
    body = _eli_sproc_body("ReadExpenseLineItemById")
    assert "[QboId]" in body
    assert "[RealmId]" in body


def test_read_by_public_id_sproc_projects_qbo_identity_columns():
    body = _eli_sproc_body("ReadExpenseLineItemByPublicId")
    assert "[QboId]" in body
    assert "[RealmId]" in body


def test_read_by_expense_id_sproc_projects_qbo_identity_columns():
    body = _eli_sproc_body("ReadExpenseLineItemsByExpenseId")
    assert "[QboId]" in body
    assert "[RealmId]" in body


def test_update_by_id_sproc_projects_qbo_identity_columns():
    body = _eli_sproc_body("UpdateExpenseLineItemById")
    assert "INSERTED.[QboId]" in body, (
        "UpdateExpenseLineItemById's OUTPUT must include INSERTED.[QboId] - the HIT "
        "path's update_by_public_id return value should carry the row's real "
        "identity, not silently read back as None."
    )
    assert "INSERTED.[RealmId]" in body, "UpdateExpenseLineItemById's OUTPUT must also include INSERTED.[RealmId]."
