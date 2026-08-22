"""Pure-logic tests for U-293b: repoint the `bill_credit_line_item` connector's
LINE identity resolution off qbo.VendorCreditLine /
qbo.VendorCreditLineItemBillCreditLineItem onto dbo.BillCreditLineItem's
native QboId (U-238b), scoped to its parent BillCredit, via the shared
base/identity_fastpath.py::run_line_identity_fastpath helper — the same
helper U-293's Bill pilot proved, now fanned out to the 3rd of 3 remaining
line families (the last of the U-293b fan-out).

The shared helper's state machine is already exhaustively tested in
tests/test_u293_line_identity_fastpath_helper.py; these tests prove THIS
connector's wiring: the callbacks it hands the helper, that a conflict never
writes to the dbo-identity-matched row, that the legacy mapping-table +
Shape-B fingerprint fallback keeps working unchanged on a fast-path miss, and
(U-293-dw fold-in) that the update path stamps identity with
enforce_realm_pairing=True and an existing-realm_id fallback. Also pins the
pre-existing ROWVERSION-race guard this unit ADDS to the legacy update branch
(the pre-U-293b code had none — see _apply_line_fields's docstring).

Mirrors tests/test_u293_bill_line_item_qbo_identity_repoint.py's shape.
"""
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)


def _make_qbo_line(**overrides):
    defaults = dict(
        id=42,
        qbo_vendor_credit_id=4,
        qbo_line_id="1",
        description="Credit",
        amount=Decimal("50"),
        qty=Decimal("1"),
        unit_price=Decimal("50"),
        billable_status=None,
        customer_ref_value=None,
        item_ref_value=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector():
    connector = VendorCreditLineItemConnector()
    mapping_repo = Mock()
    bill_credit_line_item_service = Mock()
    bill_credit_line_item_service.repo = Mock()
    reconciliation_repo = Mock()
    connector.mapping_repo = mapping_repo
    connector.bill_credit_line_item_service = bill_credit_line_item_service
    connector.reconciliation_repo = reconciliation_repo
    connector._get_project_public_id = Mock(return_value=None)
    connector._get_sub_cost_code_id = Mock(return_value=None)
    return connector, mapping_repo, bill_credit_line_item_service, reconciliation_repo


def test_raise_line_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(id=42, qbo_line_id="1")
    qbo_side = SimpleNamespace(id=2, bill_credit_line_item_id=9, qbo_vendor_credit_line_id=42)
    local_side = SimpleNamespace(id=3, bill_credit_line_item_id=55, qbo_vendor_credit_line_id=5)

    connector._raise_line_identity_mapping_conflict_issue(
        qbo_line=qbo_line, dbo_line_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
        realm_id="realm-1",
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "bc_line_item_identity_conflict"
    assert kwargs["realm_id"] == "realm-1"
    assert "BillCreditLineItem 9 (mapping 2)" in kwargs["details"]
    assert "DIFFERENT QboVendorCreditLine 5" in kwargs["details"]


def test_raise_line_identity_mapping_conflict_issue_qbo_side_only():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(id=42, qbo_line_id="1")
    qbo_side = SimpleNamespace(id=2, bill_credit_line_item_id=9, qbo_vendor_credit_line_id=42)

    connector._raise_line_identity_mapping_conflict_issue(
        qbo_line=qbo_line, dbo_line_id=55,
        local_side_mapping=None, qbo_side_mapping=qbo_side,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "BillCreditLineItem 9 (mapping 2)" in kwargs["details"]
    assert "local-side" not in kwargs["details"]
    assert kwargs["realm_id"] == ""


def test_raise_line_identity_mapping_conflict_issue_local_side_only():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(id=42, qbo_line_id="1")
    local_side = SimpleNamespace(id=3, bill_credit_line_item_id=55, qbo_vendor_credit_line_id=5)

    connector._raise_line_identity_mapping_conflict_issue(
        qbo_line=qbo_line, dbo_line_id=55,
        local_side_mapping=local_side, qbo_side_mapping=None,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "DIFFERENT QboVendorCreditLine 5" in kwargs["details"]
    assert "qbo-side" not in kwargs["details"]


def test_fast_path_hit_conflict_raises_and_never_writes():
    connector, mapping_repo, bcli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bcli_svc.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_bill_credit_line_item_id.return_value = None
    conflicting = SimpleNamespace(id=2, bill_credit_line_item_id=9, qbo_vendor_credit_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_line_id.return_value = conflicting
    bcli_svc.read_by_id.return_value = SimpleNamespace(id=9, public_id="pub-9", row_version="rv-9")
    bcli_svc.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any BillCreditLineItem on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "bc_line_item_identity_conflict"
    mapping_repo.create.assert_not_called()
    bcli_svc.repo.set_qbo_identity.assert_not_called()


def test_fast_path_hit_conflict_local_side_only_raises_and_attributes_correctly():
    connector, mapping_repo, bcli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bcli_svc.read_by_qbo_identity.return_value = direct_hit
    local_side = SimpleNamespace(id=3, bill_credit_line_item_id=55, qbo_vendor_credit_line_id=7)
    mapping_repo.read_by_bill_credit_line_item_id.return_value = local_side
    mapping_repo.read_by_qbo_line_id.return_value = None
    bcli_svc.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any BillCreditLineItem on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "DIFFERENT QboVendorCreditLine 7" in kwargs["details"]
    assert "qbo-side" not in kwargs["details"]


def test_fast_path_hit_conflict_both_sides_crossed_raises_and_names_both():
    connector, mapping_repo, bcli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bcli_svc.read_by_qbo_identity.return_value = direct_hit
    local_side = SimpleNamespace(id=3, bill_credit_line_item_id=55, qbo_vendor_credit_line_id=7)
    qbo_side = SimpleNamespace(id=2, bill_credit_line_item_id=9, qbo_vendor_credit_line_id=qbo_line.id)
    mapping_repo.read_by_bill_credit_line_item_id.return_value = local_side
    mapping_repo.read_by_qbo_line_id.return_value = qbo_side
    bcli_svc.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any BillCreditLineItem on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "BillCreditLineItem 9 (mapping 2)" in kwargs["details"]
    assert "DIFFERENT QboVendorCreditLine 7" in kwargs["details"]


def test_fast_path_hit_consistent_updates_and_restamps_identity():
    connector, mapping_repo, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bcli_svc.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bcli_svc.update_by_public_id.return_value = updated
    mapping_repo.read_by_bill_credit_line_item_id.return_value = SimpleNamespace(id=1, qbo_vendor_credit_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_line_id.return_value = SimpleNamespace(id=1, bill_credit_line_item_id=55)

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    assert result is updated
    mapping_repo.create.assert_not_called()
    bcli_svc.repo.set_qbo_identity.assert_called_once_with(id=55, qbo_id="1", realm_id="realm-1")


def test_fast_path_hit_falls_back_to_existing_realm_id_when_call_realm_id_missing():
    """U-293-dw fold-in: the atomic-pair guard + existing-realm fallback in
    _apply_line_fields fires identically via the fast-path hit branch — this
    call passes no realm_id, but the direct-hit row already carries one, so
    the restamp must still fire using the row's own realm_id."""
    connector, mapping_repo, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1", realm_id="realm-existing",
    )
    bcli_svc.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bcli_svc.update_by_public_id.return_value = updated
    mapping_repo.read_by_bill_credit_line_item_id.return_value = SimpleNamespace(id=1, qbo_vendor_credit_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_line_id.return_value = SimpleNamespace(id=1, bill_credit_line_item_id=55)

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line)

    assert result is updated
    bcli_svc.repo.set_qbo_identity.assert_called_once_with(id=55, qbo_id="1", realm_id="realm-existing")


def test_fast_path_hit_missing_falls_through_without_writing_or_minting():
    connector, mapping_repo, bcli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1", description="Materials", amount=Decimal("500"))
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        description="Unrelated", amount=Decimal("1"),
    )
    bcli_svc.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_bill_credit_line_item_id.return_value = None
    mapping_repo.read_by_qbo_line_id.return_value = None
    bcli_svc.read_by_bill_credit_id.return_value = []  # no unmapped orphans to fingerprint-match
    created = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.create.return_value = created

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    assert result is created
    bcli_svc.update_by_public_id.assert_not_called()
    mapping_repo.create.assert_called_once_with(qbo_vendor_credit_line_id=qbo_line.id, bill_credit_line_item_id=77)
    reconciliation_repo.create.assert_not_called()


def test_fast_path_hit_missing_on_a_stale_orphan_does_not_overwrite_it():
    connector, mapping_repo, bcli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1", description="Materials", amount=Decimal("500"))
    stale_orphan = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        description="Old Credit", amount=Decimal("100"), quantity=Decimal("1"), unit_price=Decimal("100"),
    )
    bcli_svc.read_by_qbo_identity.return_value = stale_orphan
    mapping_repo.read_by_bill_credit_line_item_id.return_value = None
    mapping_repo.read_by_qbo_line_id.return_value = None
    bcli_svc.read_by_bill_credit_id.return_value = [stale_orphan]
    created = SimpleNamespace(id=88, public_id="pub-88")
    bcli_svc.create.return_value = created

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    bcli_svc.update_by_public_id.assert_not_called()
    mapping_repo.create.assert_called_once_with(qbo_vendor_credit_line_id=qbo_line.id, bill_credit_line_item_id=88)
    assert result is created
    reconciliation_repo.create.assert_not_called()


def test_fast_path_update_returns_none_raises_runtime_error():
    connector, mapping_repo, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bcli_svc.read_by_qbo_identity.return_value = direct_hit
    bcli_svc.update_by_public_id.return_value = None
    mapping_repo.read_by_bill_credit_line_item_id.return_value = SimpleNamespace(id=1, qbo_vendor_credit_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_line_id.return_value = SimpleNamespace(id=1, bill_credit_line_item_id=55)

    with pytest.raises(RuntimeError, match="Failed to update BillCreditLineItem"):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    bcli_svc.repo.set_qbo_identity.assert_not_called()


def test_legacy_path_update_returns_none_also_raises_runtime_error():
    """Pre-U-293b, the legacy 'existing' branch had NO ROWVERSION-race guard at
    all (a None return would AttributeError on `line_item.id`) — U-293b's
    _apply_line_fields unification adds the same guard Bill/Expense already
    carried. Pin it explicitly on the legacy path here."""
    connector, mapping_repo, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None  # fast path misses
    existing_mapping = SimpleNamespace(id=1, bill_credit_line_item_id=55, qbo_vendor_credit_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_line_id.return_value = existing_mapping
    existing_line = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55")
    bcli_svc.read_by_id.return_value = existing_line
    bcli_svc.update_by_public_id.return_value = None

    with pytest.raises(RuntimeError, match="Failed to update BillCreditLineItem"):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")


def test_fast_path_miss_falls_back_to_legacy_mapping_table_path():
    connector, mapping_repo, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None
    existing_mapping = SimpleNamespace(id=1, bill_credit_line_item_id=55, qbo_vendor_credit_line_id=qbo_line.id)
    mapping_repo.read_by_qbo_line_id.return_value = existing_mapping
    existing_line = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55")
    bcli_svc.read_by_id.return_value = existing_line
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bcli_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    bcli_svc.read_by_qbo_identity.assert_called_once_with(19146, "1")
    assert result is updated
    bcli_svc.repo.set_qbo_identity.assert_called_once()


def test_fast_path_skipped_entirely_when_no_qbo_line_id():
    connector, mapping_repo, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id=None, description="Materials", amount=Decimal("250"))
    mapping_repo.read_by_qbo_line_id.return_value = None
    bcli_svc.read_by_bill_credit_id.return_value = []
    created = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.create.return_value = created

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    bcli_svc.read_by_qbo_identity.assert_not_called()
    assert result is created


def test_fast_path_skipped_entirely_when_no_staging_id():
    """qbo_line.id (the staging PK) is None — the connector's own defensive
    guard around the fast-path block, distinct from a falsy qbo_line_id."""
    connector, mapping_repo, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(id=None, qbo_line_id="1", description="Materials", amount=Decimal("250"))
    bcli_svc.read_by_bill_credit_id.return_value = []
    created = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.create.return_value = created

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    bcli_svc.read_by_qbo_identity.assert_not_called()
    assert result is created
    mapping_repo.create.assert_not_called()


def test_fast_path_direct_lookup_is_scoped_to_this_bill_credit_not_a_bare_qbo_id():
    connector, mapping_repo, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_line_id.return_value = None
    bcli_svc.read_by_bill_credit_id.return_value = []
    bcli_svc.create.return_value = SimpleNamespace(id=1, public_id="p")

    connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, realm_id="realm-1")

    bcli_svc.read_by_qbo_identity.assert_called_once_with(19146, "1")


def test_create_path_and_update_path_both_pass_enforce_realm_pairing_true():
    """U-293-dw debt #2 fold-in (per family): assert BOTH this connector's own
    call sites (create-path, update-path) actually pass
    enforce_realm_pairing=True to stamp_line_identity_or_warn, not just the
    shared function's isolated behavior (already covered in
    tests/test_qbo_identity_lines.py). Mirrors Bill's own equivalent test."""
    from unittest.mock import patch as _patch

    connector, mapping_repo, bcli_svc, _ = _build_connector()
    bcli_svc.read_by_qbo_identity.return_value = None  # fast path misses both times

    mapping_repo.read_by_qbo_line_id.return_value = None
    bcli_svc.read_by_bill_credit_id.return_value = []
    created = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.create.return_value = created

    existing_mapping = SimpleNamespace(id=1, bill_credit_line_item_id=77, qbo_vendor_credit_line_id=43)
    existing_line = SimpleNamespace(id=77, public_id="pub-77", row_version="rv-77")
    updated = SimpleNamespace(id=77, public_id="pub-77")

    with _patch(
        "integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service.stamp_line_identity_or_warn"
    ) as mock_stamp:
        qbo_line_create = _make_qbo_line(id=42, qbo_line_id="1")
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line_create, realm_id="realm-1")

        mapping_repo.read_by_qbo_line_id.return_value = existing_mapping
        bcli_svc.read_by_id.return_value = existing_line
        bcli_svc.update_by_public_id.return_value = updated
        qbo_line_update = _make_qbo_line(id=43, qbo_line_id="2")
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line_update, realm_id="realm-1")

    assert mock_stamp.call_count == 2
    for call in mock_stamp.call_args_list:
        assert call.kwargs.get("enforce_realm_pairing") is True
