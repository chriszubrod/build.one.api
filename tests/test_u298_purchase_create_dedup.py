"""Pure-logic tests for U-298 (Wave-1) — PurchaseExpenseConnector's CREATE path
gets a dbo-native uniqueness recheck immediately before minting a new Expense.

Without this recheck, a second process racing the exact same record between the
top-of-function fast-path check (see test_u283b_purchase_qbo_identity_repoint.py)
and the literal `expense_service.create(...)` call would mint a genuine duplicate
Expense: SetExpenseQboIdentity's theft-clear UPDATE does not fail on a unique-index
violation, it silently steals the (QboId, RealmId) pair onto whichever row stamps
it LAST — leaving one Expense correctly identified and the other permanently
orphaned (no QboId left to ever re-resolve it by). U-304 added a real
sp_getapplock (`create_race_lock`, see test_u304_rollback_lock.py) spanning this
recheck-and-conditional-rollback and the self-heal insert it races against — this
module grants that lock unconditionally via conftest.py's `grant_qbo_app_lock`
fixture (see `pytestmark` below) since these tests are pure-logic / no-live-DB
and only exercise the resolve-state branching, not the serialization itself.

These tests simulate the race by making `expense_service.read_by_qbo_identity`
MISS on the first call (top-of-function) and HIT on the second (immediately
before create) — proving the recheck folds into the race's winner instead of
minting the loser. Mocks stand in for expense_service + repos; no DB/QBO I/O.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
)

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")

LINE_CONNECTOR_PATH = (
    "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
    ".PurchaseLineExpenseLineItemConnector"
)


def _make_qbo_purchase(**overrides):
    defaults = dict(
        id=901,
        qbo_id="PURCH-77",
        realm_id="realm-1",
        entity_ref_value="qbo-vendor-1",
        doc_number="5001",
        txn_date="2026-08-01",
        private_note="card spend",
        total_amt=100,
        credit=False,
        sync_token="3",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_ONE_LINE = [SimpleNamespace(id=1)]


def _build_connector():
    mapping_repo = Mock()
    expense_service = Mock()
    expense_service.repo = Mock()
    reconciliation_repo = Mock()
    with patch(LINE_CONNECTOR_PATH, return_value=Mock()):
        connector = PurchaseExpenseConnector(
            mapping_repo=mapping_repo,
            expense_service=expense_service,
            reconciliation_repo=reconciliation_repo,
        )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector, mapping_repo, expense_service, reconciliation_repo


def test_create_race_recheck_self_heals_onto_winner_no_duplicate():
    """Both the top-of-function fast path AND the legacy mapping-table check miss
    (the state the un-patched code fell straight through to CREATE from) — but a
    concurrent racer's Expense has landed by the time of the create-time recheck.
    Must bind to the winner (self-heal a mapping for it) and NEVER call
    expense_service.create."""
    connector, mapping_repo, expense_service, _ = _build_connector()
    qbo_purchase = _make_qbo_purchase()

    winner = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_qbo_identity.side_effect = [None, winner]
    mapping_repo.read_by_qbo_purchase_id.side_effect = [None, None]  # legacy check, then recheck's by_external
    mapping_repo.read_by_expense_id.return_value = None  # recheck's by_local: MISSING state

    updated = SimpleNamespace(id=55, public_id="pub-55")
    expense_service.update_by_public_id.return_value = updated
    mapping_repo.create.return_value = SimpleNamespace(id=1)

    result = connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    assert result is updated
    expense_service.create.assert_not_called()  # NO duplicate Expense minted
    mapping_repo.create.assert_called_once_with(expense_id=55, qbo_purchase_id=qbo_purchase.id)
    assert expense_service.read_by_qbo_identity.call_count == 2  # proves the recheck ran


def test_create_race_recheck_conflict_raises_never_creates():
    """The create-time recheck finds a dbo.Expense identity match whose mapping
    disagrees (CONFLICT) — must hard-stop (record + raise), never fall through to
    expense_service.create."""
    connector, mapping_repo, expense_service, reconciliation_repo = _build_connector()
    qbo_purchase = _make_qbo_purchase()

    winner = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_qbo_identity.side_effect = [None, winner]
    mapping_repo.read_by_qbo_purchase_id.side_effect = [
        None,  # legacy "check for existing mapping" — miss
        SimpleNamespace(id=9, expense_id=55, qbo_purchase_id=555),  # recheck's by_external: disagrees
    ]
    mapping_repo.read_by_expense_id.return_value = None  # by_local doesn't resolve it either -> CONFLICT

    with pytest.raises(ValueError):
        connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    expense_service.create.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "expense_identity_conflict"
    assert expense_service.read_by_qbo_identity.call_count == 2  # proves the recheck ran


def test_create_proceeds_when_recheck_also_misses():
    """No race: both the initial check and the create-time recheck miss -> the
    genuine create path still runs exactly once, unaffected by the extra guard."""
    connector, mapping_repo, expense_service, _ = _build_connector()
    qbo_purchase = _make_qbo_purchase()

    expense_service.read_by_qbo_identity.return_value = None  # miss on every call
    mapping_repo.read_by_qbo_purchase_id.return_value = None
    mapping_repo.read_by_expense_id.return_value = None  # create_mapping's own 1:1 guard
    created = SimpleNamespace(id=77, public_id="pub-77")
    expense_service.create.return_value = created
    mapping_repo.create.return_value = SimpleNamespace(id=2)

    result = connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    assert result is created
    expense_service.create.assert_called_once()
    assert expense_service.read_by_qbo_identity.call_count == 2  # initial check + recheck, both miss


def test_create_mapping_race_returns_racers_result_without_rollback():
    """Confirmed P1 (U-298 Gate-1 hunt): a concurrent racer's identity-fastpath
    recheck can win the mapping insert in the window between create_mapping's
    own set_qbo_identity stamp and its mapping_repo.create call. The resulting
    unique-constraint collision must NOT roll back (delete) the now validly
    mapped Expense — it must recognize the racer won and return its result
    instead of destroying a legitimately-completed financial record."""
    connector, mapping_repo, expense_service, _ = _build_connector()
    qbo_purchase = _make_qbo_purchase()

    expense_service.read_by_qbo_identity.return_value = None  # miss on every fastpath call
    mapping_repo.read_by_qbo_purchase_id.return_value = None  # legacy check + create_mapping's own guard

    created = SimpleNamespace(id=77, public_id="pub-77")
    expense_service.create.return_value = created

    # create_mapping()'s own 1:1 guard (1st call) passes -> nothing exists yet.
    # The except-block's re-check (2nd call) finds the racer's mapping.
    racer_mapping = SimpleNamespace(id=5, expense_id=77, qbo_purchase_id=qbo_purchase.id)
    mapping_repo.read_by_expense_id.side_effect = [None, racer_mapping]
    # The raw insert collides with the racer's own insert of the identical pair.
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    current_state = SimpleNamespace(id=77, public_id="pub-77", reference_number="R-1")
    expense_service.read_by_id.return_value = current_state

    result = connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    assert result is current_state
    expense_service.delete_by_public_id.assert_not_called()  # NOT rolled back


def test_create_mapping_conflict_records_issue_then_still_rolls_back():
    """/simplify pass: the rollback-guard's re-check (via resolve_mapping_state,
    not a hand-rolled equality) must also handle CONFLICT — a mapping now exists
    but disagrees (points this Expense at a DIFFERENT QboPurchase). Unlike the
    benign CONSISTENT race, this is NOT a resolved race: record the same
    reconciliation issue every other conflict path in this file records, then
    still roll back this genuine orphan (mirrors the original behavior for a
    real failure, just with a durable trace now instead of a bare ValueError)."""
    connector, mapping_repo, expense_service, reconciliation_repo = _build_connector()
    qbo_purchase = _make_qbo_purchase()

    expense_service.read_by_qbo_identity.return_value = None  # miss on every fastpath call
    mapping_repo.read_by_qbo_purchase_id.return_value = None  # legacy check + create_mapping's own guard

    created = SimpleNamespace(id=77, public_id="pub-77")
    expense_service.create.return_value = created

    # create_mapping()'s own 1:1 guard (1st call) passes. The except-block's
    # re-check (2nd call) finds a mapping for THIS expense pointing at a
    # DIFFERENT qbo_purchase_id -> CONFLICT, not CONSISTENT.
    conflicting_mapping = SimpleNamespace(id=6, expense_id=77, qbo_purchase_id=555)
    mapping_repo.read_by_expense_id.side_effect = [None, conflicting_mapping]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    with pytest.raises(ValueError, match="Failed to create PurchaseExpense mapping"):
        connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "expense_identity_conflict"
    expense_service.delete_by_public_id.assert_called_once_with("pub-77")  # still rolled back
