"""Pure-logic tests for U-293b: repoint the `expense_line_item` connector's LINE
identity resolution off qbo.PurchaseLine / qbo.PurchaseLineExpenseLineItem onto
dbo.ExpenseLineItem's native QboId (U-238b), scoped to its parent Expense, via
the shared base/identity_fastpath.py::run_line_identity_fastpath helper — the
same helper U-293's Bill pilot proved, now fanned out to the 2nd of 3 remaining
line families.

The shared helper's state machine is already exhaustively tested in
tests/test_u293_line_identity_fastpath_helper.py; these tests prove THIS
connector's wiring: the callbacks it hands the helper, that a conflict never
writes to the dbo-identity-matched row, that the legacy mapping-table +
Shape-B fingerprint fallback keeps working unchanged on a fast-path miss, and
(U-293-dw fold-in) that the update path stamps identity with
enforce_realm_pairing=True and an existing-realm_id fallback.

Fixtures give qbo_line an explicit qty/unit_price so default_amount_only_line's
defaulting never engages — that logic is already exhaustively covered by
tests/test_qbo_purchase_line_defaults.py; this file's job is connector wiring.

Mirrors tests/test_u293_bill_line_item_qbo_identity_repoint.py's shape.
"""
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
    PurchaseLineExpenseLineItemConnector,
)


