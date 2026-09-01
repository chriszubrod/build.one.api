"""Pure-logic tests for U-277 (Phase-4) / U-350 / U-351: repoint the
`company_info` + `physical_address` connector families' identity resolution off
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
  3. PhysicalAddressAddressConnector's identity resolution — as of U-351 this is
     ALSO the DBO-ONLY fast path, mirroring Section 2 one-for-one: no
     qbo.PhysicalAddressAddress read or write of any kind. One divergence from
     Company: `sync_from_qbo_to_address` takes no separate realm_id parameter at
     all (realm comes straight from `qbo_physical_address.realm_id`), so there is
     no connector-level fallback to test — see Section 3's own header.

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
#
# U-351 UPDATE: this family is now the DBO-ONLY fast path
# (`run_identity_fastpath_dbo_only`), mirroring Section 2's CompanyInfoCompanyConnector
# one-for-one. No qbo.PhysicalAddressAddress read or write of any kind, so there is no
# mapping-table fallback, no self-heal, and no mapping-vs-dbo conflict state left to
# test. A hit updates fields and writes nothing else; a genuine miss adopts by
# (street_one, city) or creates, then stamps identity under the candidate's own lock.
# One Address-specific divergence from Company: `sync_from_qbo_to_address` takes no
# separate realm_id parameter at all — realm comes straight from
# `qbo_physical_address.realm_id`, so there is no connector-level fallback to test
# (Company's U-277 fallback has no analog here).


def _build_address_connector():
    address_service = Mock()
    address_service.repo = Mock()
    reconciliation_repo = Mock()
    qbo_physical_address_service = Mock()
    qbo_physical_address_service.repo = Mock()
    connector = PhysicalAddressAddressConnector(
        address_service=address_service,
        qbo_physical_address_service=qbo_physical_address_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, address_service, reconciliation_repo


def test_address_direct_hit_updates_fields_no_create_or_stamp():
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(
        qbo_id="PA-99", realm_id="realm-1", line1="123 Main", city="Austin",
    )
    direct_hit = SimpleNamespace(id=55, street_one="Old St", street_two="", city="Old City", state="OK", zip="00000")
    address_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, street_one="123 Main", city="Austin")
    address_service.repo.update_by_id.return_value = updated

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    result = connector.sync_from_qbo_to_address(qbo_physical_address.id)

    assert result is updated
    address_service.repo.update_by_id.assert_called_once()
    address_service.create.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()
    address_service.read_by_street_one_and_city.assert_not_called()


def test_address_direct_hit_always_overwrites_fields():
    """QBO is source of truth for physical_address — same as company_info, the
    write is a raw, unconditional overwrite (no preserve_human_edited_*)."""
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(
        qbo_id="PA-99", realm_id="realm-1", line1="New St", line2="Suite 2",
        city="New City", country_sub_division_code="TX", postal_code="78701",
    )
    direct_hit = SimpleNamespace(
        id=55, street_one="Curated Old St", street_two="", city="Old City", state="OK", zip="00000",
    )
    address_service.read_by_qbo_identity.return_value = direct_hit
    address_service.repo.update_by_id.side_effect = lambda a: a

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    result = connector.sync_from_qbo_to_address(qbo_physical_address.id)

    assert result.street_one == "New St"
    assert result.street_two == "Suite 2"
    assert result.city == "New City"
    assert result.state == "TX"
    assert result.zip == "78701"


def test_address_genuine_miss_creates_new_and_stamps_identity():
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(
        qbo_id="PA-99", realm_id="realm-1", line1="123 Main", city="Austin",
        country_sub_division_code="TX", postal_code="78701",
    )
    address_service.read_by_qbo_identity.return_value = None
    address_service.read_by_street_one_and_city.return_value = None
    created = SimpleNamespace(id=300, qbo_id=None, realm_id=None)
    address_service.create.return_value = created
    stamped = SimpleNamespace(id=300, qbo_id="PA-99", realm_id="realm-1")
    address_service.read_by_id.side_effect = [created, stamped]

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_to_address(qbo_physical_address.id)

    assert result is stamped
    address_service.create.assert_called_once_with(
        street_one="123 Main", street_two="", city="Austin", state="TX", zip="78701",
    )
    address_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="PA-99", realm_id="realm-1"
    )


def test_address_genuine_miss_adopts_existing_unmapped_by_street_and_city():
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(
        qbo_id="PA-99", realm_id="realm-1", line1="123 Main", city="Austin",
        country_sub_division_code="TX", postal_code="78701",
    )
    address_service.read_by_qbo_identity.return_value = None
    existing = SimpleNamespace(
        id=150, qbo_id=None, realm_id=None, street_one="Old", street_two="", city="Old City",
        state="OK", zip="00000",
    )
    address_service.read_by_street_one_and_city.return_value = existing
    address_service.repo.update_by_id.side_effect = lambda a: a
    stamped = SimpleNamespace(id=150, qbo_id="PA-99", realm_id="realm-1", street_one="123 Main")
    address_service.read_by_id.side_effect = [existing, stamped]

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_to_address(qbo_physical_address.id)

    assert result is stamped
    assert existing.street_one == "123 Main"
    assert existing.city == "Austin"
    address_service.create.assert_not_called()
    address_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="PA-99", realm_id="realm-1"
    )


def test_address_blank_incoming_street_or_city_skips_the_adopt_lookup_and_creates():
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(
        qbo_id="PA-99", realm_id="realm-1", line1=None, city=None,
        country_sub_division_code="TX", postal_code="78701",
    )
    address_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=300, street_one="")
    address_service.create.return_value = created
    address_service.read_by_id.side_effect = [created, created]

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        connector.sync_from_qbo_to_address(qbo_physical_address.id)

    address_service.read_by_street_one_and_city.assert_not_called()
    address_service.create.assert_called_once_with(street_one="", street_two="", city="", state="TX", zip="78701")


def test_address_resolve_candidate_does_not_mutate_or_persist_the_adopted_row():
    """Mirrors CompanyInfoCompanyConnector's identical guard (U-350): the field
    write happens only in _stamp_address_identity, atomically with the identity
    stamp under the candidate's own lock — resolve_candidate itself must be PURE."""
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1", line1="123 Main", city="Austin")
    existing = SimpleNamespace(
        id=150, qbo_id=None, realm_id=None, street_one="Untouched", street_two="", city="Untouched City",
        state="OK", zip="00000",
    )
    address_service.read_by_street_one_and_city.return_value = existing

    candidate = connector._resolve_address_candidate(
        qbo_physical_address, street_one="123 Main", street_two="", city="Austin", state="TX", zip_code="78701",
    )

    assert candidate is existing
    assert existing.street_one == "Untouched"
    assert existing.city == "Untouched City"
    address_service.repo.update_by_id.assert_not_called()


