"""Pure-logic tests for U-290 (Phase-4, header/reference repoint) + U-313
(Wave 5, dbo-only): the `vendor` connector family's identity resolution.

U-290 repointed it onto dbo.Vendor's native QboId/RealmId via the shared
`run_identity_fastpath()` helper (mapping-table-aware). U-313 (Wave 5's "trust
dbo alone" plan, `docs/design/wave5.md`) went further and dropped the
`qbo.VendorVendor` mapping table entirely, moving `VendorVendorConnector.
sync_from_qbo_vendor` onto `run_identity_fastpath_dbo_only` (mirrors U-300b's
`AttachableAttachmentConnector` / U-307c's `ItemCostCodeConnector` / U-310's
`CustomerCustomerConnector`) — no `qbo.VendorVendor` read/write of any kind.
Section 2 below now tests that final dbo-only shape; the old mapping-table
heal/adopt/dedup branch structure it replaced (and its own dedicated test
file, `test_qbo_vendor_vendor_heal.py`) no longer applies and was deleted —
still-valid standalone tests from that file (pure helpers, `_ensure_vendor_
address`, `VendorService.create` prefetch races) were folded into Section 3
below.

Covers:
  1. VendorRepository.read_by_qbo_identity (sproc call shape) + VendorService
     .read_by_qbo_identity (bare passthrough — Vendor has no row-level RBAC,
     matching Customer's/PaymentTerm's shape, not BillCredit's).
  2. VendorVendorConnector's dbo-only fast path (U-313): direct/race-resolved
     hit refreshes fields + the QboActive mirror (U-275) every time; a
     genuine miss adopts an existing unmapped Vendor by exact name (with a
     dbo-native duplicate-QboId guard, both in `_resolve_vendor_candidate`
     and re-checked under `_stamp_vendor_identity`'s own lock) or creates a
     brand-new one; a blank DisplayName or an inactive+unmapped QboVendor
     both refuse before ever reaching create/adopt; a ROWVERSION race on
     apply_fields raises loud, never silently.
  3. Standalone Vendor-family tests unrelated to identity resolution,
     inherited from the deleted heal-test file.
"""
from contextlib import contextmanager
from types import SimpleNamespace
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


def test_vendor_repo_read_deleted_by_qbo_identity_calls_sproc_and_projects_row():
    """U-313 P1 guard: the including-deleted counterpart sproc/repo method."""
    from entities.vendor.persistence.repo import VendorRepository

    repo = VendorRepository()
    row = Mock(Id=77, PublicId="vendor-pub-77", Name="Acme Supply")
    cursor = Mock()
    cursor.fetchone.return_value = row

    with patch("entities.vendor.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.vendor.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = repo.read_deleted_by_qbo_identity("QBO-V-1", "r1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadDeletedVendorByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "QBO-V-1", "RealmId": "r1"}
    assert result.id == 77
    assert result.public_id == "vendor-pub-77"
    assert result.name == "Acme Supply"


def test_vendor_repo_read_deleted_by_qbo_identity_returns_none_on_no_row():
    from entities.vendor.persistence.repo import VendorRepository

    repo = VendorRepository()
    cursor = Mock()
    cursor.fetchone.return_value = None

    with patch("entities.vendor.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.vendor.persistence.repo.call_procedure"
    ):
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = repo.read_deleted_by_qbo_identity("QBO-V-1", "r1")

    assert result is None


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


# --- Section 2: VendorVendorConnector's dbo-only fast path (U-313) ---

VENDOR_MODULE = "integrations.intuit.qbo.vendor.connector.vendor.business.service"
FASTPATH_LOCK_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
STAMP_LOCK_TARGET = f"{VENDOR_MODULE}.qbo_app_lock"


def _granted_lock(*_args, **_kwargs):
    @contextmanager
    def _cm(*_a, **_k):
        yield True

    return _cm()


