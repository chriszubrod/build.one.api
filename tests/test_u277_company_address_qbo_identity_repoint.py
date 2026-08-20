"""Pure-logic tests for U-277 (Phase-4): repoint the `company_info` +
`physical_address` connector families' identity resolution off
qbo.CompanyInfo / qbo.CompanyInfoCompany / qbo.PhysicalAddress /
qbo.PhysicalAddressAddress onto dbo.Company / dbo.Address's native
QboId/RealmId (U-238a/c). Mirrors tests/test_u276_customer_project_qbo_identity_repoint.py's
shape exactly — see that file's module docstring for the pattern rationale.

Covers:
  1. CompanyRepository.read_by_qbo_identity / AddressRepository.read_by_qbo_identity
     (sproc call shape) + their service-layer passthroughs (neither entity has
     row-level RBAC, so — unlike Project — no actor scope to thread).
  2. CompanyInfoCompanyConnector / PhysicalAddressAddressConnector's new direct-identity
     fast path: hit updates without the mapping-table hop + self-heals a missing mapping
     row; miss falls through to the pre-existing mapping-table path unchanged.

Out of scope (confirmed at Gate-1): no outbound push anywhere in the codebase reads
dbo.Company.QboId or dbo.Address.QboId to build a QBO reference — U-276's Section 4
(push-helper repoint) has no analog here.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

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
# Same testing shape as U-276's CustomerCustomerConnector section — the
# mapping-conflict cases are unit-tested directly against
# _resolve_mapping_state / _raise_identity_mapping_conflict_issue rather than
# through the full sync_from_qbo_to_company(), because a detected conflict
# falls through to the pre-existing (complex, multi-branch) legacy path
# instead of returning early. What THIS file must prove: (a) the conflict is
# correctly detected and recorded, (b) the dbo-identity-matched row is never
# written to on that path.


def _build_company_connector():
    mapping_repo = Mock()
    company_service = Mock()
    company_service.repo = Mock()
    reconciliation_repo = Mock()
    qbo_company_info_service = Mock()
    qbo_company_info_service.repo = Mock()
    connector = CompanyInfoCompanyConnector(
        mapping_repo=mapping_repo,
        company_service=company_service,
        qbo_company_info_service=qbo_company_info_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, mapping_repo, company_service, reconciliation_repo


def test_company_resolve_mapping_state_consistent():
    connector, mapping_repo, _, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(id=4)
    mapping_repo.read_by_company_id.return_value = SimpleNamespace(id=1, qbo_company_info_id=4)
    mapping_repo.read_by_qbo_company_info_id.return_value = SimpleNamespace(id=1, company_id=55)

    state, _, _ = connector._resolve_mapping_state(company_id=55, qbo_company_info=qbo_company_info)

    assert state == "consistent"


def test_company_resolve_mapping_state_missing():
    connector, mapping_repo, _, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(id=4)
    mapping_repo.read_by_company_id.return_value = None
    mapping_repo.read_by_qbo_company_info_id.return_value = None

    state, _, _ = connector._resolve_mapping_state(company_id=55, qbo_company_info=qbo_company_info)

    assert state == "missing"


def test_company_resolve_mapping_state_qbo_side_conflict():
    connector, mapping_repo, _, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(id=4)
    mapping_repo.read_by_company_id.return_value = None
    mapping_repo.read_by_qbo_company_info_id.return_value = SimpleNamespace(id=2, company_id=9)

    state, by_company, by_qbo_company_info = connector._resolve_mapping_state(
        company_id=55, qbo_company_info=qbo_company_info
    )

    assert state == "conflict"
    assert by_company is None
    assert by_qbo_company_info.company_id == 9


def test_company_resolve_mapping_state_local_side_conflict():
    connector, mapping_repo, _, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(id=4)
    mapping_repo.read_by_company_id.return_value = SimpleNamespace(id=3, qbo_company_info_id=5)
    mapping_repo.read_by_qbo_company_info_id.return_value = None

    state, by_company, by_qbo_company_info = connector._resolve_mapping_state(
        company_id=55, qbo_company_info=qbo_company_info
    )

    assert state == "conflict"
    assert by_company.qbo_company_info_id == 5
    assert by_qbo_company_info is None


def test_company_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(id=4, qbo_id="CI-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, company_id=9, qbo_company_info_id=4)
    local_side = SimpleNamespace(id=3, company_id=55, qbo_company_info_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_company_info=qbo_company_info, dbo_company_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
        realm_id="realm-1",
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "company_identity_conflict"
    # Phrase-level checks, not bare digit substrings — the always-emitted
    # first sentence's own "55"/"4"/"CI-99" would trivially satisfy a plain
    # "in details" check even if the qbo-side/local-side blocks were dropped.
    assert "Company 9 (mapping 2)" in kwargs["details"]       # qbo-side conflicting Company
    assert "DIFFERENT QboCompanyInfo 5" in kwargs["details"]  # local-side conflicting QboCompanyInfo


def test_company_fast_path_hit_updates_without_writing_on_conflict():
    """On a detected conflict, sync_from_qbo_to_company must NOT write to the
    dbo-identity-matched Company (55)."""
    connector, mapping_repo, company_service, reconciliation_repo = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Acme", website="")
    company_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_company_id.return_value = None
    conflicting = SimpleNamespace(id=2, company_id=9, qbo_company_info_id=qbo_company_info.id)
    mapping_repo.read_by_qbo_company_info_id.return_value = conflicting
    # Safe terminal state for the legacy fallback this falls through to: it
    # resolves Company 9 via the pre-existing mapping and updates that (not
    # Company 55, which is this test's actual concern).
    company_service.read_by_id.return_value = SimpleNamespace(
        id=9, name="Other Co", website="", modified_datetime="2026-01-01 00:00:00"
    )
    company_service.repo.update_by_id.side_effect = lambda c: c

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    reconciliation_repo.create.assert_called_once()
    for call in company_service.repo.update_by_id.call_args_list:
        written = call.args[0] if call.args else call.kwargs.get("company")
        assert getattr(written, "id", None) != 55


def test_company_fast_path_local_side_conflict_legacy_rediscovery_does_not_write():
    """Local-side-only conflict shape: by_company (keyed on the fast-path-matched
    Company) points at a DIFFERENT qbo_company_info_id, so Step 1's own mapping
    lookup (keyed on THIS sync's qbo_company_info_id) misses and the legacy
    fallback reaches Step 2's by-name search — which can re-find the exact same
    Company (its name was derived from this same QboCompanyInfo). Must not
    silently overwrite it there either."""
    connector, mapping_repo, company_service, reconciliation_repo = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1", legal_name="Acme Co")
    direct_hit = SimpleNamespace(id=55, name="Acme Co", website="old.example.com")
    company_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_company_id.return_value = SimpleNamespace(id=3, qbo_company_info_id=7)
    mapping_repo.read_by_qbo_company_info_id.return_value = None
    company_service.read_by_name.return_value = direct_hit
    company_service.repo.update_by_id.side_effect = lambda c: c

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    result = connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    reconciliation_repo.create.assert_called_once()
    assert result.id == 55
    assert result.website == "old.example.com"  # untouched, not overwritten with CI-99's data
    for call in company_service.repo.update_by_id.call_args_list:
        written = call.args[0] if call.args else call.kwargs.get("company")
        assert getattr(written, "id", None) != 55


def test_company_fast_path_hit_self_heals_missing_mapping():
    connector, mapping_repo, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Acme", website="")
    company_service.read_by_qbo_identity.return_value = direct_hit
    company_service.repo.update_by_id.side_effect = lambda c: c
    mapping_repo.read_by_company_id.return_value = None
    mapping_repo.read_by_qbo_company_info_id.return_value = None

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    mapping_repo.create.assert_called_once_with(company_id=55, qbo_company_info_id=qbo_company_info.id)


def test_company_fast_path_self_heal_race_escalates_to_recorded_conflict():
    """A concurrent sync can turn 'missing' into 'conflict' between the
    pre-check and the create() call — the create() failure must re-check and
    record a real conflict issue when that's what actually happened."""
    connector, mapping_repo, company_service, reconciliation_repo = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Acme", website="")
    company_service.read_by_qbo_identity.return_value = direct_hit
    company_service.repo.update_by_id.side_effect = lambda c: c
    mapping_repo.read_by_company_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_company_info_id.side_effect = [
        None, SimpleNamespace(id=9, company_id=3, qbo_company_info_id=qbo_company_info.id)
    ]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "company_identity_conflict"


def test_company_fast_path_hit_consistent_skips_mapping_table_write():
    connector, mapping_repo, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1", legal_name="Acme")
    direct_hit = SimpleNamespace(id=55, name="", website="")
    company_service.read_by_qbo_identity.return_value = direct_hit
    company_service.repo.update_by_id.side_effect = lambda c: c
    mapping_repo.read_by_company_id.return_value = SimpleNamespace(id=1, qbo_company_info_id=qbo_company_info.id)
    mapping_repo.read_by_qbo_company_info_id.return_value = SimpleNamespace(id=1, company_id=55)

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    result = connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    assert result.name == "Acme"
    mapping_repo.create.assert_not_called()
    company_service.create.assert_not_called()
    # Identity is already correct by construction on the fast path — must not re-stamp.
    company_service.repo.set_qbo_identity.assert_not_called()


def test_company_fast_path_update_returns_none_raises_value_error():
    """ROWVERSION race: a concurrent writer touched the fast-path-matched
    Company between the read and this UPDATE, so update_by_id() affects 0
    rows and returns None. Must raise cleanly (mirrors the adjacent legacy
    path's own guard), not propagate a bare None into an .id access."""
    connector, mapping_repo, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Acme", website="")
    company_service.read_by_qbo_identity.return_value = direct_hit
    company_service.repo.update_by_id.return_value = None
    mapping_repo.read_by_company_id.return_value = SimpleNamespace(id=1, qbo_company_info_id=qbo_company_info.id)
    mapping_repo.read_by_qbo_company_info_id.return_value = SimpleNamespace(id=1, company_id=55)

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info

    with pytest.raises(ValueError, match="Failed to update Company"):
        connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")


def test_company_fast_path_miss_falls_back_to_mapping_table_path():
    """No dbo row carries this identity yet -> the pre-existing mapping-table-
    based logic must still run, untouched."""
    connector, mapping_repo, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id="realm-1")
    company_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_company_info_id.return_value = None
    company_service.read_by_name = Mock(return_value=None)
    created = SimpleNamespace(id=77)
    company_service.create.return_value = created
    mapping_repo.read_by_company_id.return_value = None

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    result = connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    company_service.read_by_qbo_identity.assert_called_once_with("CI-99", "realm-1")
    assert result is created
    company_service.create.assert_called_once()