def test_address_duplicate_qbo_id_guard_raises_and_records_issue():
    """A street/city-matched Address already carrying a DIFFERENT QboId must NOT
    be returned as the candidate -- stamp_identity's theft-clear would silently
    re-point it. Must raise + record an address_identity_conflict issue instead,
    mirroring the mapping-table-era contract this replaces."""
    connector, address_service, reconciliation_repo = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1", line1="123 Main", city="Austin")
    address_service.read_by_qbo_identity.return_value = None
    existing = SimpleNamespace(id=150, public_id="address-pub-150", qbo_id="PA-OTHER", realm_id="realm-1")
    address_service.read_by_street_one_and_city.return_value = existing

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_to_address(qbo_physical_address.id)

    address_service.repo.update_by_id.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "address_identity_conflict"


def test_address_stamp_time_reread_catches_conflict_the_street_city_lookup_cannot_see():
    """Codex xhigh P2 (U-351): `ReadAddressByStreetOneAndCity` does not project
    `QboId`/`RealmId` (entities/address/sql/dbo.address.sql), so a row returned
    by `read_by_street_one_and_city` always carries `qbo_id=None` in production
    -- `_resolve_address_candidate`'s own `_check_no_conflicting_address_identity`
    call structurally cannot see a conflict there (same shape as
    `ReadCompanyByName`, which likewise omits QboId/RealmId — this is the
    established, already-shipped characteristic of the pattern, not new here).
    The REAL guarantee lives one level down: `stamp_dbo_identity_with_lock`'s
    own `read_by_id` re-read DOES project QboId/RealmId (`ReadAddressById`), so
    it must still catch and raise on the SAME row the early check missed."""
    connector, address_service, reconciliation_repo = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1", line1="123 Main", city="Austin")
    address_service.read_by_qbo_identity.return_value = None
    # Matches the real SQL shape: no qbo_id/realm_id in the street/city projection.
    street_city_match = SimpleNamespace(id=150, public_id="address-pub-150", street_one="123 Main", city="Austin")
    address_service.read_by_street_one_and_city.return_value = street_city_match
    # The stamp-time read_by_id re-read uses the REAL column set and reveals the
    # conflict the street/city lookup couldn't.
    address_service.read_by_id.return_value = SimpleNamespace(
        id=150, public_id="address-pub-150", qbo_id="PA-OTHER", realm_id="realm-1",
    )

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        with pytest.raises(ValueError, match="already carries QBO identity PA-OTHER"):
            connector.sync_from_qbo_to_address(qbo_physical_address.id)

    address_service.repo.update_by_id.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "address_identity_conflict"