def _make_vendor(**overrides):
    defaults = dict(id=100, public_id="vendor-pub-100", name="Acme Supply", qbo_id=None, realm_id=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_vendor_connector():
    vendor_service = Mock()
    vendor_service.repo = Mock()
    # U-313 P1 guard: default to "no soft-deleted row holds this identity"
    # so tests exercising the ordinary miss/create/adopt path aren't diverted
    # into the deleted-holder raise. Tests for that guard override this.
    vendor_service.read_deleted_by_qbo_identity.return_value = None
    reconciliation_repo = Mock()
    connector = VendorVendorConnector(
        vendor_service=vendor_service,
        vendor_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=reconciliation_repo,
    )
    connector._sync_addresses = Mock()
    return connector, vendor_service, reconciliation_repo


def test_vendor_direct_hit_updates_fields_refreshes_active_syncs_addresses():
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1", active=False, display_name="Acme Supply")
    direct_hit = _make_vendor(id=55, name="Acme Supply")
    vendor_service.read_by_qbo_identity.return_value = direct_hit
    vendor_service.repo.update_by_id.return_value = direct_hit

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is direct_hit
    vendor_service.create.assert_not_called()
    # Active is refreshed on every hit; QboId/RealmId are omitted (already
    # correct by construction) so the sproc's CASE WHEN guards leave them
    # untouched and no theft-detection re-trigger fires.
    vendor_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id=None, realm_id=None, active=False,
    )
    connector._sync_addresses.assert_called_once_with(qbo_vendor, 55)


def test_vendor_direct_hit_preserves_non_blank_local_name():
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name="Acme Supply (QBO)")
    direct_hit = _make_vendor(name="Curated Local Name")
    vendor_service.read_by_qbo_identity.return_value = direct_hit

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result.name == "Curated Local Name"
    vendor_service.repo.update_by_id.assert_not_called()  # no-op, pure no-write


def test_vendor_genuine_miss_creates_new_and_stamps_identity():
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1", active=True, display_name="Totally New")
    vendor_service.read_by_qbo_identity.return_value = None
    vendor_service.read_by_name.return_value = None
    created = _make_vendor(id=300, qbo_id=None, realm_id=None)
    vendor_service.create.return_value = created
    stamped = _make_vendor(id=300, qbo_id="QBO-V-1", realm_id="r1")
    vendor_service.read_by_id.side_effect = [created, stamped]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock), patch(
        STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is stamped
    vendor_service.create.assert_called_once_with(
        name="Totally New", abbreviation=None, is_draft=False, prefetched_by_name=None,
    )
    vendor_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="QBO-V-1", realm_id="r1", active=True,
    )
    connector._sync_addresses.assert_called_once_with(qbo_vendor, 300)