def test_company_fast_path_skipped_entirely_when_no_qbo_id():
    """A record with no external qbo_id can't possibly have a dbo-native
    identity match — the fast-path lookup should not even be attempted."""
    connector, mapping_repo, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id=None)
    mapping_repo.read_by_qbo_company_info_id.return_value = None
    company_service.read_by_name = Mock(return_value=None)
    company_service.create.return_value = SimpleNamespace(id=1)
    mapping_repo.read_by_company_id.return_value = None

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    connector.sync_from_qbo_to_company(qbo_company_info.id, "realm-1")

    company_service.read_by_qbo_identity.assert_not_called()


def test_company_fast_path_uses_connector_realm_id_when_qbo_company_info_realm_id_is_falsy():
    """The `qbo_company_info.realm_id or realm_id` fallback has no analog in
    the sibling connectors (Customer/Project/Address take no separate
    realm_id parameter at all) — it's new U-277 logic. Prove its precedence
    directly with the two sources set to DIFFERENT values, rather than
    relying on every other test's fixtures happening to agree."""
    connector, mapping_repo, company_service, _ = _build_company_connector()
    qbo_company_info = _make_qbo_company_info(qbo_id="CI-99", realm_id=None)
    company_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_company_info_id.return_value = None
    company_service.read_by_name = Mock(return_value=None)
    company_service.create.return_value = SimpleNamespace(id=1)
    mapping_repo.read_by_company_id.return_value = None

    connector.qbo_company_info_service.repo.read_by_id.return_value = qbo_company_info
    connector.sync_from_qbo_to_company(qbo_company_info.id, "connector-realm")

    company_service.read_by_qbo_identity.assert_called_once_with("CI-99", "connector-realm")


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


