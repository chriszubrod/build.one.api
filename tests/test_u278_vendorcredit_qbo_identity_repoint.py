"""Pure-logic tests for U-278 (Phase-4, header/reference repoint): repoint the
`vendorcredit` connector family's HEADER identity resolution off qbo.VendorCredit /
qbo.VendorCreditBillCredit onto dbo.BillCredit's native QboId/RealmId (Phase 2), plus
the line connector's `_get_project_public_id` pull-resolver repoint onto
dbo.Project.QboId/RealmId (the U-276 §10-deferred prereq, read-only, no write side).

Line-level fingerprint staging (qbo.VendorCreditLine matching) is explicitly OUT of
scope for this unit — see docs/staging_removal_phase4_5_scoping.md §2/§7/§8 item 4.

Covers:
  1. BillCreditRepository.read_by_qbo_identity (sproc call shape) + BillCreditService
     .read_by_qbo_identity (unlike Customer's thin passthrough, BillCredit checks
     row-level access like its read_by_id/read_by_public_id siblings).
  2. VendorCreditBillCreditConnector's new direct-identity fast path: hit updates
     without the mapping-table hop + self-heals a missing mapping row; miss falls
     through to the pre-existing mapping-table path unchanged. Mirrors
     test_u276_customer_project_qbo_identity_repoint.py's Section 2 shape.
  3. VendorCreditLineItemConnector._get_project_public_id now tries the direct
     dbo.Project.QboId/RealmId lookup (built by U-276) before the legacy
     qbo.Customer -> qbo.CustomerProject hop.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
    VendorCreditBillCreditConnector,
)

VC_SERVICE = "integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service"


def _make_qbo_vc(**overrides):
    defaults = dict(
        id=30,
        qbo_id="VC-99",
        realm_id="realm-1",
        vendor_ref_value="1",
        doc_number="VC-99",
        txn_date="2026-01-01",
        total_amt=None,
        private_note="note",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- Section 1: repo-level sproc call shape ---


def test_bill_credit_repo_read_by_qbo_identity_calls_sproc():
    from entities.bill_credit.persistence.repo import BillCreditRepository

    repo = BillCreditRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.bill_credit.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.bill_credit.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("VC-99", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadBillCreditByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "VC-99", "RealmId": "realm-1"}


def test_bill_credit_service_read_by_qbo_identity_checks_access():
    """Unlike Customer (no row-level RBAC), BillCredit's read_by_id/read_by_public_id
    both gate on assert_can_access_bill_credit — the new method must match that
    convention, not the Customer template's bare passthrough."""
    from entities.bill_credit.business.service import BillCreditService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = SimpleNamespace(id=55)
    service = BillCreditService(repo=repo)

    with patch("entities.bill_credit.business.service.assert_can_access_bill_credit") as mock_assert:
        result = service.read_by_qbo_identity("VC-99", "realm-1")

    repo.read_by_qbo_identity.assert_called_once_with("VC-99", "realm-1")
    mock_assert.assert_called_once_with(55)
    assert result.id == 55


def test_bill_credit_service_read_by_qbo_identity_none_skips_access_check():
    from entities.bill_credit.business.service import BillCreditService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = None
    service = BillCreditService(repo=repo)

    with patch("entities.bill_credit.business.service.assert_can_access_bill_credit") as mock_assert:
        result = service.read_by_qbo_identity("VC-99", "realm-1")

    assert result is None
    mock_assert.assert_not_called()


# --- Section 2: VendorCreditBillCreditConnector fast path ---
#
# Same testing shape as test_u276_customer_project_qbo_identity_repoint.py's Section 2
# — see its header comment for why conflict cases are unit-tested directly against
# _resolve_mapping_state / _raise_identity_mapping_conflict_issue rather than only
# through the full sync_from_qbo_vendor_credit().


def _build_bill_credit_connector():
    mapping_repo = Mock()
    bill_credit_service = Mock()
    bill_credit_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = VendorCreditBillCreditConnector(
        mapping_repo=mapping_repo,
        bill_credit_service=bill_credit_service,
        bill_credit_line_item_service=Mock(),
        vendor_service=Mock(),
        reconciliation_repo=reconciliation_repo,
    )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector, mapping_repo, bill_credit_service, reconciliation_repo


def test_resolve_mapping_state_consistent():
    connector, mapping_repo, _, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30)
    mapping_repo.read_by_bill_credit_id.return_value = SimpleNamespace(id=1, qbo_vendor_credit_id=30)
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = SimpleNamespace(id=1, bill_credit_id=55)

    state, _, _ = connector._resolve_mapping_state(bill_credit_id=55, qbo_vc=qbo_vc)

    assert state == "consistent"


