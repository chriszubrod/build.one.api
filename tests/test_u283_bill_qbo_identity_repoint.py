"""Pure-logic tests for U-283 (Phase-4): repoint the `bill` connector family's
header identity resolution off qbo.Bill / qbo.BillBill onto dbo.Bill's native
QboId/RealmId (U-238a), via the shared base/identity_fastpath.py helper
(U-287) — no per-family copy of the state machine. Also covers the U-276 §10
prereq fold-in: bill_line_item's `_get_project_public_id` pull resolver tries
dbo.Project's native identity first.

Unlike U-276/277/278/279 (built before U-287), this connector calls
`run_identity_fastpath()`/`resolve_mapping_state()` directly — there is no
per-family `_resolve_mapping_state()` test-seam wrapper to test here, since
there is no pre-existing suite depending on that method name. The shared
helper's state machine is already exhaustively tested in
tests/test_u287_identity_fastpath_helper.py; these tests instead prove THIS
connector's wiring: the callbacks it hands the helper, and that a conflict
never writes to the dbo-identity-matched row.

Covers:
  1. BillRepository.read_by_qbo_identity (sproc call shape) + BillService's
     thin passthrough.
  2. BillBillConnector.sync_from_qbo_bill's fast path: consistent hit (update +
     SyncToken re-stamp, no mapping-table write), missing hit (self-heals a
     missing mapping row via mapping_repo.create directly, not via
     connector.create_mapping), conflict (hard stop — raises, records the
     issue, never writes to the conflicted Bill), miss (falls back to the
     pre-existing mapping-table path unchanged, reusing the same
     `_apply_bill_fields` closure).
  3. BillLineItemConnector._get_project_public_id: direct dbo.Project lookup
     tried first, legacy qbo.Customer->qbo.CustomerProject hop only on a miss
     or an unverified (conflicting) direct hit.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector


def _make_qbo_bill(**overrides):
    defaults = dict(
        id=4,
        qbo_id="BILL-99",
        realm_id="realm-1",
        vendor_ref_value="V-1",
        doc_number="INV-100",
        txn_date="2026-08-01",
        due_date="2026-08-31",
        private_note="memo",
        total_amt=100,
        sync_token="3",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_ONE_LINE = [SimpleNamespace(id=1)]


# --- Section 1: repo/service-level sproc call shape ---


def test_bill_repo_read_by_qbo_identity_calls_sproc():
    from entities.bill.persistence.repo import BillRepository

    repo = BillRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.bill.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.bill.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("BILL-99", "realm-1", actor_user_id=17, actor_is_system_admin=True)

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadBillByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {
        "QboId": "BILL-99",
        "RealmId": "realm-1",
        "ActorUserId": 17,
        "ActorIsSystemAdmin": 1,
    }


def test_bill_service_read_by_qbo_identity_threads_actor_scope():
    """Mirrors ProjectService's equivalent test — must NOT bypass RBAC scoping.
    Sets/resets the ContextVars explicitly rather than relying on their ambient
    default, since other tests in the suite leave them set (no autouse reset
    fixture exists)."""
    from entities.bill.business.service import BillService
    from shared.authz import current_is_system_admin, current_user_id

    repo = Mock()
    sentinel = SimpleNamespace(id=1)
    repo.read_by_qbo_identity.return_value = sentinel
    service = BillService(repo=repo)

    tok_u = current_user_id.set(7)
    tok_a = current_is_system_admin.set(True)
    try:
        result = service.read_by_qbo_identity("BILL-1", "realm-1")
    finally:
        current_user_id.reset(tok_u)
        current_is_system_admin.reset(tok_a)

    repo.read_by_qbo_identity.assert_called_once_with(
        "BILL-1", "realm-1", actor_user_id=7, actor_is_system_admin=True
    )
    assert result is sentinel


# --- Section 2: BillBillConnector fast path ---


def _build_bill_connector():
    mapping_repo = Mock()
    bill_service = Mock()
    bill_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = BillBillConnector(
        mapping_repo=mapping_repo,
        bill_service=bill_service,
        reconciliation_repo=reconciliation_repo,
    )
    # Out of scope for these tests — vendor resolution and line-item sync are
    # exercised elsewhere; stub them so header-identity behavior is isolated.
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector, mapping_repo, bill_service, reconciliation_repo


def test_bill_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_bill_connector()
    qbo_bill = _make_qbo_bill(id=4, qbo_id="BILL-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, bill_id=9, qbo_bill_id=4)
    local_side = SimpleNamespace(id=3, bill_id=55, qbo_bill_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_bill=qbo_bill, dbo_bill_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "bill_identity_conflict"
    # Phrase-level checks, not bare digit substrings — the always-emitted
    # first sentence's own "55"/"4"/"BILL-99" would trivially satisfy a plain
    # "in details" check even if the qbo-side/local-side blocks were dropped.
    assert "Bill 9 (mapping 2)" in kwargs["details"]      # qbo-side conflicting Bill
    assert "DIFFERENT QboBill 5" in kwargs["details"]     # local-side conflicting QboBill


def test_bill_raise_identity_mapping_conflict_issue_qbo_side_only():
    """Isolated qbo-side-only shape (local_side_mapping=None) — proves the
    qbo-side block alone produces its text and the local-side block is
    correctly skipped, not just that both substrings appear somewhere when
    both objects are supplied together."""
    connector, _, _, reconciliation_repo = _build_bill_connector()
    qbo_bill = _make_qbo_bill(id=4, qbo_id="BILL-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, bill_id=9, qbo_bill_id=4)

    connector._raise_identity_mapping_conflict_issue(
        qbo_bill=qbo_bill, dbo_bill_id=55,
        local_side_mapping=None, qbo_side_mapping=qbo_side,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "Bill 9 (mapping 2)" in kwargs["details"]
    assert "local-side" not in kwargs["details"]


def test_bill_raise_identity_mapping_conflict_issue_local_side_only():
    """Isolated local-side-only shape (qbo_side_mapping=None) — proves the
    local-side block alone produces its text and the qbo-side block is
    correctly skipped."""
    connector, _, _, reconciliation_repo = _build_bill_connector()
    qbo_bill = _make_qbo_bill(id=4, qbo_id="BILL-99", realm_id="realm-1")
    local_side = SimpleNamespace(id=3, bill_id=55, qbo_bill_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_bill=qbo_bill, dbo_bill_id=55,
        local_side_mapping=local_side, qbo_side_mapping=None,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "DIFFERENT QboBill 5" in kwargs["details"]
    assert "qbo-side" not in kwargs["details"]


def test_bill_fast_path_hit_conflict_raises_and_never_writes():
    connector, mapping_repo, bill_service, reconciliation_repo = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", bill_number="B-1", row_version="rv")
    bill_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_bill_id.return_value = None
    conflicting = SimpleNamespace(id=2, bill_id=9, qbo_bill_id=qbo_bill.id)
    mapping_repo.read_by_qbo_bill_id.return_value = conflicting
    # If the fast path fell through (it must not), these would let the legacy
    # branch reach and write Bill 9 or mint a duplicate.
    bill_service.read_by_id.return_value = SimpleNamespace(
        id=9, public_id="pub-9", bill_number="B-1", row_version="rv9"
    )
    bill_service.read_by_bill_number_and_vendor_public_id.return_value = None
    bill_service.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    bill_service.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any Bill on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    reconciliation_repo.create.assert_called_once()  # conflict recorded (durable follow-up)
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "bill_identity_conflict"
    bill_service.create.assert_not_called()  # NO duplicate Bill minted
    bill_service.repo.set_qbo_identity.assert_not_called()  # NO identity theft


def test_bill_fast_path_hit_consistent_refreshes_synctoken_skips_mapping_write():
    """Unlike Company/Address/Project, Bill carries SyncToken as part of its
    identity — the fast path's apply_fields must still re-stamp identity on a
    CONSISTENT hit (to refresh SyncToken), even though QboId/RealmId are
    already correct-by-construction. Only the mapping-table write is skipped."""
    connector, mapping_repo, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1", sync_token="7")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", bill_number="B-1", row_version="rv-55")
    bill_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bill_service.update_by_public_id.return_value = updated
    mapping_repo.read_by_bill_id.return_value = SimpleNamespace(id=1, qbo_bill_id=qbo_bill.id)
    mapping_repo.read_by_qbo_bill_id.return_value = SimpleNamespace(id=1, bill_id=55)

    result = connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    assert result is updated
    mapping_repo.create.assert_not_called()
    bill_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="BILL-99", realm_id="realm-1", sync_token="7"
    )
    connector._sync_line_items.assert_called_once()


def test_bill_fast_path_hit_missing_self_heals_via_mapping_repo_not_connector_create_mapping():
    """On MISSING, the mapping row must be created via mapping_repo.create(...)
    directly (bypassing BillBillConnector.create_mapping, which would
    redundantly re-stamp identity that the fast path already verified)."""
    connector, mapping_repo, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", bill_number="B-1", row_version="rv-55")
    bill_service.read_by_qbo_identity.return_value = direct_hit
    bill_service.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")
    mapping_repo.read_by_bill_id.return_value = None
    mapping_repo.read_by_qbo_bill_id.return_value = None

    connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    mapping_repo.create.assert_called_once_with(bill_id=55, qbo_bill_id=qbo_bill.id)
    # Exactly one stamp (from apply_fields' SyncToken refresh) — routing mapping
    # creation through the connector's OWN create_mapping() instead of
    # mapping_repo.create() directly would double-stamp identity redundantly.
    assert bill_service.repo.set_qbo_identity.call_count == 1


def test_bill_fast_path_self_heal_race_escalates_to_recorded_conflict():
    """A concurrent sync can turn 'missing' into 'conflict' between the
    pre-check and the create() call (no sp_getapplock serializes this — same
    known gap as every sibling family). The create() failure must not be a
    bare warning — re-check and record a real conflict issue when that's what
    actually happened. Mirrors every other Phase-4 family's own version of this
    test (e.g. test_u282_payment_term_qbo_identity_repoint.py)."""
    connector, mapping_repo, bill_service, reconciliation_repo = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", bill_number="B-1", row_version="rv-55")
    bill_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bill_service.update_by_public_id.return_value = updated
    mapping_repo.read_by_bill_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_bill_id.side_effect = [
        None, SimpleNamespace(id=9, bill_id=3, qbo_bill_id=qbo_bill.id)
    ]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    result = connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "bill_identity_conflict"
    # A regression that silently dropped the still-valid updated entity (e.g.
    # returning None instead) on this escalation path would pass a
    # call-count-only assertion.
    assert result is updated


def test_bill_fast_path_update_returns_none_raises_runtime_error():
    """ROWVERSION race: a concurrent writer touched the fast-path-matched Bill
    between the read and this UPDATE, so update_by_public_id() affects 0 rows
    and returns None. Must raise cleanly, not propagate a bare None onward.

    RuntimeError, deliberately NOT ValueError (U-291): a ROWVERSION race is
    transient, not a permanent data problem — record_projection_error's rule 2
    classifies a plain ValueError as a permanent SKIP, which would advance the
    watermark past this record anyway. Was ValueError pre-U-291; renamed from
    test_bill_fast_path_update_returns_none_raises_value_error."""
    connector, mapping_repo, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", bill_number="B-1", row_version="rv-55")
    bill_service.read_by_qbo_identity.return_value = direct_hit
    bill_service.update_by_public_id.return_value = None
    mapping_repo.read_by_bill_id.return_value = SimpleNamespace(id=1, qbo_bill_id=qbo_bill.id)
    mapping_repo.read_by_qbo_bill_id.return_value = SimpleNamespace(id=1, bill_id=55)

    with pytest.raises(RuntimeError, match="Failed to update Bill"):
        connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    bill_service.repo.set_qbo_identity.assert_not_called()


def test_bill_legacy_path_update_returns_none_raises_runtime_error():
    """The legacy "mapping found" branch (sync_from_qbo_bill's Step ~230) calls
    the SAME shared `_apply_bill_fields` closure the fast path uses — one fix
    covers both call sites by construction, but pin it explicitly since it's a
    genuinely different code path (proves no duplicated/diverging update logic
    reintroduces the gap, mirroring
    test_bill_fast_path_miss_falls_back_to_legacy_mapping_table_path's setup)."""
    connector, mapping_repo, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    bill_service.read_by_qbo_identity.return_value = None  # fast path misses
    existing_mapping = SimpleNamespace(id=1, bill_id=55, qbo_bill_id=qbo_bill.id)
    mapping_repo.read_by_qbo_bill_id.return_value = existing_mapping
    existing_bill = SimpleNamespace(id=55, public_id="pub-55", bill_number="B-1", row_version="rv-55")
    bill_service.read_by_id.return_value = existing_bill
    bill_service.update_by_public_id.return_value = None

    with pytest.raises(RuntimeError, match="Failed to update Bill"):
        connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)


def test_bill_fast_path_miss_falls_back_to_legacy_mapping_table_path():
    """No dbo row carries this identity yet -> the pre-existing mapping-table-
    based logic must still run, reusing the SAME `_apply_bill_fields` closure
    (proving no duplicated/diverging update logic between the two paths)."""
    connector, mapping_repo, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    bill_service.read_by_qbo_identity.return_value = None
    existing_mapping = SimpleNamespace(id=1, bill_id=55, qbo_bill_id=qbo_bill.id)
    mapping_repo.read_by_qbo_bill_id.return_value = existing_mapping
    existing_bill = SimpleNamespace(id=55, public_id="pub-55", bill_number="B-1", row_version="rv-55")
    bill_service.read_by_id.return_value = existing_bill
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bill_service.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    bill_service.read_by_qbo_identity.assert_called_once_with("BILL-99", "realm-1")
    assert result is updated
    bill_service.repo.set_qbo_identity.assert_called_once()  # legacy path still stamps identity


def test_bill_fast_path_skipped_entirely_when_no_qbo_id():
    """A record with no external qbo_id can't possibly have a dbo-native
    identity match — the fast-path lookup should not even be attempted."""
    connector, mapping_repo, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id=None)
    mapping_repo.read_by_qbo_bill_id.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    bill_service.create.return_value = created

    result = connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    bill_service.read_by_qbo_identity.assert_not_called()
    assert result is created