def test_address_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(id=100, qbo_id="PA-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, address_id=9, qbo_physical_address_id=100)
    local_side = SimpleNamespace(id=3, address_id=55, qbo_physical_address_id=5)

    connector._raise_identity_mapping_conflict_issue(
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


def test_address_fast_path_hit_updates_without_writing_on_conflict():
    """On a detected conflict, sync_from_qbo_to_address must NOT write to the
    dbo-identity-matched Address (55)."""
    connector, mapping_repo, address_service, reconciliation_repo = _build_address_connector()
    qbo_physical_address = _make_qbo_physical_address(qbo_id="PA-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, street_one="", street_two="", city="", state="", zip="")
    address_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_address_id.return_value = None
    conflicting = SimpleNamespace(id=2, address_id=9, qbo_physical_address_id=qbo_physical_address.id)
    mapping_repo.read_by_qbo_physical_address_id.return_value = conflicting
    # Safe terminal state for the legacy fallback this falls through to: it
    # resolves Address 9 via the pre-existing mapping and updates that (not
    # Address 55, which is this test's actual concern).
    address_service.read_by_id.return_value = SimpleNamespace(
        id=9, street_one="", street_two="", city="", state="", zip="",
        modified_datetime="2026-01-01 00:00:00",
    )
    address_service.repo.update_by_id.side_effect = lambda a: a

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    connector.sync_from_qbo_to_address(qbo_physical_address.id)

    reconciliation_repo.create.assert_called_once()
    for call in address_service.repo.update_by_id.call_args_list:
        written = call.args[0] if call.args else call.kwargs.get("address")
        assert getattr(written, "id", None) != 55


def test_address_fast_path_local_side_conflict_legacy_rediscovery_does_not_write():
    """Local-side-only conflict shape, Address twin of the Company test above:
    the legacy fallback's own by-street/city rediscovery can re-find the exact
    same Address (its street_one/city were derived from this same
    QboPhysicalAddress) — must not silently overwrite it there either."""
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
    address_service.read_by_street_one_and_city.return_value = direct_hit
    address_service.repo.update_by_id.side_effect = lambda a: a

    connector.qbo_physical_address_service.repo.read_by_id.return_value = qbo_physical_address
    result = connector.sync_from_qbo_to_address(qbo_physical_address.id)

    reconciliation_repo.create.assert_called_once()
    assert result.id == 55
    assert result.state == "OLD"  # untouched, not overwritten with PA-99's data
    for call in address_service.repo.update_by_id.call_args_list:
        written = call.args[0] if call.args else call.kwargs.get("address")
        assert getattr(written, "id", None) != 55


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


def test_address_fast_path_update_returns_none_raises_value_error():
    """ROWVERSION race: a concurrent writer touched the fast-path-matched
    Address between the read and this UPDATE, so update_by_id() affects 0
    rows and returns None. Must raise cleanly (mirrors the adjacent legacy
    path's own guard), not propagate a bare None into an .id access."""
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

    with pytest.raises(ValueError, match="Failed to update Address"):
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