def test_resolve_mapping_state_missing():
    connector, mapping_repo, _, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30)
    mapping_repo.read_by_bill_credit_id.return_value = None
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None

    state, _, _ = connector._resolve_mapping_state(bill_credit_id=55, qbo_vc=qbo_vc)

    assert state == "missing"


def test_resolve_mapping_state_qbo_side_conflict():
    connector, mapping_repo, _, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30)
    mapping_repo.read_by_bill_credit_id.return_value = None
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = SimpleNamespace(id=2, bill_credit_id=9)

    state, by_bill_credit, by_qbo_vc = connector._resolve_mapping_state(bill_credit_id=55, qbo_vc=qbo_vc)

    assert state == "conflict"
    assert by_bill_credit is None
    assert by_qbo_vc.bill_credit_id == 9


def test_resolve_mapping_state_local_side_conflict():
    connector, mapping_repo, _, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30)
    mapping_repo.read_by_bill_credit_id.return_value = SimpleNamespace(id=3, qbo_vendor_credit_id=5)
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None

    state, by_bill_credit, by_qbo_vc = connector._resolve_mapping_state(bill_credit_id=55, qbo_vc=qbo_vc)

    assert state == "conflict"
    assert by_bill_credit.qbo_vendor_credit_id == 5
    assert by_qbo_vc is None


def test_resolve_mapping_state_two_row_crossed_conflict():
    connector, mapping_repo, _, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30)
    mapping_repo.read_by_bill_credit_id.return_value = SimpleNamespace(id=3, qbo_vendor_credit_id=5)
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = SimpleNamespace(id=2, bill_credit_id=9)

    state, by_bill_credit, by_qbo_vc = connector._resolve_mapping_state(bill_credit_id=55, qbo_vc=qbo_vc)

    assert state == "conflict"
    assert by_bill_credit.qbo_vendor_credit_id == 5
    assert by_qbo_vc.bill_credit_id == 9


def test_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, bill_credit_id=9, qbo_vendor_credit_id=30)
    local_side = SimpleNamespace(id=3, bill_credit_id=55, qbo_vendor_credit_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_vc=qbo_vc, dbo_bill_credit_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "vendorcredit_identity_conflict"
    assert "55" in kwargs["details"]  # the dbo-identity-matched BillCredit
    assert "9" in kwargs["details"]   # the qbo-side conflicting BillCredit
    assert "5" in kwargs["details"]   # the local-side conflicting QboVendorCredit


def test_fast_path_conflict_qbo_side_raises_and_writes_nothing():
    """On a detected qbo-side conflict, sync_from_qbo_vendor_credit must record the
    issue and RAISE — never fall through to the legacy mapping-table path. Falling
    through would update the CONFLICTING BillCredit (9) and then call
    set_qbo_identity(qbo_id=qbo_vc.qbo_id, ...) on it — SetBillCreditQboIdentity's own
    theft-detection UPDATE applies against ANY row already carrying that
    (QboId, RealmId), which is exactly `direct` (55). That would silently NULL 55's
    identity in the same call that just logged 'not auto-repointed'. Confirmed bug
    class found by code review; this test locks in the fix."""
    connector, mapping_repo, bill_credit_service, reconciliation_repo = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="bc-pub-55", credit_number="VC-OLD", row_version="rv55")
    bill_credit_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_bill_credit_id.return_value = None
    conflicting = SimpleNamespace(id=2, bill_credit_id=9, qbo_vendor_credit_id=qbo_vc.id)
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = conflicting

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        with pytest.raises(ValueError, match="identity conflict"):
            connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    reconciliation_repo.create.assert_called_once()
    bill_credit_service.update_by_public_id.assert_not_called()
    bill_credit_service.create.assert_not_called()
    bill_credit_service.repo.set_qbo_identity.assert_not_called()
    # _resolve_mapping_state's conflict branch is the ONLY caller of this — the raise
    # means Step 2 is structurally unreachable from a conflict, so this can never be 2.
    mapping_repo.read_by_qbo_vendor_credit_id.assert_called_once_with(30)


def test_fast_path_conflict_local_side_only_raises_no_duplicate_create():
    """A 'local-side-only' conflict (direct match exists, but no mapping row binds
    this qbo_vc.id to anything — by_qbo_vc is None) must ALSO raise, not fall through
    to Step 3 CREATE. Falling through would mint a duplicate BillCredit for a
    transaction `direct` already represents, then steal `direct`'s identity via the
    new duplicate's own set_qbo_identity call. Confirmed bug class found by code
    review; this test locks in the fix."""
    connector, mapping_repo, bill_credit_service, reconciliation_repo = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="bc-pub-55", credit_number="VC-OLD", row_version="rv55")
    bill_credit_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_bill_credit_id.return_value = SimpleNamespace(id=3, qbo_vendor_credit_id=5)
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        with pytest.raises(ValueError, match="identity conflict"):
            connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    reconciliation_repo.create.assert_called_once()
    bill_credit_service.create.assert_not_called()
    bill_credit_service.update_by_public_id.assert_not_called()
    mapping_repo.create.assert_not_called()