def test_address_duplicate_guard_catches_same_qbo_id_different_realm():
    """QBO ids are only unique WITHIN a realm, so a QboId-only check would let a
    same-QboId-different-realm row through and overwrite its fields before
    _stamp_address_identity's own (qbo_id AND realm_id) check ever runs."""
    connector, address_service, reconciliation_repo = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1", line1="123 Main", city="Austin")
    address_service.read_by_qbo_identity.return_value = None
    existing = SimpleNamespace(
        id=150, public_id="address-pub-150", qbo_id="PA-99", realm_id="realm-OTHER", street_one="Untouched",
    )
    address_service.read_by_street_one_and_city.return_value = existing

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_to_address(qbo_physical_address.id)

    assert existing.street_one == "Untouched"  # never mutated before the raise
    address_service.repo.update_by_id.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()


def test_address_race_discovered_hit_adopts_racer_without_create():
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")
    racer_row = SimpleNamespace(id=400, qbo_id="PA-99", realm_id="realm-1")
    address_service.read_by_qbo_identity.side_effect = [None, racer_row]
    address_service.repo.update_by_id.side_effect = lambda a: a

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        result = connector.sync_from_qbo_to_address(qbo_physical_address.id)

    assert result is racer_row
    address_service.create.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()
    assert address_service.read_by_qbo_identity.call_args_list == [
        call("PA-99", "realm-1"),
        call("PA-99", "realm-1"),
    ]


def test_address_update_returning_none_raises_runtime_error_not_value_error():
    """A ROWVERSION race on the HIT branch (update_by_id affected 0 rows) must
    raise RuntimeError, NOT ValueError (U-291 discipline, carried through the
    repoint)."""
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")
    address_service.read_by_qbo_identity.return_value = SimpleNamespace(id=55)
    address_service.repo.update_by_id.return_value = None  # race: row gone on write

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_to_address(qbo_physical_address.id)

    address_service.create.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()


def test_address_no_qbo_id_raises():
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id=None)

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_to_address(qbo_physical_address.id)

    address_service.read_by_qbo_identity.assert_not_called()


def test_address_realm_id_comes_straight_from_staging_row_no_connector_fallback():
    """Unlike CompanyInfoCompanyConnector, sync_from_qbo_to_address takes no
    separate realm_id parameter — there is nothing to fall back to, so a falsy
    qbo_physical_address.realm_id must be passed through as-is (None), never
    silently defaulted."""
    connector, address_service, _ = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id=None)
    address_service.read_by_qbo_identity.return_value = None
    address_service.read_by_street_one_and_city.return_value = None
    created = SimpleNamespace(id=1)
    address_service.create.return_value = created
    address_service.read_by_id.side_effect = [created, SimpleNamespace(id=1, qbo_id="PA-99", realm_id=None)]

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        connector.sync_from_qbo_to_address(qbo_physical_address.id)

    assert address_service.read_by_qbo_identity.call_args_list == [
        call("PA-99", None),
        call("PA-99", None),
    ]


