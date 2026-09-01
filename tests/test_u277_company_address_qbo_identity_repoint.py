"""Pure-logic tests for U-277 (Phase-4) / U-350: repoint the `company_info` +
`physical_address` connector families' identity resolution off
qbo.CompanyInfo / qbo.CompanyInfoCompany / qbo.PhysicalAddress /
qbo.PhysicalAddressAddress onto dbo.Company / dbo.Address's native
QboId/RealmId (U-238a/c). Mirrors tests/test_u276_customer_project_qbo_identity_repoint.py's
shape exactly — see that file's module docstring for the pattern rationale.

Covers:
  1. CompanyRepository.read_by_qbo_identity / AddressRepository.read_by_qbo_identity
     (sproc call shape) + their service-layer passthroughs (neither entity has
     row-level RBAC, so — unlike Project — no actor scope to thread).
  2. CompanyInfoCompanyConnector's identity resolution — as of U-350 this is the
     DBO-ONLY fast path (`run_identity_fastpath_dbo_only`): no qbo.CompanyInfoCompany
     read or write of any kind, so there is no mapping-table fallback, no self-heal,
     and no mapping-vs-dbo conflict state left to test. Mirrors U-310's
     CustomerCustomerConnector / U-313's VendorVendorConnector one-for-one. See
     Section 2's own header for the one Company-specific divergence.
  3. PhysicalAddressAddressConnector's fast path — UNCHANGED by U-350, still the
     pre-existing mapping-table hop (qbo.PhysicalAddressAddress is a separate U-349
     family, out of scope for this unit).

Out of scope (confirmed at Gate-1): no outbound push anywhere in the codebase reads
dbo.Company.QboId or dbo.Address.QboId to build a QBO reference — U-276's Section 4
(push-helper repoint) has no analog here.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import mock_qbo_app_lock_granted
from integrations.intuit.qbo.company_info.connector.business.service import (
    CompanyInfoCompanyConnector,
)
from integrations.intuit.qbo.physical_address.connector.business.service import (
    PhysicalAddressAddressConnector,
)


def _make_qbo_company_info(**overrides):
    defaults = dict(
        id=4,
        qbo_id="CI-99",
        realm_id="realm-1",
        legal_name="Acme Co",
        web_addr="acme.example.com",
        modified_datetime="2026-01-01 00:00:00",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_qbo_physical_address(**overrides):
    defaults = dict(
        id=100,
        qbo_id="PA-99",
        realm_id="realm-1",
        line1="123 Main",
        line2="",
        city="Austin",
        country_sub_division_code="TX",
        postal_code="78701",
        modified_datetime="2026-01-01 00:00:00",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- Section 1: repo/service-level sproc call shape ---


def test_company_repo_read_by_qbo_identity_calls_sproc():
    from entities.company.persistence.repo import CompanyRepository

    repo = CompanyRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.company.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.company.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("CI-99", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadCompanyByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "CI-99", "RealmId": "realm-1"}


def test_address_repo_read_by_qbo_identity_calls_sproc():
    from entities.address.persistence.repo import AddressRepository

    repo = AddressRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.address.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.address.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("PA-99", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadAddressByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "PA-99", "RealmId": "realm-1"}


def test_company_service_read_by_qbo_identity_is_a_thin_passthrough():
    from entities.company.business.service import CompanyService

    repo = Mock()
    sentinel = SimpleNamespace(id=1)
    repo.read_by_qbo_identity.return_value = sentinel
    service = CompanyService(repo=repo)
    result = service.read_by_qbo_identity("CI-1", "realm-1")
    repo.read_by_qbo_identity.assert_called_once_with("CI-1", "realm-1")
    assert result is sentinel


def test_address_service_read_by_qbo_identity_is_a_thin_passthrough():
    from entities.address.business.service import AddressService

    repo = Mock()
    sentinel = SimpleNamespace(id=1)
    repo.read_by_qbo_identity.return_value = sentinel
    service = AddressService(repo=repo)
    result = service.read_by_qbo_identity("PA-1", "realm-1")
    repo.read_by_qbo_identity.assert_called_once_with("PA-1", "realm-1")
    assert result is sentinel


# --- Section 2: CompanyInfoCompanyConnector fast path ---
#
# U-350 UPDATE: this family is now the DBO-ONLY fast path
# (`run_identity_fastpath_dbo_only`), mirroring U-310's CustomerCustomerConnector /
# U-313's VendorVendorConnector one-for-one — see those files' own module docstrings
# for the pattern rationale. No qbo.CompanyInfoCompany read or write of any kind, so
# there is no mapping-table fallback, no self-heal, and no mapping-vs-dbo conflict
# state left to test. A hit updates fields and writes nothing else; a genuine miss
# adopts by NAME or creates, then stamps identity under the candidate's own lock.
# One Company-specific divergence from Customer/Vendor: `sync_from_qbo_to_company`
# takes a bare `qbo_company_info_id` (not the already-fetched staging row), so it
# reads `qbo_company_info_service.repo.read_by_id` first — and unlike every sibling,
# it always overwrites name/website unconditionally (no `preserve_human_edited_name`
# — the pre-U-350 legacy path never had one either, "QBO is source of truth").

FASTPATH_LOCK_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
# _stamp_company_identity's own lock lives in the shared stamp_dbo_identity_with_lock
# (U-328/U-331) inside identity_fastpath.py -- same target as the create lock above.
STAMP_LOCK_TARGET = FASTPATH_LOCK_TARGET


def _build_company_connector():
    company_service = Mock()
    company_service.repo = Mock()
    reconciliation_repo = Mock()
    qbo_company_info_service = Mock()
    qbo_company_info_service.repo = Mock()
    connector = CompanyInfoCompanyConnector(
        company_service=company_service,
        qbo_company_info_service=qbo_company_info_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, company_service, reconciliation_repo


def test_company_direct_hit_updates_fields_no_create_or_stamp():
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1", legal_name="Acme")
    direct_hit = SimpleNamespace(id=55, name="Old", website="old.example.com")
    company_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, name="Acme", website="acme.example.com")
    company_service.repo.update_by_id.return_value = updated

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    result = connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    assert result is updated
    company_service.repo.update_by_id.assert_called_once()
    company_service.create.assert_not_called()
    company_service.repo.set_qbo_identity.assert_not_called()
    company_service.read_by_name.assert_not_called()


def test_company_direct_hit_always_overwrites_name_and_website():
    """QBO is source of truth for company_info — unlike Customer/Vendor/Project,
    there is no preserve_human_edited_name here (the pre-U-350 legacy path never
    had one either); the write is a raw, unconditional overwrite."""
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(
        qbo_id="CI-99", realm_id="realm-1", legal_name="New Legal Name", web_addr="new.example.com",
    )
    direct_hit = SimpleNamespace(id=55, name="Curated Old Name", website="old.example.com")
    company_service.read_by_qbo_identity.return_value = direct_hit
    company_service.repo.update_by_id.side_effect = lambda c: c

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    result = connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    assert result.name == "New Legal Name"
    assert result.website == "new.example.com"


def test_company_genuine_miss_creates_new_and_stamps_identity():
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(
        qbo_id="CI-99", realm_id="realm-1", legal_name="Acme", web_addr="acme.example.com",
    )
    company_service.read_by_qbo_identity.return_value = None
    company_service.read_by_name.return_value = None
    created = SimpleNamespace(id=300, qbo_id=None, realm_id=None)
    company_service.create.return_value = created
    stamped = SimpleNamespace(id=300, qbo_id="CI-99", realm_id="realm-1")
    company_service.read_by_id.side_effect = [created, stamped]

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    assert result is stamped
    company_service.create.assert_called_once_with(name="Acme", website="acme.example.com")
    company_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="CI-99", realm_id="realm-1"
    )


def test_company_genuine_miss_adopts_existing_unmapped_by_name():
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(
        qbo_id="CI-99", realm_id="realm-1", legal_name="Acme", web_addr="acme.example.com",
    )
    company_service.read_by_qbo_identity.return_value = None
    existing = SimpleNamespace(id=150, qbo_id=None, realm_id=None, name="Old Name", website="old.example.com")
    company_service.read_by_name.return_value = existing
    company_service.repo.update_by_id.side_effect = lambda c: c
    stamped = SimpleNamespace(id=150, qbo_id="CI-99", realm_id="realm-1", name="Acme")
    company_service.read_by_id.side_effect = [existing, stamped]

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    assert result is stamped
    assert existing.name == "Acme"
    assert existing.website == "acme.example.com"
    company_service.create.assert_not_called()
    company_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="CI-99", realm_id="realm-1"
    )


def test_company_blank_incoming_name_skips_the_adopt_lookup_and_creates():
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1", legal_name=None)
    company_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=300, name="")
    company_service.create.return_value = created
    company_service.read_by_id.side_effect = [created, created]

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    company_service.read_by_name.assert_not_called()
    company_service.create.assert_called_once_with(name="", website=qbo_company_info.web_addr or "")


def test_company_resolve_candidate_does_not_mutate_or_persist_the_adopted_row():
    """Mirrors CustomerCustomerConnector's identical guard (U-310): the field
    write happens only in _stamp_company_identity, atomically with the identity
    stamp under the candidate's own lock — resolve_candidate itself must be PURE."""
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1", legal_name="Acme")
    existing = SimpleNamespace(
        id=150, qbo_id=None, realm_id=None, name="Untouched Name", website="untouched.example.com",
    )
    company_service.read_by_name.return_value = existing

    candidate = connector._resolve_company_candidate(
        qbo_company_info, name="Acme", website="acme.example.com", realm_id="realm-1",
    )

    assert candidate is existing
    assert existing.name == "Untouched Name"
    assert existing.website == "untouched.example.com"
    company_service.repo.update_by_id.assert_not_called()