# --- Section 3: BillLineItemConnector._get_project_public_id resolver fold-in ---


def _build_bill_line_item_connector():
    from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import (
        BillLineItemConnector,
    )

    project_service = Mock()
    qbo_customer_repo = Mock()
    customer_project_repo = Mock()
    connector = BillLineItemConnector(
        project_service=project_service,
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )
    return connector, project_service, qbo_customer_repo, customer_project_repo


def test_get_project_public_id_prefers_direct_dbo_lookup():
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_bill_line_item_connector()
    direct_project = SimpleNamespace(id=10, public_id="proj-pub-10", qbo_id="CUST-1", name="Acme")
    project_service.read_by_qbo_identity.return_value = direct_project
    # No CustomerProject mapping row yet -> verify_project_qbo_identity trusts it.
    customer_project_repo.read_by_project_id.return_value = None

    result = connector._get_project_public_id("CUST-1", "realm-1")

    assert result == "proj-pub-10"
    project_service.read_by_qbo_identity.assert_called_once_with("CUST-1", "realm-1")
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()
    qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_get_project_public_id_falls_back_when_direct_lookup_misses():
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_bill_line_item_connector()
    project_service.read_by_qbo_identity.return_value = None
    qbo_customer = SimpleNamespace(id=20)
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = qbo_customer
    customer_project_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(project_id=30)
    project_service.read_by_id.return_value = SimpleNamespace(id=30, public_id="proj-pub-30")

    result = connector._get_project_public_id("CUST-2", "realm-1")

    assert result == "proj-pub-30"
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with("CUST-2", "realm-1")


