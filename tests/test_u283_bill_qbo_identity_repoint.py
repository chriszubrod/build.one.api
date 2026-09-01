"""Pure-logic tests for U-283 (Phase-4 repoint), then U-355 (mapping-table
retirement): the `bill` connector family's header identity resolution against
dbo.Bill's native QboId/RealmId (U-238a). Also covers the U-276 §10 prereq
fold-in: bill_line_item's `_get_project_public_id` pull resolver tries
dbo.Project's native identity first.

Mirrors tests/test_u283b_purchase_qbo_identity_repoint.py's post-U-354 shape
exactly (both retirements follow the same `run_identity_fastpath_dbo_only`
template) — see that file's own docstring.

Covers:
  1. BillRepository.read_by_qbo_identity (sproc call shape) + BillService's
     thin passthrough.
  2. BillBillConnector's dbo-only identity fast path (U-355 — qbo.BillBill is
     retired; run_identity_fastpath_dbo_only's own conflict/race machinery is
     covered generically by tests/test_u300a_identity_fastpath_dbo_only.py, so
     this section only proves THIS connector's resolve_candidate/
     stamp_identity/apply_fields wiring, including the identity-stamp rollback
     race fix). Like Expense/Purchase, Bill carries SyncToken as part of its
     identity — the HIT branch's apply_fields must still re-stamp identity (to
     refresh SyncToken) even though QboId/RealmId are already
     correct-by-construction.
  3. BillLineItemConnector._get_project_public_id: direct dbo.Project lookup,
     verified via `verify_identity_dbo_only` (U-311, Wave-5 Option A) — a
     second dbo-only re-read by the resolved row's own identity, no
     `qbo.CustomerProject` mapping-table read left at all. A miss or a failed
     verification now returns None outright (no legacy hop to fall back to).
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

SERVICE_MODULE = "integrations.intuit.qbo.bill.connector.bill.business.service"

# U-355: the MISS/create branch runs under run_identity_fastpath_dbo_only's own
# create lock — grant it for every test in this pure-logic module.
pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


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


# --- Section 2: BillBillConnector dbo-only fast path (U-355) ---
#
# No more _record_identity_mapping_conflict_issue / _record_missing_bill_issue
# to unit-test directly (both retired with the mapping table — see this
# module's own top docstring) — every scenario below drives the full
# sync_from_qbo_bill().


def _build_bill_connector():
    bill_service = Mock()
    bill_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = BillBillConnector(
        bill_service=bill_service,
        reconciliation_repo=reconciliation_repo,
    )
    # Out of scope for these tests — vendor resolution and line-item sync are
    # exercised elsewhere; stub them so header-identity behavior is isolated.
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector, bill_service, reconciliation_repo


def test_bill_dbo_only_hit_updates_in_place_and_restamps_synctoken():
    """Like Expense/Purchase, Bill carries SyncToken as part of its identity —
    a direct dbo.Bill.QboId/RealmId hit still re-stamps identity, to refresh
    SyncToken on every pull, even though QboId/RealmId are already
    correct-by-construction."""
    connector, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1", sync_token="7")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", bill_number="B-1", row_version="rv-55")
    bill_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bill_service.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    assert result is updated
    bill_service.create.assert_not_called()
    bill_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="BILL-99", realm_id="realm-1", sync_token="7"
    )
    connector._sync_line_items.assert_called_once_with(55, _ONE_LINE, "realm-1")


def test_bill_dbo_only_hit_write_race_raises_runtime_error():
    """If `direct` is deleted between read_by_qbo_identity and the write
    (BillService.update_by_public_id returns None on a ROWVERSION race /
    concurrent delete), run_identity_fastpath_dbo_only's own guard must raise —
    never let the None flow through as a silent success (U-291)."""
    connector, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", bill_number="B-1", row_version="rv-55")
    bill_service.read_by_qbo_identity.return_value = direct_hit
    bill_service.update_by_public_id.return_value = None  # race: row gone on write

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    connector._sync_line_items.assert_not_called()


def test_bill_dbo_only_miss_creates_and_stamps_identity():
    """A genuine miss (no dbo.Bill currently holds this identity) creates a
    fresh Bill, stamps dbo-native identity, and syncs lines — no mapping row of
    any kind. The final return value is a FRESH re-read (not the pre-stamp
    `create()` object) — set_qbo_identity is a void DB write that never mutates
    the in-memory candidate, so returning it unread would hand the caller stale
    qbo_id/realm_id=None even though the row is stamped."""
    connector, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1", sync_token="3")
    bill_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77", qbo_id=None, realm_id=None)
    bill_service.create.return_value = created
    refreshed = SimpleNamespace(id=77, public_id="pub-77", qbo_id="BILL-99", realm_id="realm-1")
    bill_service.read_by_id.return_value = refreshed

    result = connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    # run_identity_fastpath_dbo_only re-reads under its create lock (race
    # re-check) before treating this as a genuine miss — 2 calls, not 1.
    bill_service.read_by_qbo_identity.assert_called_with("BILL-99", "realm-1")
    assert bill_service.read_by_qbo_identity.call_count == 2
    bill_service.create.assert_called_once()
    bill_service.read_by_id.assert_called_once_with(77)
    assert result is refreshed
    assert result.qbo_id == "BILL-99"
    bill_service.repo.set_qbo_identity.assert_called_once_with(
        id=77, qbo_id="BILL-99", realm_id="realm-1", sync_token="3"
    )
    connector._sync_line_items.assert_called_once_with(77, _ONE_LINE, "realm-1")


def test_bill_dbo_only_miss_identity_stamp_failure_rolls_back_header():
    """A transient set_qbo_identity failure on the MISS/create path must ALSO
    delete the just-created header (best-effort) — not just a line-sync
    failure. Without this, a stamp failure leaves an unstamped orphan Bill that
    read_direct_by_qbo_identity can never find again (no QboId), so the next
    pull tick mints a genuine duplicate. Mirrors the pre-U-355 legacy
    create_mapping()'s own contract: "Stamp dbo-native identity FIRST — if this
    fails, nothing else has been created yet, so the caller's existing
    rollback... fully cleans up.\""""
    connector, bill_service, reconciliation_repo = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    bill_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    bill_service.create.return_value = created
    bill_service.repo.set_qbo_identity.side_effect = RuntimeError("connection reset")
    bill_service.delete_by_public_id.return_value = created

    with pytest.raises(RuntimeError, match="connection reset"):
        connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    bill_service.delete_by_public_id.assert_called_once_with("pub-77")
    connector._sync_line_items.assert_not_called()  # never reached — stamp failed first
    reconciliation_repo.create.assert_not_called()  # header delete succeeded — no issue to record


def test_bill_dbo_only_miss_line_sync_failure_rolls_back_header():
    """A permanent line-sync failure on the MISS/create path must delete the
    just-created header (best-effort) so a bad create never strands a
    header-only zombie — mirrors the pre-U-355 legacy CREATE path's
    compensating rollback, minus the (now nonexistent) mapping-row delete."""
    connector, bill_service, reconciliation_repo = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    bill_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    bill_service.create.return_value = created
    connector._sync_line_items.side_effect = RuntimeError("2 of 2 line item(s) failed to project")
    bill_service.delete_by_public_id.return_value = created

    with pytest.raises(RuntimeError, match="failed to project"):
        connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    bill_service.delete_by_public_id.assert_called_once_with("pub-77")
    reconciliation_repo.create.assert_not_called()  # header delete succeeded — no issue to record


def test_bill_dbo_only_miss_header_delete_also_fails_records_orphan_issue():
    """If the compensating header delete ALSO fails after a line-sync failure,
    the orphan is recorded as a reconciliation issue (U-226-style) so it isn't
    silently lost — mirrors the legacy CREATE path's on_header_delete_failed."""
    connector, bill_service, reconciliation_repo = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="BILL-99", realm_id="realm-1")
    bill_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    bill_service.create.return_value = created
    connector._sync_line_items.side_effect = RuntimeError("line failure")
    bill_service.delete_by_public_id.side_effect = Exception("db down")

    with pytest.raises(RuntimeError, match="line failure"):
        connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "orphan_bill_header"