def test_company_duplicate_qbo_id_guard_raises_and_records_issue():
    """A name-matched Company already carrying a DIFFERENT QboId must NOT be
    returned as the candidate -- stamp_identity's theft-clear would silently
    re-point it. Must raise + record a company_identity_conflict issue instead,
    mirroring the mapping-table-era contract this replaces."""
    connector, company_service, reconciliation_repo = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1", legal_name="Acme")
    company_service.read_by_qbo_identity.return_value = None
    existing = SimpleNamespace(id=150, public_id="company-pub-150", qbo_id="CI-OTHER", realm_id="realm-1")
    company_service.read_by_name.return_value = existing

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    company_service.repo.update_by_id.assert_not_called()
    company_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "company_identity_conflict"


def test_company_duplicate_guard_catches_same_qbo_id_different_realm():
    """QBO ids are only unique WITHIN a realm, so a QboId-only check would let a
    same-QboId-different-realm row through and overwrite its name/website before
    _stamp_company_identity's own (qbo_id AND realm_id) check ever runs."""
    connector, company_service, reconciliation_repo = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1", legal_name="Acme")
    company_service.read_by_qbo_identity.return_value = None
    existing = SimpleNamespace(
        id=150, public_id="company-pub-150", qbo_id="CI-99", realm_id="realm-OTHER", name="Untouched",
    )
    company_service.read_by_name.return_value = existing

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    assert existing.name == "Untouched"  # never mutated before the raise
    company_service.repo.update_by_id.assert_not_called()
    company_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()