def test_vendor_genuine_miss_adopts_existing_unmapped_by_name():
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(id=1, qbo_id="QBO-V-1", realm_id="r1", display_name="Existing Local")
    vendor_service.read_by_qbo_identity.return_value = None
    existing = _make_vendor(id=400, qbo_id=None, realm_id=None, name="Existing Local")
    vendor_service.read_by_name.return_value = existing
    stamped = _make_vendor(id=400, qbo_id="QBO-V-1", realm_id="r1", name="Existing Local")
    vendor_service.read_by_id.side_effect = [existing, stamped]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock), patch(
        STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is stamped
    vendor_service.create.assert_not_called()
    vendor_service.repo.set_qbo_identity.assert_called_once_with(
        id=400, qbo_id="QBO-V-1", realm_id="r1", active=None,
    )
    connector._sync_addresses.assert_called_once_with(qbo_vendor, 400)


def test_vendor_resolve_candidate_does_not_mutate_or_persist_the_adopted_row():
    """The field write is deferred to `_stamp_vendor_identity`, applied
    atomically with the identity stamp under the candidate's own lock — two
    different QboVendors name-matching the SAME local Vendor concurrently
    (no contention on the qbo_id-keyed create lock, since they carry
    different qbo_ids) must not each mutate it before either acquires that
    lock. Direct unit test on resolve_candidate itself."""
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1", display_name="Existing Local")
    existing = _make_vendor(id=400, qbo_id=None, name="Existing Local")
    vendor_service.read_by_name.return_value = existing

    candidate = connector._resolve_vendor_candidate(qbo_vendor, vendor_name="Existing Local")

    assert candidate is existing
    vendor_service.repo.update_by_id.assert_not_called()


def test_vendor_duplicate_qbo_id_guard_in_resolve_candidate_raises_and_records_issue():
    """A name-matched Vendor already carrying a DIFFERENT QboId must NOT be
    returned as the candidate — stamp_identity's theft-clear only protects
    the INCOMING pair's uniqueness, not this row's prior identity; it would
    silently re-point it. Mirrors CustomerCustomerConnector's/
    ItemCostCodeConnector's Decision-2 guard."""
    connector, vendor_service, reconciliation_repo = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1", display_name="Existing Local")
    vendor_service.read_by_qbo_identity.return_value = None
    existing = _make_vendor(id=400, qbo_id="QBO-OTHER", realm_id="r1", name="Existing Local")
    vendor_service.read_by_name.return_value = existing

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_vendor(qbo_vendor)

    vendor_service.repo.update_by_id.assert_not_called()
    vendor_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_vendor"


def test_vendor_duplicate_guard_catches_same_qbo_id_different_realm():
    """QBO ids are only unique WITHIN a realm — a QboId-only check would let a
    same-QboId-different-realm row through."""
    connector, vendor_service, reconciliation_repo = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1", display_name="Existing Local")
    vendor_service.read_by_qbo_identity.return_value = None
    existing = _make_vendor(id=400, qbo_id="QBO-V-1", realm_id="r-OTHER", name="Existing Local")
    vendor_service.read_by_name.return_value = existing

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_vendor(qbo_vendor)

    reconciliation_repo.create.assert_called_once()


def test_vendor_deleted_holder_of_identity_raises_and_records_issue_no_duplicate():
    """P1 (Codex, U-313 review): a Vendor soft-deleted locally while still
    active in QBO reads as a plain dbo-only "miss" (read_by_qbo_identity
    filters IsDeleted=0) -- without this guard the connector would mint a
    DUPLICATE active Vendor and SetVendorQboIdentity's theft-clear (no
    IsDeleted filter of its own) would silently strip the deleted row's
    QboId. Must refuse instead, mirroring the pre-U-313 mapping-table
    architecture's own heal-don't-delete discipline."""
    connector, vendor_service, reconciliation_repo = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1", display_name="Acme Supply")
    vendor_service.read_by_qbo_identity.return_value = None
    deleted_holder = _make_vendor(id=77, public_id="vendor-pub-77", name="Acme Supply", qbo_id="QBO-V-1")
    vendor_service.read_deleted_by_qbo_identity.return_value = deleted_holder

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already held by soft-deleted Vendor"):
            connector.sync_from_qbo_vendor(qbo_vendor)

    vendor_service.read_deleted_by_qbo_identity.assert_called_once_with("QBO-V-1", "r1")
    vendor_service.read_by_name.assert_not_called()
    vendor_service.create.assert_not_called()
    vendor_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "deleted_vendor_holds_identity"
    assert "77" in kwargs["details"]


def test_vendor_no_deleted_holder_proceeds_to_ordinary_create_path():
    """Sibling of the guard test above: when no deleted row holds the
    identity, the create path proceeds exactly as before -- the guard only
    intercepts the specific deleted-holder case."""
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1", display_name="Totally New")
    vendor_service.read_by_qbo_identity.return_value = None
    vendor_service.read_by_name.return_value = None
    created = _make_vendor(id=300, qbo_id=None, realm_id=None)
    vendor_service.create.return_value = created
    stamped = _make_vendor(id=300, qbo_id="QBO-V-1", realm_id="r1")
    vendor_service.read_by_id.side_effect = [created, stamped]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock), patch(
        STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is stamped
    vendor_service.read_deleted_by_qbo_identity.assert_called_once_with("QBO-V-1", "r1")


def test_vendor_blank_display_name_no_dbo_match_raises_and_records_issue():
    connector, vendor_service, reconciliation_repo = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1", display_name="   ")
    vendor_service.read_by_qbo_identity.return_value = None

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="blank DisplayName"):
            connector.sync_from_qbo_vendor(qbo_vendor)

    vendor_service.read_by_name.assert_not_called()
    vendor_service.create.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "blank_display_name_qbo_vendor"