def test_fast_path_missing_write_race_returns_none_without_crashing():
    """Codex-fallback review finding: if `direct` is deleted between
    read_by_qbo_identity and the write (BillCreditService.update_by_public_id
    re-reads internally and returns None rather than raising), the 'missing'-mapping
    branch must not crash trying coerce_id(None.id) — nor crash AGAIN inside its own
    except-handler's log statement, which referenced the same None."""
    connector, mapping_repo, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="bc-pub-55", credit_number="VC-99", row_version="rv55")
    bill_credit_service.read_by_qbo_identity.return_value = direct_hit
    bill_credit_service.update_by_public_id.return_value = None  # race: row gone on write
    mapping_repo.read_by_bill_credit_id.return_value = None
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        result = connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    assert result is None
    mapping_repo.create.assert_not_called()


def test_fast_path_hit_self_heals_missing_mapping():
    connector, mapping_repo, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="bc-pub-55", credit_number="VC-99", row_version="rv55")
    bill_credit_service.read_by_qbo_identity.return_value = direct_hit
    bill_credit_service.update_by_public_id.return_value = direct_hit
    mapping_repo.read_by_bill_credit_id.return_value = None  # mapping missing on this side...
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None  # ...and no conflicting mapping either

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    mapping_repo.create.assert_called_once_with(qbo_vendor_credit_id=30, bill_credit_id=55)


def test_fast_path_self_heal_race_escalates_to_recorded_conflict():
    """A concurrent sync can turn 'missing' into 'conflict' between the pre-check and
    the create() call (no sp_getapplock serializes this — same known gap as U-276).
    The create() failure must not just be a bare warning — re-check and record a real
    conflict issue when that's what actually happened."""
    connector, mapping_repo, bill_credit_service, reconciliation_repo = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="bc-pub-55", credit_number="VC-99", row_version="rv55")
    bill_credit_service.read_by_qbo_identity.return_value = direct_hit
    bill_credit_service.update_by_public_id.return_value = direct_hit
    mapping_repo.read_by_bill_credit_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_vendor_credit_id.side_effect = [
        None, SimpleNamespace(id=9, bill_credit_id=3, qbo_vendor_credit_id=qbo_vc.id)
    ]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "vendorcredit_identity_conflict"


def test_fast_path_hit_consistent_skips_mapping_write_and_identity_restamp():
    connector, mapping_repo, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1", doc_number="VC-99")
    direct_hit = SimpleNamespace(id=55, public_id="bc-pub-55", credit_number="VC-99", row_version="rv55")
    bill_credit_service.read_by_qbo_identity.return_value = direct_hit
    bill_credit_service.update_by_public_id.return_value = direct_hit
    mapping_repo.read_by_bill_credit_id.return_value = SimpleNamespace(id=1, qbo_vendor_credit_id=30)
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = SimpleNamespace(id=1, bill_credit_id=55)

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        result = connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    assert result is direct_hit
    mapping_repo.create.assert_not_called()
    bill_credit_service.create.assert_not_called()
    # Identity is already correct by construction on the fast path — must not re-stamp
    # (the row was found BY that exact identity; re-stamping is a wasted round trip on
    # the steady-state path this feature exists to keep cheap — mirrors U-276's Project
    # fast path, which carries the same assertion).
    bill_credit_service.repo.set_qbo_identity.assert_not_called()


def test_fast_path_miss_falls_back_to_mapping_table_path():
    connector, mapping_repo, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    bill_credit_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None
    created = SimpleNamespace(id=77, public_id="bc-pub-77")
    bill_credit_service.create.return_value = created

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        result = connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    bill_credit_service.read_by_qbo_identity.assert_called_once_with("VC-99", "realm-1")
    assert result is created
    bill_credit_service.create.assert_called_once()


def test_fast_path_skipped_entirely_when_no_qbo_id():
    """A record with no external qbo_id can't possibly have a dbo-native identity
    match — the fast-path lookup should not even be attempted."""
    connector, mapping_repo, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id=None)
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None
    bill_credit_service.create.return_value = SimpleNamespace(id=1, public_id="bc-pub-1")

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    bill_credit_service.read_by_qbo_identity.assert_not_called()