def test_company_race_discovered_hit_adopts_racer_without_create():
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1")
    racer_row = SimpleNamespace(id=400, qbo_id="CI-99", realm_id="realm-1")
    company_service.read_by_qbo_identity.side_effect = [None, racer_row]
    company_service.repo.update_by_id.side_effect = lambda c: c

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        result = connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    assert result is racer_row
    company_service.create.assert_not_called()
    company_service.repo.set_qbo_identity.assert_not_called()
    assert company_service.read_by_qbo_identity.call_args_list == [
        call("CI-99", "realm-1"),
        call("CI-99", "realm-1"),
    ]


def test_company_update_returning_none_raises_runtime_error_not_value_error():
    """A ROWVERSION race on the HIT branch (update_by_id affected 0 rows) must
    raise RuntimeError, NOT ValueError (U-291 discipline, carried through the
    repoint) — record_projection_error classifies a plain ValueError as a
    permanent SKIP that advances the watermark past a Company whose fields
    were never written. RuntimeError holds it for retry."""
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1")
    company_service.read_by_qbo_identity.return_value = SimpleNamespace(id=55)
    company_service.repo.update_by_id.return_value = None  # race: row gone on write

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    company_service.create.assert_not_called()
    company_service.repo.set_qbo_identity.assert_not_called()


