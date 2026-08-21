"""U-281 (Phase-4 prerequisite, `account` family): give
BillBillConnector._get_ap_account_ref's one live business fact — "which QBO
account is Accounts Payable for this realm" — a dbo-native home on
dbo.Company, populated by QboAccountService's scheduled qbo.Account pull.

`account` has no dbo entity/mapping table of its own (confirmed at Gate-1),
so this is reference-only: no identity_fastpath() repoint, just one cached
derived fact written onto a *different* entity (Company).

Covers:
  1. CompanyRepository.read_by_realm_id / .set_ap_account (sproc call shape)
     + CompanyService's thin passthroughs.
  2. select_ap_account() — the pure selection helper shared by the cache
     derivation and the live-scan fallback, so the two can never diverge.
  3. QboAccountService._sync_ap_account_cache — re-derives from the FULL
     local qbo.Account mirror (not just the batch this pull fetched) and is
     failure-isolated from the account pull itself.
  4. BillBillConnector._get_ap_account_ref — cache-hit skips the live scan
     entirely; cache-miss (no Company row, or Company row with no cached
     value) falls back to the pre-U-281 scan unchanged.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest


def _make_qbo_account(**overrides):
    defaults = dict(qbo_id="7", name="Accounts Payable (A/P)", account_type="Accounts Payable")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- Section 1: repo/service-level sproc call shape ---


def test_company_repo_read_by_realm_id_calls_sproc():
    from entities.company.persistence.repo import CompanyRepository

    repo = CompanyRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.company.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.company.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_realm_id("realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadCompanyByRealmId"
    assert mock_call.call_args.kwargs["params"] == {"RealmId": "realm-1"}


def test_company_repo_read_by_realm_id_maps_ap_account_fields_from_a_real_row():
    """_from_db's ap_account_qbo_id/ap_account_name use getattr(..., default=None)
    -- a wrong column/attribute name fails SILENTLY (always None), not with an
    exception, so this must be exercised with a real row carrying those columns,
    not just cursor.fetchone.return_value = None (which short-circuits before the
    mapping block ever runs)."""
    from entities.company.persistence.repo import CompanyRepository
    import base64

    repo = CompanyRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(
        Id=1,
        PublicId="11111111-1111-1111-1111-111111111111",
        RowVersion=base64.b64decode(b"AAAAAAAAB9E="),
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime="2026-01-01 00:00:00",
        Name="Acme Co",
        Website=None,
        OrganizationId=1,
        CreatedByUserId=17,
        ModifiedByUserId=17,
        QboId="1",
        RealmId="realm-1",
        APAccountQboId="7",
        APAccountName="Accounts Payable (A/P)",
    )

    with patch("entities.company.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.company.persistence.repo.call_procedure"
    ):
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        company = repo.read_by_realm_id("realm-1")

    assert company.ap_account_qbo_id == "7"
    assert company.ap_account_name == "Accounts Payable (A/P)"


def test_company_repo_set_ap_account_calls_sproc_and_does_not_crash_on_narrow_output():
    """SetCompanyApAccount's OUTPUT (Id/RealmId/APAccountQboId/APAccountName only)
    is narrower than a full Company row — this must NOT be routed through
    _from_db (which requires RowVersion/PublicId/etc and would AttributeError)."""
    from entities.company.persistence.repo import CompanyRepository

    repo = CompanyRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(
        Id=1, RealmId="realm-1", APAccountQboId="7", APAccountName="Accounts Payable (A/P)"
    )

    with patch("entities.company.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.company.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = repo.set_ap_account(
            realm_id="realm-1", ap_account_qbo_id="7", ap_account_name="Accounts Payable (A/P)"
        )

    assert result is None  # mirrors set_qbo_identity's own None return
    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "SetCompanyApAccount"
    assert mock_call.call_args.kwargs["params"] == {
        "RealmId": "realm-1",
        "APAccountQboId": "7",
        "APAccountName": "Accounts Payable (A/P)",
    }


def test_company_repo_set_ap_account_warns_on_no_matching_realm():
    from entities.company.persistence.repo import CompanyRepository

    repo = CompanyRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None  # no Company row matched RealmId

    with patch("entities.company.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.company.persistence.repo.call_procedure"
    ), patch("entities.company.persistence.repo.logger") as mock_logger:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.set_ap_account(realm_id="no-such-realm", ap_account_qbo_id="7", ap_account_name="AP")

    mock_logger.warning.assert_called_once()


def test_company_service_read_by_realm_id_is_a_thin_passthrough():
    from entities.company.business.service import CompanyService

    repo = Mock()
    sentinel = SimpleNamespace(id=1, ap_account_qbo_id="7")
    repo.read_by_realm_id.return_value = sentinel
    service = CompanyService(repo=repo)

    result = service.read_by_realm_id("realm-1")

    repo.read_by_realm_id.assert_called_once_with("realm-1")
    assert result is sentinel


def test_company_service_set_ap_account_is_a_thin_passthrough():
    from entities.company.business.service import CompanyService

    repo = Mock()
    service = CompanyService(repo=repo)

    service.set_ap_account(realm_id="realm-1", ap_account_qbo_id="7", ap_account_name="AP")

    repo.set_ap_account.assert_called_once_with(
        realm_id="realm-1", ap_account_qbo_id="7", ap_account_name="AP"
    )


# --- Section 2: select_ap_account (shared pure selection helper) ---


def test_select_ap_account_picks_first_ap_type_match():
    from integrations.intuit.qbo.account.business.service import select_ap_account

    accounts = [
        _make_qbo_account(qbo_id="1", name="Bank Account", account_type="Bank"),
        _make_qbo_account(qbo_id="7", name="Accounts Payable (A/P)", account_type="Accounts Payable"),
        _make_qbo_account(qbo_id="9", name="Zzz AP Sub", account_type="Accounts Payable"),
    ]

    result = select_ap_account(accounts)

    assert result.qbo_id == "7"  # first match in the given (Name ASC) order, not the last


def test_select_ap_account_no_match_returns_none():
    from integrations.intuit.qbo.account.business.service import select_ap_account

    accounts = [_make_qbo_account(qbo_id="1", name="Bank Account", account_type="Bank")]

    assert select_ap_account(accounts) is None


def test_select_ap_account_empty_list_returns_none():
    from integrations.intuit.qbo.account.business.service import select_ap_account

    assert select_ap_account([]) is None


# --- Section 3: QboAccountService._sync_ap_account_cache / sync_from_qbo ---


def _make_service_for_sync(repo=None, company_service=None):
    from integrations.intuit.qbo.account.business.service import QboAccountService

    return QboAccountService(
        repo=repo or MagicMock(), company_service=company_service or MagicMock()
    )


def test_sync_ap_account_cache_derives_from_full_local_mirror_not_just_this_batch():
    """An incremental pull's `qbo_accounts` may not include the AP account row
    at all (it wasn't the thing that changed) — the cache derivation must
    re-query the full local qbo.Account mirror, not the fetched batch."""
    svc = _make_service_for_sync()
    svc.repo.read_by_realm_id.return_value = [
        _make_qbo_account(qbo_id="7", name="Accounts Payable (A/P)", account_type="Accounts Payable"),
    ]
    svc.company_service.read_by_realm_id.return_value = None  # nothing cached yet

    svc._sync_ap_account_cache("realm-1")

    svc.repo.read_by_realm_id.assert_called_once_with("realm-1")
    svc.company_service.set_ap_account.assert_called_once_with(
        realm_id="realm-1", ap_account_qbo_id="7", ap_account_name="Accounts Payable (A/P)"
    )


def test_sync_ap_account_cache_stamps_none_when_no_ap_account_exists():
    svc = _make_service_for_sync()
    svc.repo.read_by_realm_id.return_value = [
        _make_qbo_account(qbo_id="1", name="Bank Account", account_type="Bank"),
    ]
    svc.company_service.read_by_realm_id.return_value = SimpleNamespace(
        ap_account_qbo_id="7", ap_account_name="Accounts Payable (A/P)"  # currently cached differs
    )

    svc._sync_ap_account_cache("realm-1")

    svc.company_service.set_ap_account.assert_called_once_with(
        realm_id="realm-1", ap_account_qbo_id=None, ap_account_name=None
    )


def test_sync_ap_account_cache_skips_the_write_when_derived_value_already_matches_cache():
    """The write bumps dbo.Company.RowVersion as a side effect (any UPDATE to a
    ROWVERSION-tracked row advances it) -- skip it entirely on the overwhelmingly
    common case where nothing actually changed, per the /simplify efficiency pass."""
    svc = _make_service_for_sync()
    svc.repo.read_by_realm_id.return_value = [
        _make_qbo_account(qbo_id="7", name="Accounts Payable (A/P)", account_type="Accounts Payable"),
    ]
    svc.company_service.read_by_realm_id.return_value = SimpleNamespace(
        ap_account_qbo_id="7", ap_account_name="Accounts Payable (A/P)"  # already correct
    )

    svc._sync_ap_account_cache("realm-1")

    svc.company_service.set_ap_account.assert_not_called()


def test_sync_ap_account_cache_is_failure_isolated_from_the_account_pull():
    svc = _make_service_for_sync()
    svc.repo.read_by_realm_id.side_effect = Exception("boom")

    # Must not raise — a Company-side failure must never fail the account pull.
    svc._sync_ap_account_cache("realm-1")

    svc.company_service.set_ap_account.assert_not_called()


def test_sync_ap_account_cache_is_failure_isolated_from_a_company_write_failure():
    """The docstring specifically promises isolation from a Company-side WRITE
    problem, not just a qbo.Account read problem -- exercise that branch
    directly rather than relying on the read-failure test to stand in for it."""
    svc = _make_service_for_sync()
    svc.repo.read_by_realm_id.return_value = [
        _make_qbo_account(qbo_id="7", name="Accounts Payable (A/P)", account_type="Accounts Payable"),
    ]
    svc.company_service.read_by_realm_id.return_value = None  # nothing cached yet -- must write
    svc.company_service.set_ap_account.side_effect = Exception("boom")

    # Must not raise — a Company-side write failure must never fail the account pull.
    svc._sync_ap_account_cache("realm-1")

    svc.company_service.set_ap_account.assert_called_once_with(
        realm_id="realm-1", ap_account_qbo_id="7", ap_account_name="Accounts Payable (A/P)"
    )


def test_sync_from_qbo_calls_ap_account_cache_after_upsert_loop():
    from integrations.intuit.qbo.account.external.schemas import QboAccount as QboAccountSchema

    svc = _make_service_for_sync()
    svc.repo.read_by_realm_id.return_value = []
    fetched = QboAccountSchema.model_validate(
        {
            "Id": "1",
            "SyncToken": "0",
            "Name": "Cash",
            "Active": True,
            "AccountType": "Bank",
            "Classification": "Asset",
        }
    )

    with patch.object(svc, "_upsert_account", return_value=MagicMock()), patch(
        "integrations.intuit.qbo.account.business.service.QboAccountClient"
    ) as client_cls, patch.object(svc, "_sync_ap_account_cache") as mock_sync_cache:
        client_cls.return_value.__enter__.return_value.query_all_accounts.return_value = [fetched]
        svc.sync_from_qbo(realm_id="realm-1", last_updated_time=None)

    mock_sync_cache.assert_called_once_with("realm-1")


def test_sync_from_qbo_skips_ap_account_cache_on_empty_incremental_pull():
    """Nothing changed since last_updated_time -> early return, no re-derivation
    needed (the previously-cached value, if any, is still correct)."""
    svc = _make_service_for_sync()

    with patch(
        "integrations.intuit.qbo.account.business.service.QboAccountClient"
    ) as client_cls, patch.object(svc, "_sync_ap_account_cache") as mock_sync_cache:
        client_cls.return_value.__enter__.return_value.query_all_accounts.return_value = []
        svc.sync_from_qbo(realm_id="realm-1", last_updated_time="2026-08-01T00:00:00Z")

    mock_sync_cache.assert_not_called()


# --- Section 4: BillBillConnector._get_ap_account_ref repoint ---


def _make_connector():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    connector = BillBillConnector(
        mapping_repo=MagicMock(),
        bill_service=MagicMock(),
        vendor_service=MagicMock(),
        reconciliation_repo=MagicMock(),
        qbo_account_repo=MagicMock(),
        company_service=MagicMock(),
    )
    return connector


def test_get_ap_account_ref_cache_hit_never_scans_qbo_account():
    connector = _make_connector()
    connector.company_service.read_by_realm_id.return_value = SimpleNamespace(
        ap_account_qbo_id="7", ap_account_name="Accounts Payable (A/P)"
    )

    ref = connector._get_ap_account_ref("realm-1")

    assert ref.value == "7"
    assert ref.name == "Accounts Payable (A/P)"
    connector.qbo_account_repo.read_by_realm_id.assert_not_called()


def test_get_ap_account_ref_falls_back_when_no_company_row():
    connector = _make_connector()
    connector.company_service.read_by_realm_id.return_value = None
    connector.qbo_account_repo.read_by_realm_id.return_value = [
        _make_qbo_account(qbo_id="7", name="Accounts Payable (A/P)", account_type="Accounts Payable"),
    ]

    ref = connector._get_ap_account_ref("realm-1")

    assert ref.value == "7"
    assert ref.name == "Accounts Payable (A/P)"
    connector.qbo_account_repo.read_by_realm_id.assert_called_once_with("realm-1")


def test_get_ap_account_ref_falls_back_when_company_row_has_no_cached_value():
    """Company row exists (already QBO-connected) but the AP field hasn't been
    populated yet (pre-U-281 rollout window / cache not yet stamped)."""
    connector = _make_connector()
    connector.company_service.read_by_realm_id.return_value = SimpleNamespace(
        ap_account_qbo_id=None, ap_account_name=None
    )
    connector.qbo_account_repo.read_by_realm_id.return_value = [
        _make_qbo_account(qbo_id="7", name="Accounts Payable (A/P)", account_type="Accounts Payable"),
    ]

    ref = connector._get_ap_account_ref("realm-1")

    assert ref.value == "7"
    connector.qbo_account_repo.read_by_realm_id.assert_called_once_with("realm-1")


def test_get_ap_account_ref_falls_back_when_company_read_raises():
    """A transient dbo.Company error is unrelated to qbo.Account -- it must not
    newly break a live Bill push that never touched dbo.Company before U-281."""
    connector = _make_connector()
    connector.company_service.read_by_realm_id.side_effect = Exception("db boom")
    connector.qbo_account_repo.read_by_realm_id.return_value = [
        _make_qbo_account(qbo_id="7", name="Accounts Payable (A/P)", account_type="Accounts Payable"),
    ]

    ref = connector._get_ap_account_ref("realm-1")  # must not raise

    assert ref.value == "7"
    connector.qbo_account_repo.read_by_realm_id.assert_called_once_with("realm-1")


def test_get_ap_account_ref_returns_none_and_warns_when_nothing_found_anywhere():
    connector = _make_connector()
    connector.company_service.read_by_realm_id.return_value = None
    connector.qbo_account_repo.read_by_realm_id.return_value = [
        _make_qbo_account(qbo_id="1", name="Bank Account", account_type="Bank"),
    ]

    with patch(
        "integrations.intuit.qbo.bill.connector.bill.business.service.logger"
    ) as mock_logger:
        ref = connector._get_ap_account_ref("realm-1")

    assert ref is None
    mock_logger.warning.assert_called_once()
