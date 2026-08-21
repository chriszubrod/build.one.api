"""Pure-logic tests for U-290 (Phase-4, header/reference repoint): repoint the
`vendor` connector family's identity resolution off qbo.Vendor / qbo.VendorVendor
onto dbo.Vendor's native QboId/RealmId, via the shared
base.identity_fastpath.run_identity_fastpath() helper (U-287).

The qbo.VendorVendor MAPPING-TABLE fan-out into other families (Bill pull/push,
Purchase/Expense pull, VendorCredit pull, the expense-coding cockpit) is
explicitly OUT of scope — see docs/staging_removal_phase4_5_scoping.md §2/§8
item 10. Those consumers keep reading the mapping table for a different purpose
(resolving vendor refs on entities other than Vendor itself) and are unaffected
by this repoint — the mapping rows are still created identically regardless of
which path (fast or legacy) resolves the Vendor.

Covers:
  1. VendorRepository.read_by_qbo_identity (sproc call shape) + VendorService
     .read_by_qbo_identity (bare passthrough — Vendor has no row-level RBAC,
     matching Customer's/PaymentTerm's shape, not BillCredit's).
  2. VendorVendorConnector's direct-identity fast path: consistent hit skips
     the mapping-table hop and identity re-stamp but DOES refresh the
     QboActive mirror (U-275) every hit; missing hit self-heals a missing
     mapping row; conflict (either side) RAISES and writes nothing; a
     self-heal create-race escalates to a recorded conflict; a miss falls
     through to the pre-existing mapping-table path unchanged; no qbo_id
     skips the fast path entirely.
  3. The QboActive-refresh-on-every-hit behavior specifically (the deliberate
     deviation from U-282/PaymentTerm's accepted-staleness tradeoff, adopted
     instead to match ItemSubCostCodeConnector's (concurrent, same session)
     fix for the identical "family also carries an Active mirror" shape).
"""
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendor.connector.vendor.business.service import VendorVendorConnector

VENDOR_SERVICE = "integrations.intuit.qbo.vendor.connector.vendor.business.service"


def _make_qbo_vendor(**overrides):
    defaults = dict(
        id=1,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="QBO-V-1",
        sync_token=None,
        realm_id="r1",
        display_name="Acme Supply",
        title=None,
        given_name=None,
        middle_name=None,
        family_name=None,
        suffix=None,
        company_name=None,
        print_on_check_name=None,
        tax_identifier=None,
        vendor_1099=None,
        active=None,
        primary_email_addr=None,
        primary_phone=None,
        mobile=None,
        fax=None,
        bill_addr_id=None,
        balance=None,
        acct_num=None,
        web_addr=None,
    )
    defaults.update(overrides)
    return QboVendor(**defaults)


# --- Section 1: repo-level sproc call shape ---


def test_vendor_repo_read_by_qbo_identity_calls_sproc():
    from entities.vendor.persistence.repo import VendorRepository

    repo = VendorRepository()
    cursor = Mock()
    cursor.fetchone.return_value = None

    with patch("entities.vendor.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.vendor.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("QBO-V-1", "r1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadVendorByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "QBO-V-1", "RealmId": "r1"}


def test_vendor_service_read_by_qbo_identity_is_bare_passthrough():
    """Vendor has no row-level RBAC (unlike Project/BillCredit) — the new method
    must be a bare passthrough, matching Customer's/PaymentTerm's template."""
    from entities.vendor.business.service import VendorService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = "sentinel"
    service = VendorService(repo=repo)

    result = service.read_by_qbo_identity("QBO-V-1", "r1")

    repo.read_by_qbo_identity.assert_called_once_with("QBO-V-1", "r1")
    assert result == "sentinel"


# --- Section 2: VendorVendorConnector fast path ---


def _build_vendor_connector():
    mapping_repo = Mock()
    vendor_service = Mock()
    vendor_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = VendorVendorConnector(
        mapping_repo=mapping_repo,
        vendor_service=vendor_service,
        vendor_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=reconciliation_repo,
    )
    connector._sync_addresses = Mock()
    return connector, mapping_repo, vendor_service, reconciliation_repo