def _make_qbo_line(**overrides):
    defaults = dict(
        id=42,
        qbo_purchase_id=4,
        qbo_line_id="1",
        description="Materials",
        amount=Decimal("250"),
        item_ref_value=None,
        customer_ref_value=None,
        billable_status=None,
        qty=Decimal("2"),
        unit_price=Decimal("125"),
        markup_percent=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector():
    mapping_repo = Mock()
    expense_line_item_service = Mock()
    expense_line_item_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = PurchaseLineExpenseLineItemConnector(
        mapping_repo=mapping_repo,
        expense_line_item_service=expense_line_item_service,
        item_sub_cost_code_repo=Mock(),
        qbo_item_repo=Mock(),
        customer_project_repo=Mock(),
        qbo_customer_repo=Mock(),
        reconciliation_repo=reconciliation_repo,
    )
    return connector, mapping_repo, expense_line_item_service, reconciliation_repo


def test_raise_line_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(id=42, qbo_line_id="1")
    qbo_side = SimpleNamespace(id=2, expense_line_item_id=9, qbo_purchase_line_id=42)
    local_side = SimpleNamespace(id=3, expense_line_item_id=55, qbo_purchase_line_id=5)

    connector._raise_line_identity_mapping_conflict_issue(
        qbo_line=qbo_line, dbo_line_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
        realm_id="realm-1",
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "expense_line_identity_conflict"
    assert kwargs["realm_id"] == "realm-1"
    assert "ExpenseLineItem 9 (mapping 2)" in kwargs["details"]
    assert "DIFFERENT QboPurchaseLine 5" in kwargs["details"]


def test_raise_line_identity_mapping_conflict_issue_qbo_side_only():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(id=42, qbo_line_id="1")
    qbo_side = SimpleNamespace(id=2, expense_line_item_id=9, qbo_purchase_line_id=42)

    connector._raise_line_identity_mapping_conflict_issue(
        qbo_line=qbo_line, dbo_line_id=55,
        local_side_mapping=None, qbo_side_mapping=qbo_side,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "ExpenseLineItem 9 (mapping 2)" in kwargs["details"]
    assert "local-side" not in kwargs["details"]
    assert kwargs["realm_id"] == ""


def test_raise_line_identity_mapping_conflict_issue_local_side_only():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(id=42, qbo_line_id="1")
    local_side = SimpleNamespace(id=3, expense_line_item_id=55, qbo_purchase_line_id=5)

    connector._raise_line_identity_mapping_conflict_issue(
        qbo_line=qbo_line, dbo_line_id=55,
        local_side_mapping=local_side, qbo_side_mapping=None,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "DIFFERENT QboPurchaseLine 5" in kwargs["details"]
    assert "qbo-side" not in kwargs["details"]


def test_fast_path_hit_conflict_raises_and_never_writes():
    connector, mapping_repo, eli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    eli_svc.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_expense_line_item_id.return_value = None
    conflicting = SimpleNamespace(id=2, expense_line_item_id=9, qbo_purchase_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_purchase_line_id.return_value = conflicting
    # If the fast path fell through (it must not), these would let the legacy
    # branch reach and write/adopt a different local row.
    eli_svc.read_by_id.return_value = SimpleNamespace(id=9, public_id="pub-9", row_version="rv-9")
    eli_svc.read_by_expense_id.return_value = []
    eli_svc.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any ExpenseLineItem on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "expense_line_identity_conflict"
    mapping_repo.create.assert_not_called()
    eli_svc.repo.set_qbo_identity.assert_not_called()


def test_fast_path_miss_does_not_requery_the_mapping_it_already_ruled_out():
    connector, mapping_repo, eli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1", description="Materials", amount=Decimal("250"))
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    eli_svc.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_expense_line_item_id.return_value = None
    mapping_repo.read_by_qbo_purchase_line_id.return_value = None
    orphan = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55",
        description="Materials", amount=Decimal("250"), quantity=Decimal("2"), rate=Decimal("125"),
    )
    eli_svc.read_by_expense_id.return_value = [orphan]
    mapping_repo.create.return_value = SimpleNamespace(id=1, expense_line_item_id=55, qbo_purchase_line_id=42)
    updated = SimpleNamespace(id=55, public_id="pub-55")
    eli_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    assert result is updated
    mapping_repo.create.assert_called_once_with(expense_line_item_id=55, qbo_purchase_line_id=42)
    mapping_repo.read_by_qbo_purchase_line_id.assert_called_once_with(42)


def test_fast_path_hit_conflict_local_side_only_raises_and_attributes_correctly():
    connector, mapping_repo, eli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    eli_svc.read_by_qbo_identity.return_value = direct_hit
    local_side = SimpleNamespace(id=3, expense_line_item_id=55, qbo_purchase_line_id=7)
    mapping_repo.read_by_expense_line_item_id.return_value = local_side
    mapping_repo.read_by_qbo_purchase_line_id.return_value = None
    eli_svc.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any ExpenseLineItem on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "DIFFERENT QboPurchaseLine 7" in kwargs["details"]
    assert "qbo-side" not in kwargs["details"]


def test_fast_path_hit_conflict_both_sides_crossed_raises_and_names_both():
    connector, mapping_repo, eli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    eli_svc.read_by_qbo_identity.return_value = direct_hit
    local_side = SimpleNamespace(id=3, expense_line_item_id=55, qbo_purchase_line_id=7)
    qbo_side = SimpleNamespace(id=2, expense_line_item_id=9, qbo_purchase_line_id=qbo_line.id)
    mapping_repo.read_by_expense_line_item_id.return_value = local_side
    mapping_repo.read_by_qbo_purchase_line_id.return_value = qbo_side
    eli_svc.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any ExpenseLineItem on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "ExpenseLineItem 9 (mapping 2)" in kwargs["details"]
    assert "DIFFERENT QboPurchaseLine 7" in kwargs["details"]


def test_fast_path_hit_consistent_updates_and_restamps_identity():
    connector, mapping_repo, eli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        quantity=Decimal("2"), rate=Decimal("125"), markup=Decimal("0"),
    )
    eli_svc.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    eli_svc.update_by_public_id.return_value = updated
    mapping_repo.read_by_expense_line_item_id.return_value = SimpleNamespace(id=1, qbo_purchase_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_purchase_line_id.return_value = SimpleNamespace(id=1, expense_line_item_id=55)

    result = connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    assert result is updated
    mapping_repo.create.assert_not_called()
    eli_svc.repo.set_qbo_identity.assert_called_once_with(id=55, qbo_id="1", realm_id="realm-1")


def test_fast_path_hit_falls_back_to_existing_realm_id_when_call_realm_id_missing():
    """U-293-dw fold-in: the atomic-pair guard + existing-realm fallback in
    _apply_line_fields fires identically via the fast-path hit branch — this
    call passes no realm_id, but the direct-hit row already carries one, so
    the restamp must still fire using the row's own realm_id."""
    connector, mapping_repo, eli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1", realm_id="realm-existing",
        quantity=Decimal("2"), rate=Decimal("125"), markup=Decimal("0"),
    )
    eli_svc.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    eli_svc.update_by_public_id.return_value = updated
    mapping_repo.read_by_expense_line_item_id.return_value = SimpleNamespace(id=1, qbo_purchase_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_purchase_line_id.return_value = SimpleNamespace(id=1, expense_line_item_id=55)

    result = connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line)

    assert result is updated
    eli_svc.repo.set_qbo_identity.assert_called_once_with(id=55, qbo_id="1", realm_id="realm-existing")


def test_fast_path_hit_missing_falls_through_without_writing_or_minting():
    connector, mapping_repo, eli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1", description="Materials", amount=Decimal("500"))
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        description="Unrelated", amount=Decimal("1"),
    )
    eli_svc.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_expense_line_item_id.return_value = None
    mapping_repo.read_by_qbo_purchase_line_id.return_value = None
    eli_svc.read_by_expense_id.return_value = []  # no unmapped orphans to fingerprint-match
    created = SimpleNamespace(id=77, public_id="pub-77")
    eli_svc.create.return_value = created

    result = connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    assert result is created
    eli_svc.update_by_public_id.assert_not_called()
    mapping_repo.create.assert_called_once_with(expense_line_item_id=77, qbo_purchase_line_id=qbo_line.id)
    reconciliation_repo.create.assert_not_called()


def test_fast_path_hit_missing_on_a_stale_orphan_does_not_overwrite_it():
    connector, mapping_repo, eli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1", description="Materials", amount=Decimal("500"))
    stale_orphan = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        description="Old Item", amount=Decimal("100"), quantity=Decimal("1"), rate=Decimal("100"),
    )
    eli_svc.read_by_qbo_identity.return_value = stale_orphan
    mapping_repo.read_by_expense_line_item_id.return_value = None
    mapping_repo.read_by_qbo_purchase_line_id.return_value = None
    eli_svc.read_by_expense_id.return_value = [stale_orphan]
    created = SimpleNamespace(id=88, public_id="pub-88")
    eli_svc.create.return_value = created

    result = connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    eli_svc.update_by_public_id.assert_not_called()
    mapping_repo.create.assert_called_once_with(expense_line_item_id=88, qbo_purchase_line_id=qbo_line.id)
    assert result is created
    reconciliation_repo.create.assert_not_called()


def test_fast_path_update_returns_none_raises_runtime_error():
    connector, mapping_repo, eli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        quantity=Decimal("2"), rate=Decimal("125"), markup=Decimal("0"),
    )
    eli_svc.read_by_qbo_identity.return_value = direct_hit
    eli_svc.update_by_public_id.return_value = None
    mapping_repo.read_by_expense_line_item_id.return_value = SimpleNamespace(id=1, qbo_purchase_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_purchase_line_id.return_value = SimpleNamespace(id=1, expense_line_item_id=55)

    with pytest.raises(RuntimeError, match="Failed to update ExpenseLineItem"):
        connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    eli_svc.repo.set_qbo_identity.assert_not_called()


def test_legacy_path_update_returns_none_also_raises_runtime_error():
    connector, mapping_repo, eli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    eli_svc.read_by_qbo_identity.return_value = None  # fast path misses
    existing_mapping = SimpleNamespace(id=1, expense_line_item_id=55, qbo_purchase_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_purchase_line_id.return_value = existing_mapping
    existing_line = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55",
        quantity=Decimal("2"), rate=Decimal("125"), markup=Decimal("0"),
    )
    eli_svc.read_by_id.return_value = existing_line
    eli_svc.update_by_public_id.return_value = None

    with pytest.raises(RuntimeError, match="Failed to update ExpenseLineItem"):
        connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")


def test_fast_path_miss_falls_back_to_legacy_mapping_table_path():
    connector, mapping_repo, eli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    eli_svc.read_by_qbo_identity.return_value = None
    existing_mapping = SimpleNamespace(id=1, expense_line_item_id=55, qbo_purchase_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_purchase_line_id.return_value = existing_mapping
    existing_line = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55",
        quantity=Decimal("2"), rate=Decimal("125"), markup=Decimal("0"),
    )
    eli_svc.read_by_id.return_value = existing_line
    updated = SimpleNamespace(id=55, public_id="pub-55")
    eli_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    eli_svc.read_by_qbo_identity.assert_called_once_with(19146, "1")
    assert result is updated
    eli_svc.repo.set_qbo_identity.assert_called_once()


def test_fast_path_skipped_entirely_when_no_qbo_line_id():
    connector, mapping_repo, eli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id=None, description="Materials", amount=Decimal("250"))
    mapping_repo.read_by_qbo_purchase_line_id.return_value = None
    mapping_repo.read_by_expense_line_item_id.return_value = None
    eli_svc.read_by_expense_id.return_value = []
    created = SimpleNamespace(id=77, public_id="pub-77")
    eli_svc.create.return_value = created

    result = connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    eli_svc.read_by_qbo_identity.assert_not_called()
    assert result is created


