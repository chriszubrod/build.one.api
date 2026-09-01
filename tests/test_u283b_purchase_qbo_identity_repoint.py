"""Pure-logic tests for U-283b/U-354 (Phase-4 repoint, then mapping-table
retirement): the `purchase` connector family's HEADER identity resolution
against dbo.Expense's native QboId/RealmId, plus the line connector's
`_get_project_public_id` pull resolver repoint onto dbo.Project.QboId/RealmId
(the U-276 §10 prereq, read-only, no write side).

Mirrors tests/test_u278_vendorcredit_qbo_identity_repoint.py's post-U-353
shape exactly (both retirements follow the same `run_identity_fastpath_dbo_only`
template). qbo.Purchase/qbo.PurchaseLine remain a documented read-only audit
mirror for the expense-coding cockpit's `PurchaseExpenseConnector.
recode_purchase_line` (untouched by this unit, Chris's 2026-08-20 decision) —
these tests only cover the identity-resolution seam this unit repointed.

Covers:
  1. ExpenseRepository.read_by_qbo_identity (sproc call shape) + ExpenseService's
     thin passthrough.
  2. PurchaseExpenseConnector's dbo-only identity fast path (U-354 —
     qbo.PurchaseExpense is retired; run_identity_fastpath_dbo_only's own
     conflict/race machinery is covered generically by
     test_u300a_identity_fastpath_dbo_only.py, so this section only proves THIS
     connector's resolve_candidate/stamp_identity/apply_fields wiring). Unlike
     Company/Address/Project/VendorCredit, Expense (like Bill) carries SyncToken
     as part of its identity — the HIT branch's apply_fields must still
     re-stamp identity (to refresh SyncToken) even though QboId/RealmId are
     already correct-by-construction.
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

SERVICE_MODULE = "integrations.intuit.qbo.purchase.connector.expense.business.service"

LINE_CONNECTOR_PATH = (
    "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
    ".PurchaseLineExpenseLineItemConnector"
)

# U-354: the MISS/create branch runs under run_identity_fastpath_dbo_only's own
# create lock — grant it for every test in this pure-logic module.
pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


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


# --- Section 2: PurchaseExpenseConnector dbo-only fast path (U-354) ---
#
# No more _record_identity_mapping_conflict_issue / _record_missing_expense_issue
# to unit-test directly (both retired with the mapping table — see this module's
# own top docstring) — every scenario below drives the full sync_from_qbo_purchase().


def _build_purchase_connector():
    expense_service = Mock()
    expense_service.repo = Mock()
    reconciliation_repo = Mock()
    with patch(LINE_CONNECTOR_PATH, return_value=Mock()):
        connector = PurchaseExpenseConnector(
            expense_service=expense_service,
            reconciliation_repo=reconciliation_repo,
        )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector, expense_service, reconciliation_repo


def test_dbo_only_hit_updates_in_place_and_restamps_synctoken():
    """Unlike Company/Address/Project/VendorCredit, Expense carries SyncToken as
    part of its identity (mirrors Bill) — a direct dbo.Expense.QboId/RealmId hit
    still re-stamps identity, to refresh SyncToken on every pull, even though
    QboId/RealmId are already correct-by-construction."""
    connector, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1", sync_token="7")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    expense_service.update_by_public_id.return_value = updated

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        result = connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    assert result is updated
    expense_service.create.assert_not_called()
    expense_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="PURCH-99", realm_id="realm-1", sync_token="7"
    )
    connector._sync_line_items.assert_called_once_with(55, "pub-55", _ONE_LINE, "realm-1")


def test_dbo_only_hit_write_race_raises_runtime_error():
    """If `direct` is deleted between read_by_qbo_identity and the write
    (ExpenseService.update_by_public_id returns None on a ROWVERSION race /
    concurrent delete), run_identity_fastpath_dbo_only's own guard must raise —
    never let the None flow through as a silent success (U-291)."""
    connector, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", reference_number="R-1", row_version="rv-55")
    expense_service.read_by_qbo_identity.return_value = direct_hit
    expense_service.update_by_public_id.return_value = None  # race: row gone on write

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    connector._sync_line_items.assert_not_called()


def test_dbo_only_miss_creates_and_stamps_identity():
    """A genuine miss (no dbo.Expense currently holds this identity) creates a
    fresh Expense, stamps dbo-native identity, and syncs lines — no mapping row
    of any kind. The final return value is a FRESH re-read (not the pre-stamp
    `create()` object) — set_qbo_identity is a void DB write that never mutates
    the in-memory candidate, so returning it unread would hand the caller stale
    qbo_id/realm_id=None even though the row is stamped."""
    connector, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1", sync_token="3")
    expense_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77", qbo_id=None, realm_id=None)
    expense_service.create.return_value = created
    refreshed = SimpleNamespace(id=77, public_id="pub-77", qbo_id="PURCH-99", realm_id="realm-1")
    expense_service.read_by_id.return_value = refreshed

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        result = connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    # run_identity_fastpath_dbo_only re-reads under its create lock (race
    # re-check) before treating this as a genuine miss — 2 calls, not 1.
    expense_service.read_by_qbo_identity.assert_called_with("PURCH-99", "realm-1")
    assert expense_service.read_by_qbo_identity.call_count == 2
    expense_service.create.assert_called_once()
    expense_service.read_by_id.assert_called_once_with(77)
    assert result is refreshed
    assert result.qbo_id == "PURCH-99"
    expense_service.repo.set_qbo_identity.assert_called_once_with(
        id=77, qbo_id="PURCH-99", realm_id="realm-1", sync_token="3"
    )
    connector._sync_line_items.assert_called_once_with(77, "pub-77", _ONE_LINE, "realm-1")


def test_dbo_only_miss_identity_stamp_failure_rolls_back_header():
    """A transient set_qbo_identity failure on the MISS/create path must ALSO
    delete the just-created header (best-effort) — not just a line-sync
    failure. Without this, a stamp failure leaves an unstamped orphan Expense
    that read_direct_by_qbo_identity can never find again (no QboId), so the
    next pull tick mints a genuine duplicate. Mirrors the pre-U-354 legacy
    create_mapping()'s own contract: "Stamp dbo-native identity FIRST — if
    this fails, nothing else has been created yet, so the caller's existing
    rollback... fully cleans up.\""""
    connector, expense_service, reconciliation_repo = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    expense_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    expense_service.create.return_value = created
    expense_service.repo.set_qbo_identity.side_effect = RuntimeError("connection reset")
    expense_service.delete_by_public_id.return_value = created

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        with pytest.raises(RuntimeError, match="connection reset"):
            connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    expense_service.delete_by_public_id.assert_called_once_with("pub-77")
    connector._sync_line_items.assert_not_called()  # never reached — stamp failed first
    reconciliation_repo.create.assert_not_called()  # header delete succeeded — no issue to record


def test_dbo_only_miss_line_sync_failure_rolls_back_header():
    """A permanent line-sync failure on the MISS/create path must delete the
    just-created header (best-effort) so a bad create never strands a
    header-only zombie — mirrors the pre-U-354 legacy CREATE path's
    compensating rollback, minus the (now nonexistent) mapping-row delete."""
    connector, expense_service, reconciliation_repo = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    expense_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    expense_service.create.return_value = created
    connector._sync_line_items.side_effect = RuntimeError("2 of 2 line item(s) failed to project")
    expense_service.delete_by_public_id.return_value = created

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        with pytest.raises(RuntimeError, match="failed to project"):
            connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    expense_service.delete_by_public_id.assert_called_once_with("pub-77")
    reconciliation_repo.create.assert_not_called()  # header delete succeeded — no issue to record


def test_dbo_only_miss_header_delete_also_fails_records_orphan_issue():
    """If the compensating header delete ALSO fails after a line-sync failure,
    the orphan is recorded as a reconciliation issue (U-226-style) so it isn't
    silently lost — mirrors the legacy CREATE path's on_header_delete_failed."""
    connector, expense_service, reconciliation_repo = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    expense_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    expense_service.create.return_value = created
    connector._sync_line_items.side_effect = RuntimeError("line failure")
    expense_service.delete_by_public_id.side_effect = Exception("db down")

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        with pytest.raises(RuntimeError, match="line failure"):
            connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "orphan_expense_header"


def test_dbo_only_no_qbo_id_hits_backstop_raise():
    """A record with no external qbo_id can't possibly have a dbo-native identity
    match — run_identity_fastpath_dbo_only short-circuits to hit=False/entity=None
    without even attempting the direct lookup, and the connector's own backstop
    (kept for a directly-invoked falsy qbo_id, mirroring every sibling connector —
    not reachable via the real pull path) raises rather than silently creating."""
    connector, expense_service, _ = _build_purchase_connector()
    qbo_purchase = _make_qbo_purchase(id=4, qbo_id=None)

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
            connector.sync_from_qbo_purchase(qbo_purchase, _ONE_LINE)

    expense_service.read_by_qbo_identity.assert_not_called()
    expense_service.create.assert_not_called()


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