def test_company_no_qbo_id_raises():
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id=None)

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    company_service.read_by_qbo_identity.assert_not_called()


def test_company_uses_connector_realm_id_when_qbo_company_info_realm_id_is_falsy():
    """The `qbo_company_info.realm_id or realm_id` fallback has no analog in the
    sibling connectors (Customer/Vendor/Project take no separate realm_id
    parameter at all) — it's U-277 logic, preserved unchanged by U-350. Prove
    its precedence directly with the two sources set to DIFFERENT values."""
    connector, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id=None)
    company_service.read_by_qbo_identity.return_value = None
    company_service.read_by_name.return_value = None
    created = SimpleNamespace(id=1)
    company_service.create.return_value = created
    company_service.read_by_id.side_effect = [
        created, SimpleNamespace(id=1, qbo_id="CI-99", realm_id="connector-realm"),
    ]

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        connector.sync_from_qbo_to_company(qbo_company_info.id, "connector-realm")

    # Called twice (pre-lock check + re-check under the create lock) — both with
    # the connector-level realm_id fallback, never the (falsy) qbo_company_info one.
    assert company_service.read_by_qbo_identity.call_args_list == [
        call("CI-99", "connector-realm"),
        call("CI-99", "connector-realm"),
    ]


def test_company_stamp_identity_refuses_to_overwrite_different_existing_identity():
    connector, company_service, _ = _build_company_connector()
    candidate = SimpleNamespace(id=150)
    company_service.read_by_id.return_value = SimpleNamespace(
        id=150, public_id="company-pub-150", qbo_id="CI-OTHER", realm_id="realm-1",
    )
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1")

    with patch(STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match=r"already carries QBO identity CI-OTHER"):
            connector._stamp_company_identity(candidate, qbo_company_info, realm_id="realm-1")

    company_service.repo.set_qbo_identity.assert_not_called()
    company_service.repo.update_by_id.assert_not_called()  # never mutated before the raise