def test_fast_path_direct_lookup_is_scoped_to_this_expense_not_a_bare_qbo_id():
    connector, mapping_repo, eli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    eli_svc.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_purchase_line_id.return_value = None
    mapping_repo.read_by_expense_line_item_id.return_value = None
    eli_svc.read_by_expense_id.return_value = []
    eli_svc.create.return_value = SimpleNamespace(id=1, public_id="p")

    connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line, realm_id="realm-1")

    eli_svc.read_by_qbo_identity.assert_called_once_with(19146, "1")


def test_create_path_and_update_path_both_pass_enforce_realm_pairing_true():
    """U-293-dw debt #2 fold-in (per family): assert BOTH this connector's own
    call sites (create-path, update-path) actually pass
    enforce_realm_pairing=True to stamp_line_identity_or_warn, not just the
    shared function's isolated behavior (already covered in
    tests/test_qbo_identity_lines.py). Mirrors Bill's own equivalent test."""
    from unittest.mock import patch as _patch

    connector, mapping_repo, eli_svc, _ = _build_connector()
    eli_svc.read_by_qbo_identity.return_value = None  # fast path misses both times
    mapping_repo.read_by_expense_line_item_id.return_value = None

    mapping_repo.read_by_qbo_purchase_line_id.return_value = None
    eli_svc.read_by_expense_id.return_value = []
    created = SimpleNamespace(id=77, public_id="pub-77")
    eli_svc.create.return_value = created

    existing_mapping = SimpleNamespace(id=1, expense_line_item_id=77, qbo_purchase_line_id=43)
    existing_line = SimpleNamespace(
        id=77, public_id="pub-77", row_version="rv-77",
        quantity=Decimal("2"), rate=Decimal("125"), markup=Decimal("0"),
    )
    updated = SimpleNamespace(id=77, public_id="pub-77")

    with _patch(
        "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service.stamp_line_identity_or_warn"
    ) as mock_stamp:
        qbo_line_create = _make_qbo_line(id=42, qbo_line_id="1")
        connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line_create, realm_id="realm-1")

        mapping_repo.read_by_qbo_purchase_line_id.return_value = existing_mapping
        eli_svc.read_by_id.return_value = existing_line
        eli_svc.update_by_public_id.return_value = updated
        qbo_line_update = _make_qbo_line(id=43, qbo_line_id="2")
        connector.sync_from_qbo_purchase_line(19146, "exp-pub-1", qbo_line_update, realm_id="realm-1")

    assert mock_stamp.call_count == 2
    for call in mock_stamp.call_args_list:
        assert call.kwargs.get("enforce_realm_pairing") is True