def test_vendor_inactive_unmapped_raises_without_creating():
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", active=False, display_name="Inactive Co")
    vendor_service.read_by_qbo_identity.return_value = None

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="inactive"):
            connector.sync_from_qbo_vendor(qbo_vendor)

    vendor_service.read_by_name.assert_not_called()
    vendor_service.create.assert_not_called()


def test_vendor_race_discovered_hit_adopts_racer_without_create():
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1")
    racer_row = _make_vendor(id=400, qbo_id="QBO-V-1", realm_id="r1")
    vendor_service.read_by_qbo_identity.side_effect = [None, racer_row]
    vendor_service.repo.update_by_id.side_effect = lambda v: v

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is racer_row
    vendor_service.create.assert_not_called()
    connector._sync_addresses.assert_called_once_with(qbo_vendor, 400)


def test_vendor_no_qbo_id_raises():
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id=None, display_name="Brand New Vendor")

    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    vendor_service.read_by_qbo_identity.assert_not_called()


def test_vendor_lock_resource_key_matches_dbo_only_namespace():
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1", display_name="Totally New")
    vendor_service.read_by_qbo_identity.return_value = None
    vendor_service.read_by_name.return_value = None
    vendor_service.create.return_value = _make_vendor(id=300)
    vendor_service.read_by_id.return_value = _make_vendor(id=300, qbo_id="QBO-V-1", realm_id="r1")
    recorded = []

    def _recording_lock(resource_name, timeout_ms=15000):
        recorded.append(resource_name)
        return _granted_lock()

    with patch(FASTPATH_LOCK_TARGET, side_effect=_recording_lock), patch(
        STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        connector.sync_from_qbo_vendor(qbo_vendor)

    assert recorded == ["qbo_dbo_identity_create:Vendor:QBO-V-1:r1"]


def test_vendor_stamp_identity_refuses_to_overwrite_different_existing_identity():
    """Also proves the shared `_check_no_conflicting_vendor_identity` records
    a reconciliation issue from THIS call site too (/simplify simplification
    lens: the pre-extraction code only recorded one from
    _resolve_vendor_candidate's copy, an asymmetry the shared guard closes)."""
    connector, vendor_service, reconciliation_repo = _build_vendor_connector()
    candidate = _make_vendor(id=150)
    vendor_service.read_by_id.return_value = _make_vendor(id=150, qbo_id="QBO-OTHER", realm_id="r1")
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1")

    with patch(STAMP_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector._stamp_vendor_identity(candidate, qbo_vendor)

    vendor_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_vendor"


def test_check_no_conflicting_vendor_identity_allows_reresolve_to_same_identity():
    """A benign re-resolve (existing.qbo_id/realm_id already equal the
    incoming pair) must proceed normally -- the shared guard only blocks a
    DIFFERENT identity, not an exact re-match."""
    connector, vendor_service, reconciliation_repo = _build_vendor_connector()
    local_vendor = _make_vendor(id=150, qbo_id="QBO-V-1", realm_id="r1")
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1")

    connector._check_no_conflicting_vendor_identity(local_vendor, qbo_vendor)

    reconciliation_repo.create.assert_not_called()


def test_check_no_conflicting_vendor_identity_noop_when_no_existing_identity():
    connector, vendor_service, reconciliation_repo = _build_vendor_connector()
    local_vendor = _make_vendor(id=150, qbo_id=None, realm_id=None)
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1")

    connector._check_no_conflicting_vendor_identity(local_vendor, qbo_vendor)

    reconciliation_repo.create.assert_not_called()


def test_vendor_stamp_identity_lock_key_scoped_to_candidate():
    connector, vendor_service, _ = _build_vendor_connector()
    candidate = _make_vendor(id=150)
    vendor_service.read_by_id.return_value = _make_vendor(id=150, qbo_id=None, realm_id=None)
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1")
    recorded = []

    def _recording_lock(resource_name, timeout_ms=15000):
        recorded.append(resource_name)
        return _granted_lock()

    with patch(STAMP_LOCK_TARGET, side_effect=_recording_lock):
        connector._stamp_vendor_identity(candidate, qbo_vendor)

    assert recorded == ["qbo_dbo_identity_stamp:Vendor:150"]


def test_vendor_stamp_identity_returns_none_when_candidate_vanished():
    """The candidate was deleted between resolve_candidate and the lock being
    acquired here — must not blindly stamp a nonexistent row; propagates as
    the top-level RuntimeError via run_identity_fastpath_dbo_only's own
    entity-is-None handling."""
    connector, vendor_service, _ = _build_vendor_connector()
    candidate = _make_vendor(id=150)
    vendor_service.read_by_id.return_value = None
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1", display_name="Totally New")

    with patch(STAMP_LOCK_TARGET, side_effect=_granted_lock):
        result = connector._stamp_vendor_identity(candidate, qbo_vendor)

    assert result is None
    vendor_service.repo.set_qbo_identity.assert_not_called()


def test_vendor_direct_hit_apply_returns_none_raises_runtime_error():
    """ROWVERSION race on the direct-hit path: `_apply_vendor_fields_and_sync`
    returning None must fail loud (RuntimeError, hold for retry), never
    silently advance the watermark past a Vendor whose fields were never
    written. UpdateVendorById's RAISERROR-on-not-found/RowVersion-mismatch
    design means `repo.update_by_id` can't actually return None without an
    exception already having fired, so this isolates the scenario by mocking
    `_apply_vendor_fields_and_sync` directly, rather than trying to drive a
    real None out of that unreachable production path. As of U-316 the raise
    comes from `run_identity_fastpath_dbo_only`'s own unconditional
    apply-path guard, not a per-family `on_apply_returned_none` wiring
    (removed here as dead now that the primitive raises unconditionally)."""
    connector, vendor_service, _ = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1", display_name="Renamed")
    direct_hit = _make_vendor(id=55, name="Old Name")
    vendor_service.read_by_qbo_identity.return_value = direct_hit
    connector._apply_vendor_fields_and_sync = Mock(return_value=None)

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_vendor(qbo_vendor)


def test_vendor_raise_duplicate_qbo_vendor_issue_names_the_conflicting_identity():
    connector, _, reconciliation_repo = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(qbo_id="QBO-V-1", realm_id="r1")
    local_vendor = _make_vendor(id=55, public_id="vendor-pub-55")

    connector._raise_duplicate_qbo_vendor_issue(
        qbo_vendor=qbo_vendor, local_vendor=local_vendor, existing_qbo_id="QBO-OTHER",
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "duplicate_qbo_vendor"
    assert "QBO-OTHER" in kwargs["details"]


# --- Section 3: standalone Vendor-family tests, salvaged from the deleted
# test_qbo_vendor_vendor_heal.py (unrelated to identity-resolution wiring) ---


def test_preserve_human_edited_name_cases():
    from integrations.intuit.qbo.base.field_ownership import preserve_human_edited_name

    assert preserve_human_edited_name("Local Curated", "QBO DISPLAY") == "Local Curated"
    assert preserve_human_edited_name(None, "From QBO") == "From QBO"
    assert preserve_human_edited_name("", "From QBO") == "From QBO"
    assert preserve_human_edited_name("   ", "From QBO") == "From QBO"
    assert preserve_human_edited_name(None, None) is None


def test_for_entity_vendor_field_ownership_registry():
    """Vendor registry key resolves and name is both_editable (typo in _REGISTRY would KeyError)."""
    from integrations.intuit.qbo.base.field_ownership import BOTH_EDITABLE, for_entity

    rules = for_entity("Vendor")
    assert rules.ownership_of("name") == BOTH_EDITABLE


def _vendor_name_unique_violation():
    from shared.database import DatabaseConstraintError, map_database_error

    raw = (
        "('23000', \"[23000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"
        "Violation of UNIQUE KEY constraint 'UQ_Vendor_Name_Active'. "
        "Cannot insert duplicate key in object 'dbo.Vendor'. (2627)\")"
    )
    error = map_database_error(Exception(raw))
    assert isinstance(error, DatabaseConstraintError)
    return error


def test_vendor_create_prefetch_skips_read_by_name():
    """prefetched_by_name=None must skip read_by_name and proceed to insert."""
    from entities.vendor.business.model import Vendor
    from entities.vendor.business.service import VendorService

    repo = Mock()
    service = VendorService(repo=repo)
    created = Vendor(
        id=700, public_id="vendor-pub-700", row_version=None, created_datetime=None,
        modified_datetime=None, name="Prefetch Vendor", abbreviation=None,
        taxpayer_id=None, vendor_type_id=None, is_draft=False,
    )
    service.read_by_name = Mock(return_value=_make_vendor(id=999, name="Prefetch Vendor"))
    repo.create.return_value = created

    result = service.create(name="Prefetch Vendor", prefetched_by_name=None)

    service.read_by_name.assert_not_called()
    repo.create.assert_called_once()
    assert result is created


def test_vendor_create_prefetch_race_translates_unique_constraint_to_value_error():
    """Prefetch shortcut race on UQ_Vendor_Name_Active must surface as ValueError (skip)."""
    from entities.vendor.business.service import VendorService

    repo = Mock()
    service = VendorService(repo=repo)
    repo.create.side_effect = _vendor_name_unique_violation()

    with pytest.raises(ValueError, match="Vendor with name 'Race Vendor' already exists"):
        service.create(name="Race Vendor", prefetched_by_name=None)


def _make_vendor_address(*, va_id=1, vendor_id=100, address_id=10, address_type_id=1):
    from entities.vendor_address.business.model import VendorAddress

    return VendorAddress(
        id=str(va_id), public_id="va-pub-1", row_version=None, created_datetime=None,
        modified_datetime=None, vendor_id=str(vendor_id), address_id=str(address_id),
        address_type_id=str(address_type_id),
    )


def test_ensure_vendor_address_uses_read_all_by_vendor_id_not_read_by_vendor_id():
    connector, vendor_service, _ = _build_vendor_connector()
    connector.vendor_address_service.read_all_by_vendor_id.return_value = []

    connector._ensure_vendor_address(vendor_id=100, address_id=20, address_type_id=1)

    connector.vendor_address_service.read_by_vendor_id.assert_not_called()
    connector.vendor_address_service.read_all.assert_not_called()
    connector.vendor_address_service.read_all_by_vendor_id.assert_called_once_with(100)
    connector.vendor_address_service.create.assert_called_once_with(
        vendor_id="100", address_id="20", address_type_id="1",
    )


def test_ensure_vendor_address_updates_when_existing_differs():
    connector, vendor_service, _ = _build_vendor_connector()
    existing = _make_vendor_address(vendor_id=100, address_id=10)
    connector.vendor_address_service.read_all_by_vendor_id.return_value = [existing]

    connector._ensure_vendor_address(vendor_id=100, address_id=20, address_type_id=1)

    connector.vendor_address_service.read_all_by_vendor_id.assert_called_once_with(100)
    connector.vendor_address_service.create.assert_not_called()
    connector.vendor_address_service.repo.update_by_id.assert_called_once_with(existing)
    assert existing.address_id == "20"


def test_ensure_vendor_address_noop_when_existing_same_address():
    connector, vendor_service, _ = _build_vendor_connector()
    existing = _make_vendor_address(vendor_id=100, address_id=20)
    connector.vendor_address_service.read_all_by_vendor_id.return_value = [existing]

    connector._ensure_vendor_address(vendor_id=100, address_id=20, address_type_id=1)

    connector.vendor_address_service.read_all_by_vendor_id.assert_called_once_with(100)
    connector.vendor_address_service.create.assert_not_called()
    connector.vendor_address_service.repo.update_by_id.assert_not_called()


def test_ensure_vendor_address_creates_when_no_matching_type():
    connector, vendor_service, _ = _build_vendor_connector()
    other_type = _make_vendor_address(vendor_id=100, address_id=10, address_type_id=99)
    connector.vendor_address_service.read_all_by_vendor_id.return_value = [other_type]

    connector._ensure_vendor_address(vendor_id=100, address_id=20, address_type_id=1)

    connector.vendor_address_service.read_all_by_vendor_id.assert_called_once_with(100)
    connector.vendor_address_service.repo.update_by_id.assert_not_called()
    connector.vendor_address_service.create.assert_called_once_with(
        vendor_id="100", address_id="20", address_type_id="1",
    )