def test_company_stamp_identity_update_returning_none_raises_runtime_error():
    """A ROWVERSION race between the pre-stamp read and the field-write
    update_by_id call must not silently proceed to stamp identity on a row
    whose write never took."""
    connector, company_service, _ = _build_company_connector()
    candidate = SimpleNamespace(id=150)
    company_service.read_by_id.return_value = SimpleNamespace(
        id=150, qbo_id=None, realm_id=None, name="Old Name",
    )
    company_service.repo.update_by_id.return_value = None  # race: row gone on write
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1", legal_name="New Name")

    with patch(STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector._stamp_company_identity(candidate, qbo_company_info, realm_id="realm-1")

    company_service.repo.set_qbo_identity.assert_not_called()


def test_company_stamp_identity_sanitizes_blank_legal_name_to_empty_string():
    """Codex xhigh round-1 P1: on the genuine-miss create path,
    `_resolve_company_candidate`'s own `.create(name=name or "", ...)` already
    sanitizes a blank QboCompanyInfo LegalName -- but this method's OWN
    apply_fields closure used to re-derive name/website RAW from
    qbo_company_info and immediately overwrite that already-sanitized value
    with None, which `UpdateCompanyById`'s `NOT NULL [Name]` column rejects.
    Mutation target: dropping the `or ""` here reintroduces the None write."""
    connector, company_service, _ = _build_company_connector()
    candidate = SimpleNamespace(id=150)
    unmapped = SimpleNamespace(id=150, qbo_id=None, realm_id=None, name="", website="")
    company_service.read_by_id.return_value = unmapped
    company_service.repo.update_by_id.side_effect = lambda c: c
    qbo_company_info = _make_qbo_company_info(
        qbo_id="CI-99", realm_id="realm-1", legal_name=None, web_addr=None,
    )

    with patch(STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector._stamp_company_identity(candidate, qbo_company_info, realm_id="realm-1")

    assert unmapped.name == ""
    assert unmapped.website == ""


def test_company_duplicate_guard_records_effective_fallback_realm_not_raw_none():
    """Codex xhigh round-1 P2: qbo_company_info.realm_id can be None while the
    connector-level realm_id fallback supplies the real realm (U-277's own
    fallback) -- the recorded ReconciliationIssue must use that EFFECTIVE
    realm, not misreport realm_id="" / an incoming realm of None."""
    connector, company_service, reconciliation_repo = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id=None, legal_name="Acme")
    company_service.read_by_qbo_identity.return_value = None
    existing = SimpleNamespace(id=150, public_id="company-pub-150", qbo_id="CI-OTHER", realm_id="realm-1")
    company_service.read_by_name.return_value = existing

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError):
            connector.sync_from_qbo_to_company(qbo_company_info.id, "connector-realm")

    kwargs = reconciliation_repo.create.call_args.kwargs
    # The recorded issue's realm_id is the EFFECTIVE fallback, not the raw
    # (falsy) qbo_company_info.realm_id -- before the fix this was "".
    assert kwargs["realm_id"] == "connector-realm"


def test_company_stamp_identity_applies_field_write_atomically_with_stamp():
    """The field write happens INSIDE this method, under the candidate lock, not
    in resolve_candidate — confirms it's actually applied, and that
    write_identity delegates through create_mapping (no mapping row left)."""
    connector, company_service, _ = _build_company_connector()
    candidate = SimpleNamespace(id=150)
    unmapped = SimpleNamespace(id=150, qbo_id=None, realm_id=None, name="Old Name", website="old.example.com")
    company_service.read_by_id.return_value = unmapped
    company_service.repo.update_by_id.side_effect = lambda c: c
    qbo_company_info = _make_qbo_company_info(
        qbo_id="CI-99", realm_id="realm-1", legal_name="New Name", web_addr="new.example.com",
    )

    with patch(STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector._stamp_company_identity(candidate, qbo_company_info, realm_id="realm-1")

    assert unmapped.name == "New Name"
    assert unmapped.website == "new.example.com"
    company_service.repo.update_by_id.assert_called_once_with(unmapped)
    company_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="CI-99", realm_id="realm-1"
    )


# --- Section 3: PhysicalAddressAddressConnector fast path ---
# Same testing shape as Section 2.


