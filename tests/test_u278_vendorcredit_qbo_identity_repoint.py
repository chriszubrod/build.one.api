"""Pure-logic tests for U-278/U-353 (Phase-4 header repoint, then mapping-table
retirement): the `vendorcredit` connector family's HEADER identity resolution
against dbo.BillCredit's native QboId/RealmId, plus the line connector's
`_get_project_public_id` pull-resolver repoint onto dbo.Project.QboId/RealmId
(the U-276 §10-deferred prereq, read-only, no write side).

Line-level fingerprint staging (qbo.VendorCreditLine matching) is explicitly OUT of
scope for this unit — see docs/staging_removal_phase4_5_scoping.md §2/§7/§8 item 4.

Covers:
  1. BillCreditRepository.read_by_qbo_identity (sproc call shape) + BillCreditService
     .read_by_qbo_identity (unlike Customer's thin passthrough, BillCredit checks
     row-level access like its read_by_id/read_by_public_id siblings).
  2. VendorCreditBillCreditConnector's dbo-only identity fast path (U-353 —
     qbo.VendorCreditBillCredit is retired; run_identity_fastpath_dbo_only's own
     conflict/race machinery is covered generically by
     test_u300a_identity_fastpath_dbo_only.py, so this section only proves THIS
     connector's resolve_candidate/stamp_identity/apply_fields wiring). Mirrors
     test_u277_company_address_qbo_identity_repoint.py's post-U-350 Section 2 shape.
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

# U-353: the MISS/create branch runs under run_identity_fastpath_dbo_only's own
# create lock — grant it for every test in this pure-logic module.
pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


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


# --- Section 2: VendorCreditBillCreditConnector dbo-only fast path (U-353) ---
#
# No more _resolve_mapping_state / _record_identity_mapping_conflict_issue to unit-
# test directly (both retired with the mapping table — see this module's own top
# docstring) — every scenario below drives the full sync_from_qbo_vendor_credit().


def _build_bill_credit_connector():
    bill_credit_service = Mock()
    bill_credit_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = VendorCreditBillCreditConnector(
        bill_credit_service=bill_credit_service,
        bill_credit_line_item_service=Mock(),
        vendor_service=Mock(),
        reconciliation_repo=reconciliation_repo,
    )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector, bill_credit_service, reconciliation_repo


def test_dbo_only_hit_updates_in_place_and_skips_identity_restamp():
    """A direct dbo.BillCredit.QboId/RealmId hit updates the existing row and
    never re-stamps identity (already correct by construction — re-stamping
    would be a wasted round trip on the steady-state path)."""
    connector, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1", doc_number="VC-99")
    direct_hit = SimpleNamespace(id=55, public_id="bc-pub-55", credit_number="VC-99", row_version="rv55")
    bill_credit_service.read_by_qbo_identity.return_value = direct_hit
    bill_credit_service.update_by_public_id.return_value = direct_hit

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        result = connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    assert result is direct_hit
    bill_credit_service.create.assert_not_called()
    bill_credit_service.repo.set_qbo_identity.assert_not_called()
    connector._sync_line_items.assert_called_once_with(55, "bc-pub-55", [], "realm-1")


def test_dbo_only_hit_write_race_raises_runtime_error():
    """If `direct` is deleted between read_by_qbo_identity and the write
    (BillCreditService.update_by_public_id returns None on a ROWVERSION race /
    concurrent delete), run_identity_fastpath_dbo_only's own guard must raise —
    never let the None flow through as a silent success (U-291)."""
    connector, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, public_id="bc-pub-55", credit_number="VC-99", row_version="rv55")
    bill_credit_service.read_by_qbo_identity.return_value = direct_hit
    bill_credit_service.update_by_public_id.return_value = None  # race: row gone on write

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    connector._sync_line_items.assert_not_called()


def test_dbo_only_miss_creates_and_stamps_identity():
    """A genuine miss (no dbo.BillCredit currently holds this identity) creates a
    fresh BillCredit, stamps dbo-native identity, and syncs lines — no mapping
    row of any kind. The final return value is a FRESH re-read (not the
    pre-stamp `create()` object) — set_qbo_identity is a void DB write that
    never mutates the in-memory candidate, so returning it unread would hand
    the caller stale qbo_id/realm_id=None even though the row is stamped."""
    connector, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1", doc_number="VC-99")
    bill_credit_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="bc-pub-77", qbo_id=None, realm_id=None)
    bill_credit_service.create.return_value = created
    refreshed = SimpleNamespace(id=77, public_id="bc-pub-77", qbo_id="VC-99", realm_id="realm-1")
    bill_credit_service.read_by_id.return_value = refreshed

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        result = connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    # run_identity_fastpath_dbo_only re-reads under its create lock (race
    # re-check) before treating this as a genuine miss — 2 calls, not 1.
    bill_credit_service.read_by_qbo_identity.assert_called_with("VC-99", "realm-1")
    assert bill_credit_service.read_by_qbo_identity.call_count == 2
    bill_credit_service.create.assert_called_once()
    bill_credit_service.read_by_id.assert_called_once_with(77)
    assert result is refreshed
    assert result.qbo_id == "VC-99"
    bill_credit_service.repo.set_qbo_identity.assert_called_once_with(
        id=77, qbo_id="VC-99", realm_id="realm-1"
    )
    connector._sync_line_items.assert_called_once_with(77, "bc-pub-77", [], "realm-1")


def test_dbo_only_miss_line_sync_failure_rolls_back_header():
    """A permanent line-sync failure on the MISS/create path must delete the
    just-created header (best-effort) so a bad create never strands a
    header-only zombie — mirrors the pre-U-353 legacy CREATE path's
    compensating rollback, minus the (now nonexistent) mapping-row delete."""
    connector, bill_credit_service, reconciliation_repo = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    bill_credit_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="bc-pub-77")
    bill_credit_service.create.return_value = created
    connector._sync_line_items.side_effect = RuntimeError("2 of 2 credit line(s) failed to project")
    bill_credit_service.delete_by_public_id.return_value = created

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        with pytest.raises(RuntimeError, match="failed to project"):
            connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    bill_credit_service.delete_by_public_id.assert_called_once_with("bc-pub-77")
    reconciliation_repo.create.assert_not_called()  # header delete succeeded — no issue to record


def test_dbo_only_miss_header_delete_also_fails_records_orphan_issue():
    """If the compensating header delete ALSO fails after a line-sync failure,
    the orphan is recorded as a reconciliation issue (U-226-style) so it isn't
    silently lost — mirrors the legacy CREATE path's on_header_delete_failed."""
    connector, bill_credit_service, reconciliation_repo = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id="VC-99", realm_id="realm-1")
    bill_credit_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="bc-pub-77")
    bill_credit_service.create.return_value = created
    connector._sync_line_items.side_effect = RuntimeError("line failure")
    bill_credit_service.delete_by_public_id.side_effect = Exception("db down")

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        with pytest.raises(RuntimeError, match="line failure"):
            connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "orphan_billcredit_header"