def test_bill_dbo_only_no_qbo_id_hits_backstop_raise():
    """A record with no external qbo_id can't possibly have a dbo-native
    identity match — run_identity_fastpath_dbo_only short-circuits to
    hit=False/entity=None without even attempting the direct lookup, and the
    connector's own backstop (kept for a directly-invoked falsy qbo_id,
    mirroring every sibling connector — not reachable via the real pull path)
    raises rather than silently creating."""
    connector, bill_service, _ = _build_bill_connector()
    qbo_bill = _make_qbo_bill(id=4, qbo_id=None)

    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_bill(qbo_bill, _ONE_LINE)

    bill_service.read_by_qbo_identity.assert_not_called()
    bill_service.create.assert_not_called()


# --- Section 2b: BillBillConnector.update_has_been_billed_in_qbo (U-355) ---
#
# No prior test coverage existed for this method at all (grepped tests/ before
# writing this section) despite it being live-exercised on every invoice
# completion that bills a QBO-synced Bill. Resolves the target QboBill via
# dbo.Bill's own QboId (verified via verify_identity_dbo_only), not the retired
# qbo.BillBill mapping hop.


def _build_bill_connector_for_hbb():
    bill_service = Mock()
    connector = BillBillConnector(bill_service=bill_service)
    return connector, bill_service