def test_legacy_path_still_stamps_identity_after_apply():
    """Regression coverage for the shared-helper refactor: set_qbo_identity was moved
    OUT of _apply_bill_credit_fields_and_sync (the fast path must not call it — see
    above), so the legacy mapping-table UPDATE path must call it itself afterward,
    since a mapping-table-matched row may predate identity stamping."""
    connector, mapping_repo, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    bill_credit_service.read_by_qbo_identity.return_value = None  # no dbo identity yet
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = SimpleNamespace(id=1, bill_credit_id=55)
    stored_bc = SimpleNamespace(id=55, public_id="bc-pub-55", credit_number="VC-99", row_version="rv55")
    bill_credit_service.read_by_id.return_value = stored_bc
    bill_credit_service.update_by_public_id.return_value = stored_bc

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    bill_credit_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="VC-99", realm_id="realm-1"
    )


# --- Section 3: VendorCreditLineItemConnector._get_project_public_id repoint ---

QBO_CUSTOMER_REPO_PATH = "integrations.intuit.qbo.customer.persistence.repo.QboCustomerRepository"
CUSTOMER_PROJECT_REPO_PATH = (
    "integrations.intuit.qbo.customer.connector.project.persistence.repo.CustomerProjectRepository"
)


def _build_line_connector():
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
        VendorCreditLineItemConnector,
    )

    connector = VendorCreditLineItemConnector()
    connector.project_service = Mock()
    return connector


def test_line_get_project_public_id_direct_hit_skips_legacy_lookup():
    connector = _build_line_connector()
    project = SimpleNamespace(id=42, public_id="proj-pub-42")
    connector.project_service.read_by_qbo_identity.return_value = project

    with patch(QBO_CUSTOMER_REPO_PATH) as mock_qbo_customer_repo_cls:
        result = connector._get_project_public_id("QBO-100", "realm-1")

    assert result == "proj-pub-42"
    connector.project_service.read_by_qbo_identity.assert_called_once_with("QBO-100", "realm-1")
    mock_qbo_customer_repo_cls.assert_not_called()


def test_line_get_project_public_id_direct_miss_falls_back_to_legacy():
    connector = _build_line_connector()
    connector.project_service.read_by_qbo_identity.return_value = None
    project = SimpleNamespace(id=42, public_id="proj-pub-42")
    connector.project_service.read_by_id.return_value = project

    qbo_customer = SimpleNamespace(id=4)
    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(project_id=42)

    with patch(QBO_CUSTOMER_REPO_PATH, return_value=qbo_customer_repo), patch(
        CUSTOMER_PROJECT_REPO_PATH, return_value=customer_project_repo
    ):
        result = connector._get_project_public_id("QBO-100", "realm-1")

    connector.project_service.read_by_qbo_identity.assert_called_once_with("QBO-100", "realm-1")
    assert result == "proj-pub-42"
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with("QBO-100", "realm-1")


def test_line_get_project_public_id_no_customer_ref_value_short_circuits():
    connector = _build_line_connector()
    assert connector._get_project_public_id("") is None
    connector.project_service.read_by_qbo_identity.assert_not_called()


def test_line_get_project_public_id_memoizes_per_connector_lifetime():
    """A VendorCredit's lines commonly repeat the same CustomerRef — the resolver
    must not pay a fresh round trip per line for an identical (ref, realm) key
    (efficiency finding from code review, mirrors the existing
    _sub_cost_code_cache pattern in this same class)."""
    connector = _build_line_connector()
    project = SimpleNamespace(id=42, public_id="proj-pub-42")
    connector.project_service.read_by_qbo_identity.return_value = project

    first = connector._get_project_public_id("QBO-100", "realm-1")
    second = connector._get_project_public_id("QBO-100", "realm-1")

    assert first == second == "proj-pub-42"
    connector.project_service.read_by_qbo_identity.assert_called_once_with("QBO-100", "realm-1")


def test_line_get_project_public_id_cache_keyed_by_realm_too():
    """A different realm_id for the same customer ref must NOT hit the same cache
    entry — multi-realm is a real (if currently narrow) case this connector already
    threads through realm_id everywhere else."""
    connector = _build_line_connector()
    project_a = SimpleNamespace(id=1, public_id="proj-pub-a")
    project_b = SimpleNamespace(id=2, public_id="proj-pub-b")
    connector.project_service.read_by_qbo_identity.side_effect = [project_a, project_b]

    result_a = connector._get_project_public_id("QBO-100", "realm-1")
    result_b = connector._get_project_public_id("QBO-100", "realm-2")

    assert result_a == "proj-pub-a"
    assert result_b == "proj-pub-b"
    assert connector.project_service.read_by_qbo_identity.call_count == 2