def test_dbo_only_no_qbo_id_hits_backstop_raise():
    """A record with no external qbo_id can't possibly have a dbo-native identity
    match — run_identity_fastpath_dbo_only short-circuits to hit=False/entity=None
    without even attempting the direct lookup, and the connector's own backstop
    (kept for a directly-invoked falsy qbo_id, mirroring every sibling connector —
    not reachable via the real pull path) raises rather than silently creating."""
    connector, bill_credit_service, _ = _build_bill_credit_connector()
    qbo_vc = _make_qbo_vc(id=30, qbo_id=None)

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
            connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    bill_credit_service.read_by_qbo_identity.assert_not_called()
    bill_credit_service.create.assert_not_called()


# --- Section 3: VendorCreditLineItemConnector._get_project_public_id repoint ---

QBO_CUSTOMER_REPO_PATH = "integrations.intuit.qbo.customer.persistence.repo.QboCustomerRepository"


def _build_line_connector():
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
        VendorCreditLineItemConnector,
    )

    connector = VendorCreditLineItemConnector()
    connector.project_service = Mock()
    return connector


def test_line_get_project_public_id_direct_hit_skips_legacy_lookup():
    """U-311 (Wave-5, scope expansion — this resolver was missed by
    `docs/design/wave5.md` §4's own consumer sweep): a direct hit is now
    verified via `verify_identity_dbo_only` — a second call to the SAME
    `read_by_qbo_identity` (keyed on the resolved row's own qbo_id/realm_id),
    not a qbo.CustomerProject mapping-table read. This resolver previously
    trusted a direct hit unconditionally (unlike its 3 near-identical
    siblings, which all verified) — closing that gap is a side effect of
    retiring the legacy hop below."""
    connector = _build_line_connector()
    project = SimpleNamespace(id=42, public_id="proj-pub-42", qbo_id="QBO-100", realm_id="realm-1")
    connector.project_service.read_by_qbo_identity.return_value = project

    with patch(QBO_CUSTOMER_REPO_PATH) as mock_qbo_customer_repo_cls:
        result = connector._get_project_public_id("QBO-100", "realm-1")

    assert result == "proj-pub-42"
    assert connector.project_service.read_by_qbo_identity.call_count == 2
    connector.project_service.read_by_qbo_identity.assert_any_call("QBO-100", "realm-1")
    mock_qbo_customer_repo_cls.assert_not_called()