def test_vendor_fast_path_hit_consistent_skips_mapping_write_but_refreshes_active():
    connector, mapping_repo, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1", active=False, display_name="Acme Supply")
    direct_hit = Mock(id=55, name="Acme Supply")
    vendor_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_vendor_id.return_value = Mock(qbo_vendor_id=1)
    mapping_repo.read_by_qbo_vendor_id.return_value = Mock(vendor_id=55)
    vendor_service.repo.update_by_id.return_value = direct_hit

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is direct_hit
    mapping_repo.create.assert_not_called()
    # Active is refreshed on every hit; QboId/RealmId are omitted (already
    # correct by construction) so the sproc's CASE WHEN guards leave them
    # untouched and no theft-detection re-trigger fires.
    vendor_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id=None, realm_id=None, active=False,
    )
    # Proves apply_fields actually ran (not silently dropped from the
    # run_identity_fastpath() call) — _sync_addresses is _apply_vendor_fields_and_sync's
    # own unconditional final step, fired on every branch of that method but never fired
    # if apply_fields were skipped (identity_fastpath.py's `if apply_fields is None:
    # updated = direct` path). `result is direct_hit` alone can't tell the two apart when
    # the QBO/stored names already match, since the no-op branch also returns the input
    # object unchanged.
    connector._sync_addresses.assert_called_once_with(qbo_vendor, 55)


def test_vendor_fast_path_hit_missing_self_heals_mapping():
    connector, mapping_repo, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1", active=True)
    direct_hit = Mock(id=55, name="Acme Supply")
    vendor_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_vendor_id.return_value = None
    mapping_repo.read_by_qbo_vendor_id.return_value = None
    vendor_service.repo.update_by_id.return_value = direct_hit

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is direct_hit
    mapping_repo.create.assert_called_once_with(vendor_id=55, qbo_vendor_id=1)
    connector._sync_addresses.assert_called_once_with(qbo_vendor, 55)