def test_update_has_been_billed_no_qbo_identity_is_a_noop():
    connector, bill_service = _build_bill_connector_for_hbb()
    bill_service.read_by_id.return_value = SimpleNamespace(id=7, qbo_id=None, realm_id="realm-1")

    connector.update_has_been_billed_in_qbo(7, "realm-1")

    bill_service.read_by_qbo_identity.assert_not_called()


def test_update_has_been_billed_refuses_when_identity_no_longer_verifies():
    """A fresh dbo-only re-read resolves to a DIFFERENT Bill -- refuse (log,
    return) rather than push a HasBeenBilled update under disputed identity."""
    connector, bill_service = _build_bill_connector_for_hbb()
    bill = SimpleNamespace(id=7, qbo_id="BILL-99", realm_id="realm-1")
    bill_service.read_by_id.return_value = bill
    bill_service.read_by_qbo_identity.return_value = SimpleNamespace(id=999)
    connector.qbo_bill_repo = Mock()

    connector.update_has_been_billed_in_qbo(7, "realm-1")

    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.assert_not_called()


def test_update_has_been_billed_uses_bills_own_realm_id_for_staging_lookup():
    """Review fix: the staging-cache lookup must use bill.realm_id -- the SAME
    realm source verify_identity_dbo_only just checked against -- not the bare
    `realm_id` parameter, so the two can never silently diverge (single-realm
    today, so they're always equal in practice, but the parameter and the
    bill's own stamped realm are conceptually different sources)."""
    connector, bill_service = _build_bill_connector_for_hbb()
    bill = SimpleNamespace(id=7, qbo_id="BILL-99", realm_id="bills-own-realm")
    bill_service.read_by_id.return_value = bill
    bill_service.read_by_qbo_identity.return_value = SimpleNamespace(id=7)
    connector.qbo_bill_repo = Mock()
    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.return_value = None

    # Called with a DIFFERENT realm_id than the bill's own -- if the lookup used
    # this parameter instead of bill.realm_id, this test's mock would still be
    # consistent (both None) and hide the bug; asserting the CALL ARGS below is
    # what actually pins the fix.
    connector.update_has_been_billed_in_qbo(7, "caller-supplied-realm")

    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.assert_called_once_with(
        "BILL-99", "bills-own-realm"
    )


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
    """U-311 (Wave-5 Option A): the verify step is now `verify_identity_dbo_only`
    — a second call to the SAME `read_by_qbo_identity` (keyed on the resolved
    row's own qbo_id/realm_id), not a qbo.CustomerProject mapping-table read.
    A trusted hit costs exactly 2 dbo reads; there is no `qbo.*` fallback left."""
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_bill_line_item_connector()
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
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_bill_line_item_connector()
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
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_bill_line_item_connector()
    direct_project = SimpleNamespace(id=10, public_id="proj-pub-10", qbo_id="CUST-1", realm_id="realm-1", name="Acme")
    stolen_by = SimpleNamespace(id=99, public_id="proj-pub-99", qbo_id="CUST-1", realm_id="realm-1", name="Other")
    project_service.read_by_qbo_identity.side_effect = [direct_project, stolen_by]

    result = connector._get_project_public_id("CUST-1", "realm-1")

    assert result is None
    assert project_service.read_by_qbo_identity.call_count == 2
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()


def test_get_project_public_id_caches_per_realm_and_customer_ref():
    """A Bill's lines commonly share one job/customer_ref_value — the second
    lookup for the same (realm_id, qbo_customer_ref_value) must be served from
    cache, not re-resolved."""
    connector, project_service, qbo_customer_repo, customer_project_repo = _build_bill_line_item_connector()
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
