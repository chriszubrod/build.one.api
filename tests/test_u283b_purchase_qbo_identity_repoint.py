"""Pure-logic tests for U-283b (Phase-4): repoint the `purchase` connector
family's header identity resolution off qbo.Purchase / qbo.PurchaseExpense
onto dbo.Expense's native QboId/RealmId (U-238a), via the shared
base/identity_fastpath.py helper (U-287) — no per-family copy of the state
machine. Also covers the U-276 §10 prereq fold-in: the purchase line
connector's `_get_project_public_id` pull resolver tries dbo.Project's
native identity first.

Mirrors tests/test_u283_bill_qbo_identity_repoint.py exactly (Bill is the
sibling family — both carry SyncToken as part of their identity, unlike
Company/Address/Project). qbo.Purchase/qbo.PurchaseLine remain a documented
read-only audit mirror for the expense-coding cockpit's
`PurchaseExpenseConnector.recode_purchase_line` (untouched by this unit,
Chris's 2026-08-20 decision) — these tests only cover the identity-resolution
seam this unit repointed.

Covers:
  1. ExpenseRepository.read_by_qbo_identity (sproc call shape) + ExpenseService's
     thin passthrough.
  2. PurchaseExpenseConnector.sync_from_qbo_purchase's fast path: consistent
     hit (update + SyncToken re-stamp, no mapping-table write), missing hit
     (self-heals a missing mapping row via mapping_repo.create directly, not
     via connector.create_mapping), conflict (hard stop — raises, records the
     issue, never writes to the conflicted Expense), miss (falls back to the
     pre-existing mapping-table path unchanged, reusing the same
     `_apply_expense_fields` closure).
  3. PurchaseLineExpenseLineItemConnector._get_project_public_id: direct
     dbo.Project lookup tried first, legacy qbo.Customer->qbo.CustomerProject
     hop only on a miss or an unverified (conflicting) direct hit.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
)

# Matches the sibling QBO connector test files' own convention (e.g.
# test_u302_invoice_rollback_race.py) — makes the bare `from conftest import ...`
# below resolve when this file is collected standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import mock_qbo_app_lock_granted as _granted_lock  # U-304 lock grant

LINE_CONNECTOR_PATH = (
    "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
    ".PurchaseLineExpenseLineItemConnector"
)


def _make_qbo_purchase(**overrides):
    defaults = dict(
        id=4,
        qbo_id="PURCH-99",
        realm_id="realm-1",
        entity_ref_value="V-1",
        doc_number="INV-100",
        txn_date="2026-08-01",
        private_note="memo",
        total_amt=100,
        credit=False,
        sync_token="3",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_ONE_LINE = [SimpleNamespace(id=1)]


# --- Section 1: repo/service-level sproc call shape ---


def test_expense_repo_read_by_qbo_identity_calls_sproc():
    from entities.expense.persistence.repo import ExpenseRepository

    repo = ExpenseRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.expense.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.expense.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("PURCH-99", "realm-1", actor_user_id=17, actor_is_system_admin=True)

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadExpenseByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {
        "QboId": "PURCH-99",
        "RealmId": "realm-1",
        "ActorUserId": 17,
        "ActorIsSystemAdmin": 1,
    }


def test_expense_service_read_by_qbo_identity_threads_actor_scope():
    """Mirrors BillService's equivalent test — must NOT bypass RBAC scoping.
    Sets/resets the ContextVars explicitly rather than relying on their ambient
    default, since other tests in the suite leave them set (no autouse reset
    fixture exists)."""
    from entities.expense.business.service import ExpenseService
    from shared.authz import current_is_system_admin, current_user_id

    repo = Mock()
    sentinel = SimpleNamespace(id=1)
    repo.read_by_qbo_identity.return_value = sentinel
    service = ExpenseService(repo=repo)

    tok_u = current_user_id.set(7)
    tok_a = current_is_system_admin.set(True)
    try:
        result = service.read_by_qbo_identity("PURCH-1", "realm-1")
    finally:
        current_user_id.reset(tok_u)
        current_is_system_admin.reset(tok_a)

    repo.read_by_qbo_identity.assert_called_once_with(
        "PURCH-1", "realm-1", actor_user_id=7, actor_is_system_admin=True
    )
    assert result is sentinel


# --- Section 2: PurchaseExpenseConnector fast path ---


def _build_purchase_connector():
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
    # Out of scope for these tests — vendor resolution and line-item sync are
    # exercised elsewhere; stub them so header-identity behavior is isolated.
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector, mapping_repo, expense_service, reconciliation_repo


def test_expense_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(id=4, qbo_id="PURCH-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, expense_id=9, qbo_purchase_id=4)
    local_side = SimpleNamespace(id=3, expense_id=55, qbo_purchase_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_purchase=qbo_purchase, dbo_expense_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "expense_identity_conflict"
    # Phrase-level checks, not bare digit substrings — the always-emitted
    # first sentence's own "55"/"4"/"PURCH-99" would trivially satisfy a plain
    # "in details" check even if the qbo-side/local-side blocks were dropped.
    assert "Expense 9 (mapping 2)" in kwargs["details"]         # qbo-side conflicting Expense
    assert "DIFFERENT QboPurchase 5" in kwargs["details"]       # local-side conflicting QboPurchase


def test_expense_raise_identity_mapping_conflict_issue_qbo_side_only():
    """Isolated qbo-side-only shape (local_side_mapping=None) — proves the
    qbo-side block alone produces its text and the local-side block is
    correctly skipped, not just that both substrings appear somewhere when
    both objects are supplied together."""
    connector, _, _, reconciliation_repo = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(id=4, qbo_id="PURCH-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, expense_id=9, qbo_purchase_id=4)

    connector._raise_identity_mapping_conflict_issue(
        qbo_purchase=qbo_purchase, dbo_expense_id=55,
        local_side_mapping=None, qbo_side_mapping=qbo_side,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "Expense 9 (mapping 2)" in kwargs["details"]
    assert "local-side" not in kwargs["details"]


def test_expense_raise_identity_mapping_conflict_issue_local_side_only():
    """Isolated local-side-only shape (qbo_side_mapping=None) — proves the
    local-side block alone produces its text and the qbo-side block is
    correctly skipped."""
    connector, _, _, reconciliation_repo = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(id=4, qbo_id="PURCH-99", realm_id="realm-1")
    local_side = SimpleNamespace(id=3, expense_id=55, qbo_purchase_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_purchase=qbo_purchase, dbo_expense_id=55,
        local_side_mapping=local_side, qbo_side_mapping=None,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "DIFFERENT QboPurchase 5" in kwargs["details"]
    assert "qbo-side" not in kwargs["details"]


def test_expense_fast_path_hit_conflict_raises_and_never_writes():
    connector, mapping_repo, expense_service, reconciliation_repo = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv")
    expense_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_expense_id.return_value = None
    conflicting = SimpleNamespace(id=2, expense_id=9, qbo_purchase_id=qbo_purchase.id)
    mapping_repo.read_by_qbo_purchase_id.return_value = conflicting
    # If the fast path fell through (it must not), these would let the legacy
    # branch reach and write Expense 9 or mint a duplicate.
    expense_service.read_by_id.return_value = SimpleNamespace(
        id=9, public_id="pub-9", reference_number="R-1", row_version="rv9"
    )
    expense_service.read_by_reference_number_and_vendor_public_id.return_value = None
    expense_service.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    expense_service.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any Expense on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    reconciliation_repo.create.assert_called_once()  # conflict recorded (durable follow-up)
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "expense_identity_conflict"
    expense_service.create.assert_not_called()  # NO duplicate Expense minted
    expense_service.repo.set_qbo_identity.assert_not_called()  # NO identity theft


def test_expense_fast_path_hit_consistent_refreshes_synctoken_skips_mapping_write():
    """Unlike Company/Address/Project, Expense carries SyncToken as part of its
    identity (mirrors Bill) — the fast path's apply_fields must still re-stamp
    identity on a CONSISTENT hit (to refresh SyncToken), even though
    QboId/RealmId are already correct-by-construction. Only the mapping-table
    write is skipped."""
    connector, mapping_repo, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1", sync_token="7")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    expense_service.update_by_public_id.return_value = updated
    mapping_repo.read_by_expense_id.return_value = SimpleNamespace(id=1, qbo_purchase_id=qbo_purchase.id)
    mapping_repo.read_by_qbo_purchase_id.return_value = SimpleNamespace(id=1, expense_id=55)

    result = connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    assert result is updated
    mapping_repo.create.assert_not_called()
    expense_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="PURCH-99", realm_id="realm-1", sync_token="7"
    )
    connector._sync_line_items.assert_called_once()


@patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", _granted_lock)
def test_expense_fast_path_hit_missing_self_heals_via_mapping_repo_not_connector_create_mapping():
    """On MISSING, the mapping row must be created via mapping_repo.create(...)
    directly (bypassing PurchaseExpenseConnector.create_mapping, which would
    redundantly re-stamp identity that the fast path already verified)."""
    connector, mapping_repo, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_qbo_identity.return_value = direct_hit
    expense_service.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")
    mapping_repo.read_by_expense_id.return_value = None
    mapping_repo.read_by_qbo_purchase_id.return_value = None

    connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    mapping_repo.create.assert_called_once_with(expense_id=55, qbo_purchase_id=qbo_purchase.id)
    # Exactly one stamp (from apply_fields' SyncToken refresh) — routing mapping
    # creation through the connector's OWN create_mapping() instead of
    # mapping_repo.create() directly would double-stamp identity redundantly.
    assert expense_service.repo.set_qbo_identity.call_count == 1


@patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", _granted_lock)
def test_expense_fast_path_self_heal_race_escalates_to_recorded_conflict():
    """A concurrent sync can turn 'missing' into 'conflict' between the
    pre-check and the create() call (no sp_getapplock serializes this — same
    known gap as every sibling family). The create() failure must not be a
    bare warning — re-check and record a real conflict issue when that's what
    actually happened. Mirrors every other Phase-4 family's own version of this
    test (e.g. test_u283_bill_qbo_identity_repoint.py)."""
    connector, mapping_repo, expense_service, reconciliation_repo = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    expense_service.update_by_public_id.return_value = updated
    mapping_repo.read_by_expense_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_purchase_id.side_effect = [
        None, SimpleNamespace(id=9, expense_id=3, qbo_purchase_id=qbo_purchase.id)
    ]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    result = connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "expense_identity_conflict"
    # A regression that silently dropped the still-valid updated entity (e.g.
    # returning None instead) on this escalation path would pass a
    # call-count-only assertion.
    assert result is updated


def test_expense_fast_path_update_returns_none_raises_runtime_error():
    """ROWVERSION race: a concurrent writer touched the fast-path-matched
    Expense between the read and this UPDATE, so update_by_public_id() affects
    0 rows and returns None. Must raise cleanly, not propagate a bare None
    onward. RuntimeError, deliberately NOT ValueError (U-291): a ROWVERSION
    race is transient, not a permanent data problem — record_projection_error's
    rule 2 classifies a plain ValueError as a permanent SKIP, which would
    advance the watermark past this record anyway."""
    connector, mapping_repo, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_qbo_identity.return_value = direct_hit
    expense_service.update_by_public_id.return_value = None
    mapping_repo.read_by_expense_id.return_value = SimpleNamespace(id=1, qbo_purchase_id=qbo_purchase.id)
    mapping_repo.read_by_qbo_purchase_id.return_value = SimpleNamespace(id=1, expense_id=55)

    with pytest.raises(RuntimeError, match="Failed to update Expense"):
        connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    expense_service.repo.set_qbo_identity.assert_not_called()


def test_expense_legacy_path_update_returns_none_raises_runtime_error():
    """The legacy "mapping found" branch calls the SAME shared
    `_apply_expense_fields` closure the fast path uses — one fix covers both
    call sites by construction, but pin it explicitly since it's a genuinely
    different code path (proves no duplicated/diverging update logic
    reintroduces the gap, mirroring
    test_expense_fast_path_miss_falls_back_to_legacy_mapping_table_path's
    setup)."""
    connector, mapping_repo, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    expense_service.read_by_qbo_identity.return_value = None  # fast path misses
    existing_mapping = SimpleNamespace(id=1, expense_id=55, qbo_purchase_id=qbo_purchase.id)
    mapping_repo.read_by_qbo_purchase_id.return_value = existing_mapping
    existing_expense = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_id.return_value = existing_expense
    expense_service.update_by_public_id.return_value = None

    with pytest.raises(RuntimeError, match="Failed to update Expense"):
        connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)


def test_expense_fast_path_miss_falls_back_to_legacy_mapping_table_path():
    """No dbo row carries this identity yet -> the pre-existing mapping-table-
    based logic must still run, reusing the SAME `_apply_expense_fields`
    closure (proving no duplicated/diverging update logic between the two
    paths)."""
    connector, mapping_repo, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    expense_service.read_by_qbo_identity.return_value = None
    existing_mapping = SimpleNamespace(id=1, expense_id=55, qbo_purchase_id=qbo_purchase.id)
    mapping_repo.read_by_qbo_purchase_id.return_value = existing_mapping
    existing_expense = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_id.return_value = existing_expense
    updated = SimpleNamespace(id=55, public_id="pub-55")
    expense_service.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    expense_service.read_by_qbo_identity.assert_called_once_with("PURCH-99", "realm-1")
    assert result is updated
    expense_service.repo.set_qbo_identity.assert_called_once()  # legacy path still stamps identity


def test_expense_fast_path_skipped_entirely_when_no_qbo_id():
    """A record with no external qbo_id can't possibly have a dbo-native
    identity match — the fast-path lookup should not even be attempted."""
    connector, mapping_repo, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id=None)
    mapping_repo.read_by_qbo_purchase_id.return_value = None
    mapping_repo.read_by_expense_id.return_value = None  # create_mapping's own 1:1 guard
    created = SimpleNamespace(id=77, public_id="pub-77")
    expense_service.create.return_value = created
    mapping_repo.create.return_value = SimpleNamespace(id=1)

    result = connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    expense_service.read_by_qbo_identity.assert_not_called()
    assert result is created


# --- Section 3: PurchaseLineExpenseLineItemConnector._get_project_public_id ---


def _build_purchase_line_connector():
    from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
        PurchaseLineExpenseLineItemConnector,
    )

    project_service = Mock()
    qbo_customer_repo = Mock()
    customer_project_repo = Mock()
    connector = PurchaseLineExpenseLineItemConnector(
        project_service=project_service,
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )
    return connector, project_service, qbo_customer_repo, customer_project_repo


def test_get_project_public_id_prefers_direct_dbo_lookup():
    """U-311 (Wave-5 Option A): the verify step is now `verify_identity_dbo_only`
    — a second call to the SAME `read_by_qbo_identity` (keyed on the resolved
    row's own qbo_id/realm_id), not a qbo.CustomerProject mapping-table read.
    A trusted hit costs exactly 2 dbo reads; there is no `qbo.*` fallback left."""
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_purchase_line_connector()
    direct_project = SimpleNamespace(id=10, public_id="proj-pub-10", qbo_id="CUST-1", realm_id="realm-1", name="Acme")
    project_service.read_by_qbo_identity.return_value = direct_project

    result = connector._get_project_public_id("CUST-1", "realm-1")

    assert result == "proj-pub-10"
    assert project_service.read_by_qbo_identity.call_count == 2
    project_service.read_by_qbo_identity.assert_any_call("CUST-1", "realm-1")
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()
    qbo_customer_repo.read_by_qbo_id.assert_not_called()
    customer_project_repo.read_by_qbo_customer_id.assert_not_called()


def test_get_project_public_id_returns_none_when_direct_lookup_misses():
    """U-311: no legacy hop left — a miss on the direct dbo lookup returns
    None outright."""
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_purchase_line_connector()
    project_service.read_by_qbo_identity.return_value = None

    result = connector._get_project_public_id("CUST-2", "realm-1")

    assert result is None
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()
    customer_project_repo.read_by_qbo_customer_id.assert_not_called()


def test_get_project_public_id_returns_none_when_verification_fails():
    """The direct dbo.Project hit exists, but a fresh re-read by its OWN
    (qbo_id, realm_id) no longer resolves back to the SAME row (a stale/
    "stolen" identity) — must not trust it; U-311 has no legacy hop left to
    fall back to, so this now returns None rather than misattributing the
    line to the wrong project."""
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_purchase_line_connector()
    direct_project = SimpleNamespace(id=10, public_id="proj-pub-10", qbo_id="CUST-1", realm_id="realm-1", name="Acme")
    stolen_by = SimpleNamespace(id=99, public_id="proj-pub-99", qbo_id="CUST-1", realm_id="realm-1", name="Other")
    project_service.read_by_qbo_identity.side_effect = [direct_project, stolen_by]

    result = connector._get_project_public_id("CUST-1", "realm-1")

    assert result is None
    assert project_service.read_by_qbo_identity.call_count == 2
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()


def test_get_project_public_id_caches_per_realm_and_customer_ref():
    """A Purchase's lines commonly share one job/customer_ref_value — the
    second lookup for the same (realm_id, qbo_customer_ref_value) must be
    served from cache, not re-resolved."""
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_purchase_line_connector()
    direct_project = SimpleNamespace(id=10, public_id="proj-pub-10", qbo_id="CUST-1", realm_id="realm-1", name="Acme")
    project_service.read_by_qbo_identity.return_value = direct_project

    first = connector._get_project_public_id("CUST-1", "realm-1")
    second = connector._get_project_public_id("CUST-1", "realm-1")

    assert first == second == "proj-pub-10"
    # 2 reads (initial lookup + verify) for the first call; the second call
    # is served entirely from cache, no further reads.
    assert project_service.read_by_qbo_identity.call_count == 2

    # A different realm is a different cache key — must resolve independently.
    project_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=20, public_id="proj-pub-20", qbo_id="CUST-1", realm_id="realm-2", name="Other"
    )
    third = connector._get_project_public_id("CUST-1", "realm-2")
    assert third == "proj-pub-20"
    assert project_service.read_by_qbo_identity.call_count == 4