def _build_address_connector():
    mapping_repo = Mock()
    address_service = Mock()
    address_service.repo = Mock()
    reconciliation_repo = Mock()
    qbo_physical_address_service = Mock()
    qbo_physical_address_service.repo = Mock()
    connector = PhysicalAddressAddressConnector(
        mapping_repo=mapping_repo,
        address_service=address_service,
        qbo_physical_address_service=qbo_physical_address_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, mapping_repo, address_service, reconciliation_repo


def test_address_resolve_mapping_state_consistent():
    connector, mapping_repo, _, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(id=100)
    mapping_repo.read_by_address_id.return_value = SimpleNamespace(id=1, qbo_physical_address_id=100)
    mapping_repo.read_by_qbo_physical_address_id.return_value = SimpleNamespace(id=1, address_id=55)

    state, _, _ = connector._resolve_mapping_state(address_id=55, qbo_physical_address=qbo_physical_address)

    assert state == "consistent"


def test_address_resolve_mapping_state_missing():
    connector, mapping_repo, _, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(id=100)
    mapping_repo.read_by_address_id.return_value = None
    mapping_repo.read_by_qbo_physical_address_id.return_value = None

    state, _, _ = connector._resolve_mapping_state(address_id=55, qbo_physical_address=qbo_physical_address)

    assert state == "missing"


def test_address_resolve_mapping_state_qbo_side_conflict():
    connector, mapping_repo, _, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(id=100)
    mapping_repo.read_by_address_id.return_value = None
    mapping_repo.read_by_qbo_physical_address_id.return_value = SimpleNamespace(id=2, address_id=9)

    state, by_address, by_qbo_physical_address = connector._resolve_mapping_state(
        address_id=55, qbo_physical_address=qbo_physical_address
    )

    assert state == "conflict"
    assert by_address is None
    assert by_qbo_physical_address.address_id == 9


def test_address_resolve_mapping_state_local_side_conflict():
    connector, mapping_repo, _, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(id=100)
    mapping_repo.read_by_address_id.return_value = SimpleNamespace(id=3, qbo_physical_address_id=5)
    mapping_repo.read_by_qbo_physical_address_id.return_value = None

    state, by_address, by_qbo_physical_address = connector._resolve_mapping_state(
        address_id=55, qbo_physical_address=qbo_physical_address
    )

    assert state == "conflict"
    assert by_address.qbo_physical_address_id == 5
    assert by_qbo_physical_address is None


def test_address_record_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(id=100, qbo_id="PA-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, address_id=9, qbo_physical_address_id=100)
    local_side = SimpleNamespace(id=3, address_id=55, qbo_physical_address_id=5)

    connector._record_identity_mapping_conflict_issue(
        qbo_physical_address=qbo_physical_address, dbo_address_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "address_identity_conflict"
    # Phrase-level checks, not bare digit substrings — the always-emitted
    # first sentence's own "55"/"100"/"PA-99" would trivially satisfy a plain
    # "in details" check even if the qbo-side/local-side blocks were dropped.
    assert "Address 9 (mapping 2)" in kwargs["details"]          # qbo-side conflicting Address
    assert "DIFFERENT QboPhysicalAddress 5" in kwargs["details"]  # local-side conflicting QboPhysicalAddress


def test_address_fast_path_hit_conflict_raises_and_never_steals_identity():
    """Address twin of the Company hard-stop test. REWRITTEN BY U-287.

    This family's fall-through was the most direct of the two: on a qbo-side conflict
    the legacy path resolves Address 9 via the pre-existing mapping, updates it, and
    then the trailing stamp block (`if not needs_mapping_repair and mapping is not
    None and mapping.qbo_physical_address_id == ...`) calls set_qbo_identity on
    Address 9 — and SetAddressQboIdentity's theft-clear UPDATE nulls QboId/RealmId on
    ANY other row holding that pair, i.e. Address 55. Identity theft, reachable
    without any by-street/city miss. The `protected_address_id` guard never covered it.
    """
    connector, mapping_repo, address_service, reconciliation_repo = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, street_one="", street_two="", city="", state="", zip="")
    address_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_address_id.return_value = None
    conflicting = SimpleNamespace(id=2, address_id=9, qbo_physical_address_id=qbo_physical_address.id)
    mapping_repo.read_by_qbo_physical_address_id.return_value = conflicting
    # If the fall-through were still present, these would let it reach Address 9,
    # write it, and stamp identity onto it — stealing it from Address 55.
    address_service.read_by_id.return_value = SimpleNamespace(
        id=9, street_one="", street_two="", city="", state="", zip="",
        modified_datetime="2026-01-01 00:00:00",
    )
    address_service.repo.update_by_id.side_effect = lambda a: a

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with pytest.raises(ValueError):
        connector.sync_from_qbo_to_address(qbo_physical_address.id)

    reconciliation_repo.create.assert_called_once()
    address_service.create.assert_not_called()  # NO duplicate Address minted
    address_service.repo.update_by_id.assert_not_called()  # NO write to ANY Address
    address_service.repo.set_qbo_identity.assert_not_called()  # NO identity theft


def test_address_fast_path_local_side_conflict_raises_before_legacy_rediscovery():
    """Local-side-only conflict shape, Address twin. REWRITTEN BY U-287.

    Under U-277 this fell through and relied on `protected_address_id` to spare
    Address 55 when the by-street/city search re-found it. When that search MISSED
    — a corrected street line, a changed city — the fall-through minted a duplicate
    Address and stamped PA-99's identity onto it, stealing it from Address 55.
    """
    connector, mapping_repo, address_service, reconciliation_repo = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(
        qbo_id="PA-99", realm_id="realm-1", line1="123 Main", city="Austin"
    )
    direct_hit = SimpleNamespace(
        id=55, street_one="123 Main", street_two="", city="Austin", state="OLD", zip="00000"
    )
    address_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_address_id.return_value = SimpleNamespace(id=3, qbo_physical_address_id=7)
    mapping_repo.read_by_qbo_physical_address_id.return_value = None
    # The by-street/city MISS is the dangerous variant the old guard did not cover.
    address_service.read_by_street_one_and_city.return_value = None
    address_service.create.return_value = SimpleNamespace(id=77)
    address_service.repo.update_by_id.side_effect = lambda a: a

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with pytest.raises(ValueError):
        connector.sync_from_qbo_to_address(qbo_physical_address.id)

    reconciliation_repo.create.assert_called_once()
    assert direct_hit.state == "OLD"  # untouched, never overwritten with PA-99's data
    address_service.create.assert_not_called()  # NO duplicate Address minted
    address_service.repo.update_by_id.assert_not_called()  # NO write to ANY Address
    address_service.repo.set_qbo_identity.assert_not_called()  # NO identity theft


def test_address_fast_path_hit_self_heals_missing_mapping():
    connector, mapping_repo, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, street_one="", street_two="", city="", state="", zip="")
    address_service.read_by_qbo_identity.return_value = direct_hit
    address_service.repo.update_by_id.side_effect = lambda a: a
    mapping_repo.read_by_address_id.return_value = None
    mapping_repo.read_by_qbo_physical_address_id.return_value = None

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    connector.sync_from_qbo_to_address(qbo_physical_address.id)

    mapping_repo.create.assert_called_once_with(
        address_id=55, qbo_physical_address_id=qbo_physical_address.id
    )


def test_address_fast_path_self_heal_race_escalates_to_recorded_conflict():
    connector, mapping_repo, address_service, reconciliation_repo = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, street_one="", street_two="", city="", state="", zip="")
    address_service.read_by_qbo_identity.return_value = direct_hit
    address_service.repo.update_by_id.side_effect = lambda a: a
    mapping_repo.read_by_address_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_physical_address_id.side_effect = [
        None, SimpleNamespace(id=9, address_id=3, qbo_physical_address_id=qbo_physical_address.id)
    ]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    connector.sync_from_qbo_to_address(qbo_physical_address.id)

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "address_identity_conflict"