def test_line_get_project_public_id_returns_none_when_direct_miss():
    """U-311: no legacy hop left — a miss on the direct dbo lookup returns
    None outright."""
    connector = _build_line_connector()
    connector.project_service.read_by_qbo_identity.return_value = None

    with patch(QBO_CUSTOMER_REPO_PATH) as mock_qbo_customer_repo_cls:
        result = connector._get_project_public_id("QBO-100", "realm-1")

    assert result is None
    mock_qbo_customer_repo_cls.assert_not_called()


def test_line_get_project_public_id_returns_none_when_verification_fails():
    """The direct dbo.Project hit exists, but a fresh re-read by its OWN
    (qbo_id, realm_id) no longer resolves back to the SAME row (a stale/
    "stolen" identity) — must not trust it; U-311 has no legacy hop left to
    fall back to, so this returns None."""
    connector = _build_line_connector()
    project = SimpleNamespace(id=42, public_id="proj-pub-42", qbo_id="QBO-100", realm_id="realm-1")
    stolen_by = SimpleNamespace(id=99, public_id="proj-pub-99", qbo_id="QBO-100", realm_id="realm-1")
    connector.project_service.read_by_qbo_identity.side_effect = [project, stolen_by]

    result = connector._get_project_public_id("QBO-100", "realm-1")

    assert result is None
    assert connector.project_service.read_by_qbo_identity.call_count == 2


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
    project = SimpleNamespace(id=42, public_id="proj-pub-42", qbo_id="QBO-100", realm_id="realm-1")
    connector.project_service.read_by_qbo_identity.return_value = project

    first = connector._get_project_public_id("QBO-100", "realm-1")
    second = connector._get_project_public_id("QBO-100", "realm-1")

    assert first == second == "proj-pub-42"
    # 2 reads (initial lookup + verify) for the first call; the second call
    # is served entirely from cache, no further reads.
    assert connector.project_service.read_by_qbo_identity.call_count == 2


def test_line_get_project_public_id_cache_keyed_by_realm_too():
    """A different realm_id for the same customer ref must NOT hit the same cache
    entry — multi-realm is a real (if currently narrow) case this connector already
    threads through realm_id everywhere else."""
    connector = _build_line_connector()
    project_a = SimpleNamespace(id=1, public_id="proj-pub-a", qbo_id="QBO-100", realm_id="realm-1")
    project_b = SimpleNamespace(id=2, public_id="proj-pub-b", qbo_id="QBO-100", realm_id="realm-2")
    connector.project_service.read_by_qbo_identity.side_effect = [project_a, project_a, project_b, project_b]

    result_a = connector._get_project_public_id("QBO-100", "realm-1")
    result_b = connector._get_project_public_id("QBO-100", "realm-2")

    assert result_a == "proj-pub-a"
    assert result_b == "proj-pub-b"
    assert connector.project_service.read_by_qbo_identity.call_count == 4