def test_get_project_public_id_falls_back_when_direct_hit_fails_verification():
    """The direct dbo.Project hit exists, but its OWN CustomerProject mapping
    disagrees (a stale/"stolen" identity) — must not trust it blindly; falls
    back to the legacy hop rather than misattributing the line to the wrong
    project."""
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_bill_line_item_connector()
    direct_project = SimpleNamespace(id=10, public_id="proj-pub-10", qbo_id="CUST-1", name="Acme")
    project_service.read_by_qbo_identity.return_value = direct_project
    # Local-side mapping disagrees: Project 10 maps to a DIFFERENT QboCustomer.
    customer_project_repo.read_by_project_id.return_value = SimpleNamespace(qbo_customer_id=999)
    conflicting_qbo_customer = SimpleNamespace(qbo_id="CUST-OTHER")
    qbo_customer_repo.read_by_id.return_value = conflicting_qbo_customer

    # Legacy hop takes over from here.
    qbo_customer = SimpleNamespace(id=20)
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = qbo_customer
    customer_project_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(project_id=30)
    project_service.read_by_id.return_value = SimpleNamespace(id=30, public_id="proj-pub-30")

    result = connector._get_project_public_id("CUST-1", "realm-1")

    assert result == "proj-pub-30"  # legacy hop's answer, NOT the unverified direct hit
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with("CUST-1", "realm-1")


def test_get_project_public_id_caches_per_realm_and_customer_ref():
    """A Bill's lines commonly share one job/customer_ref_value — the second
    lookup for the same (realm_id, qbo_customer_ref_value) must be served from
    cache, not re-resolved."""
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_bill_line_item_connector()
    direct_project = SimpleNamespace(id=10, public_id="proj-pub-10", qbo_id="CUST-1", name="Acme")
    project_service.read_by_qbo_identity.return_value = direct_project
    customer_project_repo.read_by_project_id.return_value = None

    first = connector._get_project_public_id("CUST-1", "realm-1")
    second = connector._get_project_public_id("CUST-1", "realm-1")

    assert first == second == "proj-pub-10"
    project_service.read_by_qbo_identity.assert_called_once_with("CUST-1", "realm-1")

    # A different realm is a different cache key — must resolve independently.
    project_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=20, public_id="proj-pub-20", qbo_id="CUST-1", name="Other"
    )
    third = connector._get_project_public_id("CUST-1", "realm-2")
    assert third == "proj-pub-20"
    assert project_service.read_by_qbo_identity.call_count == 2