def test_address_fast_path_hit_consistent_skips_mapping_table_write():
    connector, mapping_repo, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(
        qbo_id="PA-99", realm_id="realm-1", line1="456 Elm"
    )
    direct_hit = SimpleNamespace(id=55, street_one="", street_two="", city="", state="", zip="")
    address_service.read_by_qbo_identity.return_value = direct_hit
    address_service.repo.update_by_id.side_effect = lambda a: a
    mapping_repo.read_by_address_id.return_value = SimpleNamespace(
        id=1, qbo_physical_address_id=qbo_physical_address.id
    )
    mapping_repo.read_by_qbo_physical_address_id.return_value = SimpleNamespace(id=1, address_id=55)

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    result = connector.sync_from_qbo_to_address(qbo_physical_address.id)

    assert result.street_one == "456 Elm"
    mapping_repo.create.assert_not_called()
    address_service.create.assert_not_called()
    # Identity is already correct by construction on the fast path — must not re-stamp.
    address_service.repo.set_qbo_identity.assert_not_called()


def test_address_fast_path_update_returns_none_raises_runtime_error():
    """ROWVERSION race: a concurrent writer touched the fast-path-matched
    Address between the read and this UPDATE, so update_by_id() affects 0
    rows and returns None. Must raise cleanly (mirrors the adjacent legacy
    path's own guard), not propagate a bare None into an .id access.

    RuntimeError, deliberately NOT ValueError (U-291): a ROWVERSION race is
    transient, not a permanent data problem — record_projection_error's rule 2
    classifies a plain ValueError as a permanent SKIP, which would advance the
    watermark past this record anyway. Was ValueError pre-U-291; renamed from
    test_address_fast_path_update_returns_none_raises_value_error."""
    connector, mapping_repo, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, street_one="", street_two="", city="", state="", zip="")
    address_service.read_by_qbo_identity.return_value = direct_hit
    address_service.repo.update_by_id.return_value = None
    mapping_repo.read_by_address_id.return_value = SimpleNamespace(
        id=1, qbo_physical_address_id=qbo_physical_address.id
    )
    mapping_repo.read_by_qbo_physical_address_id.return_value = SimpleNamespace(id=1, address_id=55)

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address

    with pytest.raises(RuntimeError, match="Failed to update Address"):
        connector.sync_from_qbo_to_address(qbo_physical_address.id)