def test_vendor_fast_path_hit_missing_apply_returns_none_raises_runtime_error():
    """The RuntimeError write-race callback (U-290 deliberately diverges from
    VendorCreditBillCreditConnector's silent-None sibling, see test_u278's
    test_fast_path_missing_write_race_returns_none_without_crashing) — locked in
    with its own regression test since it's the one place this connector's
    behavior diverges from its direct precedent."""
    # _apply_vendor_fields_and_sync itself can't cleanly return None in practice — its
    # "fill" branch does `vendor = repo.update_by_id(vendor)` immediately followed by
    # `logger.info(f"...{vendor.id}...")`, which would AttributeError on a genuine None
    # before ever returning it — and UpdateVendorById's RAISERROR-on-not-found/
    # RowVersion-mismatch design means repo.update_by_id can't return None without an
    # exception already having fired. So this test isolates the on_apply_returned_none
    # WIRING itself (mocking _apply_vendor_fields_and_sync directly) rather than trying
    # to drive a real None out of the unreachable production path — the RuntimeError
    # handler is deliberate defense-in-depth against a future change to that sproc's
    # error semantics, not a currently-live path.
    connector, mapping_repo, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1")
    direct_hit = Mock(id=55, name="Acme Supply")
    vendor_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_vendor_id.return_value = None
    mapping_repo.read_by_qbo_vendor_id.return_value = None
    connector._apply_vendor_fields_and_sync = Mock(return_value=None)

    with pytest.raises(RuntimeError, match="dbo-identity fast path"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    mapping_repo.create.assert_not_called()


def test_vendor_fast_path_hit_consistent_apply_returns_none_raises_runtime_error():
    """U-291: on_apply_returned_none must fire on the 'consistent' steady-state
    resync too, not just the rarer 'missing' self-heal window above. Before this
    fix, `run_identity_fastpath` only invoked the callback when state == MISSING
    — on a CONSISTENT hit (the common case for an already-mapped Vendor,
    exercised here via an existing mapping row) a None return fell through
    silently, with NO callback and NO exception, regardless of what this
    connector's own callback would have raised. Same wiring-isolation approach
    as the MISSING test above (mocking _apply_vendor_fields_and_sync directly)."""
    connector, mapping_repo, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1")
    direct_hit = Mock(id=55, name="Acme Supply")
    vendor_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_vendor_id.return_value = Mock(qbo_vendor_id=1)
    mapping_repo.read_by_qbo_vendor_id.return_value = Mock(vendor_id=55)
    connector._apply_vendor_fields_and_sync = Mock(return_value=None)

    with pytest.raises(RuntimeError, match="dbo-identity fast path"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    mapping_repo.create.assert_not_called()
    vendor_service.repo.set_qbo_identity.assert_not_called()


def test_vendor_fast_path_conflict_qbo_side_raises_and_writes_nothing():
    """Falling through on a conflict would update the CONFLICTING Vendor and call
    set_qbo_identity on it — SetVendorQboIdentity's theft-detection UPDATE applies
    against ANY row carrying that (QboId, RealmId), silently NULLing `direct`'s
    identity. Must hard-stop instead."""
    connector, mapping_repo, vendor_service, reconciliation_repo = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1")
    direct_hit = Mock(id=55, name="Acme Supply")
    vendor_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_vendor_id.return_value = None
    mapping_repo.read_by_qbo_vendor_id.return_value = Mock(id=2, vendor_id=9, qbo_vendor_id=1)

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "vendor_identity_conflict"
    vendor_service.repo.update_by_id.assert_not_called()
    vendor_service.repo.set_qbo_identity.assert_not_called()
    mapping_repo.create.assert_not_called()


def test_vendor_fast_path_conflict_local_side_raises_no_duplicate_create():
    connector, mapping_repo, vendor_service, reconciliation_repo = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1")
    direct_hit = Mock(id=55, name="Acme Supply")
    vendor_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_vendor_id.return_value = Mock(id=3, qbo_vendor_id=5)
    mapping_repo.read_by_qbo_vendor_id.return_value = None

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "vendor_identity_conflict"
    mapping_repo.create.assert_not_called()
    vendor_service.repo.update_by_id.assert_not_called()


def test_vendor_fast_path_self_heal_race_escalates_to_recorded_conflict():
    connector, mapping_repo, vendor_service, reconciliation_repo = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1", active=True)
    direct_hit = Mock(id=55, name="Acme Supply")
    vendor_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_vendor_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_vendor_id.side_effect = [
        None, Mock(id=9, vendor_id=3, qbo_vendor_id=1)
    ]
    vendor_service.repo.update_by_id.return_value = direct_hit
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is direct_hit
    connector._sync_addresses.assert_called_once_with(qbo_vendor, 55)
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "vendor_identity_conflict"


def test_vendor_fast_path_miss_falls_back_to_mapping_table_path():
    connector, mapping_repo, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1", display_name="Brand New Vendor")
    vendor_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_vendor_id.return_value = None
    vendor_service.read_by_name.return_value = None
    mapping_repo.read_by_vendor_id.return_value = None  # unused stub (create_mapping's caller passes prefetched_by_vendor=None literally, not _PREFETCH_UNSET, so this accessor is never actually invoked here)
    created = Mock(id=77)
    vendor_service.create.return_value = created
    mapping_repo.create.return_value = Mock(id=1)

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    vendor_service.read_by_qbo_identity.assert_called_once_with("QBO-V-1", "r1")
    assert result is created
    vendor_service.create.assert_called_once()


def test_vendor_fast_path_skipped_entirely_when_no_qbo_id():
    connector, mapping_repo, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id=None, display_name="Brand New Vendor")
    mapping_repo.read_by_qbo_vendor_id.return_value = None
    vendor_service.read_by_name.return_value = None
    mapping_repo.read_by_vendor_id.return_value = None  # unused stub (create_mapping's caller passes prefetched_by_vendor=None literally, not _PREFETCH_UNSET, so this accessor is never actually invoked here)
    vendor_service.create.return_value = Mock(id=1)
    mapping_repo.create.return_value = Mock(id=1)

    connector.sync_from_qbo_vendor(qbo_vendor)

    vendor_service.read_by_qbo_identity.assert_not_called()


def test_vendor_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1")
    qbo_side = Mock(id=2, vendor_id=9, qbo_vendor_id=1)
    local_side = Mock(id=3, vendor_id=55, qbo_vendor_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_vendor=qbo_vendor, dbo_vendor_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "vendor_identity_conflict"
    assert "55" in kwargs["details"]
    assert "9" in kwargs["details"]
    assert "5" in kwargs["details"]


def test_vendor_legacy_path_still_stamps_identity_after_apply():
    """Regression coverage: set_qbo_identity is called with the REAL qbo_id/realm_id
    ONLY by the legacy mapping-table path (a mapping-matched row may predate identity
    stamping) — the fast path deliberately never re-stamps identity itself (see the
    'consistent' test above, which only refreshes Active with qbo_id/realm_id=None)."""
    connector, mapping_repo, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1", active=True)
    vendor_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_vendor_id.return_value = Mock(id=1, vendor_id=55)
    stored = Mock(id=55, name="Acme Supply")
    vendor_service.read_by_id.return_value = stored
    vendor_service.repo.update_by_id.return_value = stored

    connector.sync_from_qbo_vendor(qbo_vendor)

    vendor_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="QBO-V-1", realm_id="r1", active=True,
    )