def test_address_stamp_identity_refuses_to_overwrite_different_existing_identity():
    connector, address_service, _ = _build_address_connector()
    candidate = SimpleNamespace(id=150)
    address_service.read_by_id.return_value = SimpleNamespace(
        id=150, public_id="address-pub-150", qbo_id="PA-OTHER", realm_id="realm-1",
    )
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")

    with patch(STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match=r"already carries QBO identity PA-OTHER"):
            connector._stamp_address_identity(
                candidate, qbo_physical_address, street_one="123 Main", street_two="", city="Austin",
                state="TX", zip_code="78701",
            )

    address_service.repo.set_qbo_identity.assert_not_called()
    address_service.repo.update_by_id.assert_not_called()  # never mutated before the raise


def test_address_stamp_identity_update_returning_none_raises_runtime_error():
    """A ROWVERSION race between the pre-stamp read and the field-write
    update_by_id call must not silently proceed to stamp identity on a row
    whose write never took."""
    connector, address_service, _ = _build_address_connector()
    candidate = SimpleNamespace(id=150)
    address_service.read_by_id.return_value = SimpleNamespace(
        id=150, qbo_id=None, realm_id=None, street_one="Old",
    )
    address_service.repo.update_by_id.return_value = None  # race: row gone on write
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1", line1="New")

    with patch(STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector._stamp_address_identity(
                candidate, qbo_physical_address, street_one="New", street_two="", city="Austin",
                state="TX", zip_code="78701",
            )

    address_service.repo.set_qbo_identity.assert_not_called()


def test_address_stamp_identity_sanitizes_blank_fields_to_empty_string():
    """Codex xhigh round-1 P1 (U-350), carried over here: on the genuine-miss
    create path, `_resolve_address_candidate`'s own `.create(street_one=... or
    "", ...)` already sanitizes blank incoming fields — but this method's OWN
    apply_fields closure must ALSO sanitize, or it would re-derive the fields RAW
    and immediately overwrite that already-sanitized value with None, which
    `UpdateAddressById`'s `NOT NULL` columns reject. Mutation target: dropping the
    `or ""` here reintroduces the None write."""
    connector, address_service, _ = _build_address_connector()
    candidate = SimpleNamespace(id=150)
    unmapped = SimpleNamespace(id=150, qbo_id=None, realm_id=None, street_one="", street_two="", city="", state="", zip="")
    address_service.read_by_id.return_value = unmapped
    address_service.repo.update_by_id.side_effect = lambda a: a
    qbo_physical_address = _make_qbo_physical_address(
        qbo_id="PA-99", realm_id="realm-1", line1=None, line2=None, city=None,
        country_sub_division_code=None, postal_code=None,
    )

    with patch(STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector._stamp_address_identity(
            candidate, qbo_physical_address, street_one=None, street_two=None, city=None,
            state=None, zip_code=None,
        )

    assert unmapped.street_one == ""
    assert unmapped.street_two == ""
    assert unmapped.city == ""
    assert unmapped.state == ""
    assert unmapped.zip == ""


def test_address_stamp_identity_applies_field_write_atomically_with_stamp():
    """The field write happens INSIDE this method, under the candidate lock, not
    in resolve_candidate — confirms it's actually applied, and that
    write_identity delegates through create_mapping (no mapping row left)."""
    connector, address_service, _ = _build_address_connector()
    candidate = SimpleNamespace(id=150)
    unmapped = SimpleNamespace(
        id=150, qbo_id=None, realm_id=None, street_one="Old", street_two="", city="Old City",
        state="OK", zip="00000",
    )
    address_service.read_by_id.return_value = unmapped
    address_service.repo.update_by_id.side_effect = lambda a: a
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1", line1="New", city="Austin")

    with patch(STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector._stamp_address_identity(
            candidate, qbo_physical_address, street_one="New", street_two="", city="Austin",
            state="TX", zip_code="78701",
        )

    assert unmapped.street_one == "New"
    assert unmapped.city == "Austin"
    address_service.repo.update_by_id.assert_called_once_with(unmapped)
    address_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="PA-99", realm_id="realm-1"
    )