def test_address_legacy_path_update_returns_none_raises_runtime_error():
    """The legacy (non-fast-path) update branch has its own INDEPENDENT copy of
    the same ROWVERSION-race guard (a separate inline block, not a shared
    closure with the fast path) — U-291 found and fixed both, not just the
    fast-path copy the board's shorthand named."""
    connector, mapping_repo, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")
    address_service.read_by_qbo_identity.return_value = None  # fast path misses
    mapping_repo.read_by_qbo_physical_address_id.return_value = SimpleNamespace(id=1, address_id=55)
    address_service.read_by_id.return_value = SimpleNamespace(
        id=55, street_one="", street_two="", city="", state="", zip="", modified_datetime=None
    )
    address_service.repo.update_by_id.return_value = None

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address

    with pytest.raises(RuntimeError, match="Failed to update Address"):
        connector.sync_from_qbo_to_address(qbo_physical_address.id)


def test_address_fast_path_miss_falls_back_to_mapping_table_path():
    connector, mapping_repo, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")
    address_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_physical_address_id.return_value = None
    address_service.read_by_street_one_and_city.return_value = None
    created = SimpleNamespace(id=77)
    address_service.create.return_value = created
    mapping_repo.read_by_address_id.return_value = None

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    result = connector.sync_from_qbo_to_address(qbo_physical_address.id)

    address_service.read_by_qbo_identity.assert_called_once_with("PA-99", "realm-1")
    assert result is created
    address_service.create.assert_called_once()


def test_address_fast_path_skipped_entirely_when_no_qbo_id():
    """A record with no external qbo_id can't possibly have a dbo-native
    identity match — the fast-path lookup should not even be attempted."""
    connector, mapping_repo, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id=None)
    mapping_repo.read_by_qbo_physical_address_id.return_value = None
    address_service.read_by_street_one_and_city.return_value = None
    address_service.create.return_value = SimpleNamespace(id=1)
    mapping_repo.read_by_address_id.return_value = None

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    connector.sync_from_qbo_to_address(qbo_physical_address.id)

    address_service.read_by_qbo_identity.assert_not_called()
