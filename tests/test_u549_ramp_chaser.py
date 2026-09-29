# Python Standard Library Imports
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

# Third-party Imports
import httpx
import pytest

# Local Imports
import config
from integrations.ramp.auth.business import service as auth_mod
from integrations.ramp.auth.business.service import (
    RAMP_OAUTH_SCOPES,
    RampAuthService,
    _TokenCache,
)
from integrations.ramp.base import retry as ramp_retry_module
from integrations.ramp.base.client import RampHttpClient
from integrations.ramp.base.errors import (
    RampAuthError,
    RampRateLimitError,
    RampUntrustedRedirectError,
)
from integrations.ramp.base.retry import RetryPolicy, execute_with_retry
from integrations.ramp.transaction.business.classify import classify_transaction
from integrations.ramp.transaction.business.service import RampTransactionService
from integrations.ramp.user.business.service import RampUserService


def _settings(**overrides) -> config.Settings:
    base = dict(
        host="h",
        port=1,
        db_driver="d",
        db_server="s",
        db_name="n",
        db_user="u",
        db_password="p",
        secret_key="k",
        algorithm="HS256",
        access_token_expire_seconds=1,
        refresh_token_expire_seconds=1,
        iterations=1,
    )
    base.update(overrides)
    return config.Settings(**base)


def _txn(
    *,
    txn_id: str = "tx-1",
    complete: bool = False,
    memo: str = "",
    receipts: Optional[List[Any]] = None,
    user_id: str = "user-1",
) -> Dict[str, Any]:
    return {
        "id": txn_id,
        "all_requirements_met_and_approved": complete,
        "memo": memo,
        "receipts": receipts if receipts is not None else [],
        "merchant_name": "Test Merchant",
        "amount": "75.00",
        "user_transaction_time": "2026-09-01T12:00:00Z",
        "card_holder": {
            "user_id": user_id,
            "first_name": "Pat",
            "last_name": "Cardholder",
        },
    }


class _AuthStub:
    def ensure_valid_token(self) -> str:
        return "tok"


class _HttpStub:
    def __init__(self, *, response_json: Optional[Dict[str, Any]] = None) -> None:
        self._response_json = response_json or {"data": []}
        self.captured_urls: List[str] = []

    def get(self, url, **kwargs):
        self.captured_urls.append(url)
        return httpx.Response(200, json=self._response_json)

    def close(self):
        pass


class _HttpStubNeverCalled:
    def get(self, url, **kwargs):
        raise AssertionError(f"HTTP must not be called for untrusted URL: {url}")

    def close(self):
        pass


class _FakeFollowUpRepo:
    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.upsert_calls = 0
        self.resolve_calls: List[str] = []

    def upsert_open_item(self, *, conn: Optional[Any] = None, **kwargs: Any) -> Any:
        self.upsert_calls += 1
        rid = kwargs["ramp_transaction_id"]
        existing = self.rows.get(rid)
        if existing:
            existing.update(kwargs)
            return existing
        row = dict(kwargs)
        row["resolved_at"] = None
        self.rows[rid] = row
        return row

    def mark_resolved(
        self, *, ramp_transaction_id: str, conn: Optional[Any] = None
    ) -> Any:
        self.resolve_calls.append(ramp_transaction_id)
        if ramp_transaction_id in self.rows:
            self.rows[ramp_transaction_id]["resolved_at"] = "2026-09-27T00:00:00Z"
        return self.rows.get(ramp_transaction_id)

    def read_unresolved_ramp_transaction_ids(
        self, *, conn: Optional[Any] = None
    ) -> List[str]:
        return [rid for rid, row in self.rows.items() if not row.get("resolved_at")]


class _FakeTxClient:
    def __init__(
        self,
        transactions: List[Dict[str, Any]],
        *,
        get_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._transactions = transactions
        self._get_overrides = get_overrides or {}
        self.get_calls = 0

    def list_transactions_in_window(self, *, window_days: int, page_size: int = 100) -> List[Dict[str, Any]]:
        return list(self._transactions)

    def get_transaction(self, ramp_transaction_id: str) -> Optional[Dict[str, Any]]:
        self.get_calls += 1
        if ramp_transaction_id in self._get_overrides:
            return self._get_overrides[ramp_transaction_id]
        for t in self._transactions:
            if str(t.get("id")) == ramp_transaction_id:
                return t
        return None


class _FakeUserClient:
    def __init__(self, users: List[Dict[str, Any]]) -> None:
        self._users = users
        self.list_calls = 0

    def list_all_users(self, *, page_size: int = 100) -> List[Dict[str, Any]]:
        self.list_calls += 1
        return list(self._users)


def _make_service(
    transactions: List[Dict[str, Any]],
    users: List[Dict[str, Any]],
    *,
    get_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    settings: Optional[config.Settings] = None,
) -> RampTransactionService:
    tx_client = _FakeTxClient(transactions, get_overrides=get_overrides)
    user_client = _FakeUserClient(users)
    user_service = RampUserService(user_client)  # type: ignore[arg-type]
    return RampTransactionService(
        settings=settings or _settings(),
        transaction_client=tx_client,
        user_service=user_service,
    )


@pytest.fixture(autouse=True)
def _reset_ramp_token_cache():
    auth_mod._token_cache = _TokenCache()
    yield


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps: List[float] = []
    monkeypatch.setattr(ramp_retry_module.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(ramp_retry_module.time, "monotonic", lambda: 0.0)
    return sleeps


def test_selector_follows_ramp_flag_not_local_fields():
    missing_receipt_complete = _txn(txn_id="a", complete=True, memo="", receipts=[])
    cls_a = classify_transaction(missing_receipt_complete)
    assert cls_a.is_open is False
    assert not (cls_a.is_open and not cls_a.skip_approval_only)

    flagged_open = _txn(txn_id="b", complete=False, memo="", receipts=[{"id": "r1"}])
    cls_b = classify_transaction(flagged_open)
    assert cls_b.is_open is True
    assert cls_b.is_open and not cls_b.skip_approval_only

    flagged_open["all_requirements_met_and_approved"] = True
    cls_c = classify_transaction(flagged_open)
    assert cls_c.is_open is False
    assert not (cls_c.is_open and not cls_c.skip_approval_only)


def test_complete_missing_receipt_not_chased_on_sweep():
    repo = _FakeFollowUpRepo()
    txns = [_txn(txn_id="done-1", complete=True, memo="", receipts=[])]
    users = [{"id": "user-1", "email": "pat@example.com", "status": "USER_ACTIVE"}]
    svc = _make_service(txns, users)
    svc.run_chaser_sweep(follow_up_repo=repo)
    assert repo.upsert_calls == 0
    assert repo.rows == {}


def test_approval_only_guard_skips_and_counts(caplog):
    repo = _FakeFollowUpRepo()
    txns = [_txn(txn_id="appr-1", complete=False, memo="ok", receipts=[{"id": "r1"}])]
    users = [{"id": "user-1", "email": "pat@example.com", "status": "USER_ACTIVE"}]
    svc = _make_service(txns, users)
    stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.skipped_approval_only == 1
    assert stats.refreshed_tracked_approval_only == 0
    assert repo.upsert_calls == 0


def test_tracked_approval_only_refreshes_needs_flags_not_resolved(caplog):
    repo = _FakeFollowUpRepo()
    repo.upsert_open_item(
        ramp_transaction_id="appr-tracked-1",
        card_holder_ramp_user_id="user-1",
        card_holder_name="Pat Cardholder",
        merchant_name="M",
        amount=Decimal("75.00"),
        transaction_date="2026-09-01",
        needs_memo=True,
        needs_receipt=True,
    )
    txns = [
        _txn(txn_id="appr-tracked-1", complete=False, memo="ok", receipts=[{"id": "r1"}]),
    ]
    users = [{"id": "user-1", "email": "pat@example.com", "status": "USER_ACTIVE"}]
    svc = _make_service(txns, users)
    with caplog.at_level(logging.WARNING):
        stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.refreshed_tracked_approval_only == 1
    assert stats.skipped_approval_only == 0
    assert stats.resolved == 0
    assert repo.rows["appr-tracked-1"]["needs_memo"] is False
    assert repo.rows["appr-tracked-1"]["needs_receipt"] is False
    assert repo.rows["appr-tracked-1"]["resolved_at"] is None
    assert "appr-tracked-1" not in repo.resolve_calls
    assert repo.upsert_calls >= 2
    assert any(
        record.message == "ramp.chaser.refresh.approval_only_tracked"
        or getattr(record, "event_name", "") == "ramp.chaser.refresh.approval_only_tracked"
        for record in caplog.records
    )


def test_approval_only_never_tracked_vs_tracked_separate_counters():
    repo = _FakeFollowUpRepo()
    repo.upsert_open_item(
        ramp_transaction_id="tracked-appr",
        card_holder_ramp_user_id="user-1",
        card_holder_name="Pat",
        merchant_name="M",
        amount=Decimal("10.00"),
        transaction_date="2026-09-01",
        needs_memo=True,
        needs_receipt=True,
    )
    txns = [
        _txn(txn_id="never-appr", complete=False, memo="ok", receipts=[{"id": "r1"}]),
        _txn(txn_id="tracked-appr", complete=False, memo="ok", receipts=[{"id": "r2"}]),
    ]
    users = [{"id": "user-1", "email": "pat@example.com", "status": "USER_ACTIVE"}]
    svc = _make_service(txns, users)
    stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.skipped_approval_only == 1
    assert stats.refreshed_tracked_approval_only == 1
    assert "never-appr" not in repo.rows
    assert repo.rows["tracked-appr"]["needs_memo"] is False
    assert repo.rows["tracked-appr"]["needs_receipt"] is False


def test_email_resolves_via_users_roster_once_per_sweep():
    repo = _FakeFollowUpRepo()
    txns = [
        _txn(txn_id="t1", complete=False, memo="", receipts=[]),
        _txn(txn_id="t2", complete=False, memo="", receipts=[], user_id="user-2"),
    ]
    users = [
        {"id": "user-1", "email": "one@example.com", "status": "USER_ACTIVE"},
        {"id": "user-2", "email": "two@example.com", "status": "USER_ACTIVE"},
    ]
    user_client = _FakeUserClient(users)
    user_service = RampUserService(user_client)  # type: ignore[arg-type]
    svc = RampTransactionService(
        settings=_settings(),
        transaction_client=_FakeTxClient(txns),
        user_service=user_service,
    )
    svc.run_chaser_sweep(follow_up_repo=repo)
    assert user_client.list_calls == 1
    assert repo.rows["t1"]["card_holder_ramp_user_id"] == "user-1"
    assert repo.rows["t2"]["card_holder_ramp_user_id"] == "user-2"
    assert "card_holder_email" not in repo.rows["t1"]


def test_unresolvable_user_id_persisted_unroutable_not_dropped(caplog):
    repo = _FakeFollowUpRepo()
    txns = [_txn(txn_id="orph-1", complete=False, memo="", receipts=[], user_id="missing-user")]
    users = [{"id": "user-1", "email": "one@example.com", "status": "USER_ACTIVE"}]
    svc = _make_service(txns, users)
    with caplog.at_level(logging.WARNING):
        stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert "orph-1" in repo.rows
    assert stats.unroutable_persisted == 1
    assert any(
        record.message == "ramp.chaser.unroutable.missing_user"
        or getattr(record, "event_name", "") == "ramp.chaser.unroutable.missing_user"
        for record in caplog.records
    )


def test_inactive_cardholder_not_chased():
    repo = _FakeFollowUpRepo()
    txns = [_txn(txn_id="inactive-1", complete=False, memo="", receipts=[])]
    users = [{"id": "user-1", "email": "pat@example.com", "status": "inactive"}]
    svc = _make_service(txns, users)
    stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.skipped_inactive_cardholder == 1
    assert repo.upsert_calls == 0


def test_token_request_sends_scope_and_scopeless_response_raises(monkeypatch):
    captured: Dict[str, Any] = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["data"] = dict(data or {})
        return httpx.Response(
            200,
            json={"access_token": "tok", "expires_in": 3600},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    auth = RampAuthService(
        _settings(
            ramp_client_id="cid",
            ramp_client_secret="sec",
            ramp_api_base_url="https://api.ramp.com",
        )
    )
    with pytest.raises(RampAuthError, match="scopeless"):
        auth.ensure_valid_token(force_refresh=True)
    assert captured["data"]["scope"] == RAMP_OAUTH_SCOPES
    assert captured["data"]["grant_type"] == "client_credentials"


def test_token_asserts_granted_scopes(monkeypatch):
    def fake_post(url, data=None, headers=None, timeout=None):
        return httpx.Response(
            200,
            json={
                "access_token": "tok",
                "expires_in": 3600,
                "scope": "transactions:read",
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    auth = RampAuthService(
        _settings(
            ramp_client_id="cid",
            ramp_client_secret="sec",
        )
    )
    with pytest.raises(RampAuthError, match="users:read"):
        auth.ensure_valid_token(force_refresh=True)


def test_429_triggers_backoff_not_immediate_retry(no_sleep):
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RampRateLimitError("rate limited", http_status=429)
        return "ok"

    result = execute_with_retry(
        op,
        RetryPolicy.for_token_mint(),
        operation_name="ramp.test",
    )
    assert result == "ok"
    assert calls["n"] == 2
    assert len(no_sleep) == 1
    assert no_sleep[0] >= 1.0


def test_upsert_idempotent_second_sweep_updates_not_duplicates():
    repo = _FakeFollowUpRepo()
    txns = [_txn(txn_id="idem-1", complete=False, memo="", receipts=[])]
    users = [{"id": "user-1", "email": "pat@example.com", "status": "USER_ACTIVE"}]
    svc = _make_service(txns, users)
    svc.run_chaser_sweep(follow_up_repo=repo)
    first_upserts = repo.upsert_calls
    txns[0]["memo"] = "updated later"
    svc.run_chaser_sweep(follow_up_repo=repo)
    assert len(repo.rows) == 1
    assert repo.upsert_calls == first_upserts + 1
    assert repo.rows["idem-1"]["needs_memo"] is False


def test_omitted_completion_flag_does_not_resolve_tracked_row(caplog):
    repo = _FakeFollowUpRepo()
    repo.upsert_open_item(
        ramp_transaction_id="unk-1",
        card_holder_ramp_user_id="user-1",
        card_holder_name="Pat",
        merchant_name="M",
        amount=Decimal("10.00"),
        transaction_date="2026-09-01",
        needs_memo=True,
        needs_receipt=False,
    )
    refetched = _txn(txn_id="unk-1", complete=False, memo="", receipts=[])
    del refetched["all_requirements_met_and_approved"]
    tx_client = _FakeTxClient([], get_overrides={"unk-1": refetched})
    users = [{"id": "user-1", "email": "pat@example.com", "status": "USER_ACTIVE"}]
    svc = RampTransactionService(
        settings=_settings(),
        transaction_client=tx_client,
        user_service=RampUserService(_FakeUserClient(users)),  # type: ignore[arg-type]
    )
    with caplog.at_level(logging.WARNING):
        stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.resolved == 0
    assert stats.flag_unknown == 1
    assert repo.rows["unk-1"]["resolved_at"] is None
    assert "unk-1" not in repo.resolve_calls
    assert tx_client.get_calls == 1
    assert any(
        record.message == "ramp.chaser.flag.unknown"
        or getattr(record, "event_name", "") == "ramp.chaser.flag.unknown"
        for record in caplog.records
    )


def test_explicit_completion_flag_resolves_tracked_row():
    repo = _FakeFollowUpRepo()
    repo.upsert_open_item(
        ramp_transaction_id="done-flag-1",
        card_holder_ramp_user_id="user-1",
        card_holder_name="Pat",
        merchant_name="M",
        amount=Decimal("10.00"),
        transaction_date="2026-09-01",
        needs_memo=True,
        needs_receipt=False,
    )
    txns = [_txn(txn_id="done-flag-1", complete=True, memo="ok", receipts=[{"id": "r"}])]
    users = [{"id": "user-1", "email": "pat@example.com", "status": "USER_ACTIVE"}]
    svc = _make_service(txns, users)
    stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.resolved == 1
    assert classify_transaction(txns[0]).is_complete is True
    assert repo.rows["done-flag-1"]["resolved_at"] is not None


def test_untrusted_absolute_next_link_raises_without_sending_token():
    captured_urls: List[str] = []

    class _HttpStubCapture:
        def get(self, url, **kwargs):
            captured_urls.append(url)
            return httpx.Response(200, json={"data": []})

        def close(self):
            pass

    client = RampHttpClient(
        api_base="https://api.ramp.com",
        auth_service=_AuthStub(),
        http_client=_HttpStubCapture(),  # type: ignore[arg-type]
    )
    with pytest.raises(RampUntrustedRedirectError):
        client.get("https://evil.example/page")
    assert captured_urls == []


def test_same_origin_absolute_and_relative_next_links_work():
    captured: List[str] = []

    class _HttpStubCapture:
        def get(self, url, **kwargs):
            captured.append(url)
            return httpx.Response(200, json={"data": []})

        def close(self):
            pass

    client = RampHttpClient(
        api_base="https://api.ramp.com",
        auth_service=_AuthStub(),
        http_client=_HttpStubCapture(),  # type: ignore[arg-type]
    )
    client.get("https://api.ramp.com/developer/v1/transactions?page=2")
    client.get("https://api.ramp.com:443/developer/v1/transactions?page=2")
    client.get("https://api.ramp.com./developer/v1/transactions?page=2")
    client.get("developer/v1/transactions?page=2")
    assert captured == [
        "https://api.ramp.com/developer/v1/transactions?page=2",
        "https://api.ramp.com:443/developer/v1/transactions?page=2",
        "https://api.ramp.com./developer/v1/transactions?page=2",
        "https://api.ramp.com/developer/v1/transactions?page=2",
    ]


def test_untrusted_pagination_link_scheme_downgrade_raises():
    client = RampHttpClient(
        api_base="https://api.ramp.com",
        auth_service=_AuthStub(),
        http_client=_HttpStubNeverCalled(),  # type: ignore[arg-type]
    )
    with pytest.raises(RampUntrustedRedirectError):
        client.get("http://api.ramp.com/developer/v1/transactions?page=2")


def test_untrusted_pagination_link_userinfo_host_raises():
    client = RampHttpClient(
        api_base="https://api.ramp.com",
        auth_service=_AuthStub(),
        http_client=_HttpStubNeverCalled(),  # type: ignore[arg-type]
    )
    with pytest.raises(RampUntrustedRedirectError):
        client.get("https://api.ramp.com@evil.example/developer/v1/transactions?page=2")


def test_token_mint_429_retries_with_backoff(monkeypatch, no_sleep):
    calls = {"n": 0}

    def fake_post(url, data=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(
            200,
            json={
                "access_token": "tok",
                "expires_in": 3600,
                "scope": RAMP_OAUTH_SCOPES,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    auth = RampAuthService(
        _settings(
            ramp_client_id="cid",
            ramp_client_secret="sec",
            ramp_api_base_url="https://api.ramp.com",
        )
    )
    assert auth.ensure_valid_token(force_refresh=True) == "tok"
    assert calls["n"] == 2
    assert len(no_sleep) == 1
    assert no_sleep[0] == 2.0


def test_token_mint_429_honors_large_retry_after(monkeypatch, no_sleep):
    calls = {"n": 0}

    def fake_post(url, data=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(
            200,
            json={
                "access_token": "tok",
                "expires_in": 3600,
                "scope": RAMP_OAUTH_SCOPES,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    auth = RampAuthService(
        _settings(
            ramp_client_id="cid",
            ramp_client_secret="sec",
            ramp_api_base_url="https://api.ramp.com",
        )
    )
    assert auth.ensure_valid_token(force_refresh=True) == "tok"
    assert calls["n"] == 2
    assert len(no_sleep) == 1
    assert no_sleep[0] == 60.0


def test_token_mint_401_fails_fast_without_retry(monkeypatch):
    sleeps: List[float] = []
    monkeypatch.setattr(ramp_retry_module.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def fake_post(url, data=None, headers=None, timeout=None):
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    monkeypatch.setattr(httpx, "post", fake_post)
    auth = RampAuthService(
        _settings(
            ramp_client_id="cid",
            ramp_client_secret="sec",
            ramp_api_base_url="https://api.ramp.com",
        )
    )
    with pytest.raises(RampAuthError):
        auth.ensure_valid_token(force_refresh=True)
    assert calls["n"] == 1
    assert sleeps == []


def test_resolution_observed_from_ramp_on_later_sweep():
    repo = _FakeFollowUpRepo()
    repo.upsert_open_item(
        ramp_transaction_id="res-1",
        card_holder_ramp_user_id="user-1",
        card_holder_name="Pat",
        merchant_name="M",
        amount=Decimal("10.00"),
        transaction_date="2026-09-01",
        needs_memo=True,
        needs_receipt=False,
    )
    txns = [_txn(txn_id="res-1", complete=True, memo="done", receipts=[{"id": "r"}])]
    users = [{"id": "user-1", "email": "pat@example.com", "status": "USER_ACTIVE"}]
    svc = _make_service(txns, users)
    stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.resolved == 1
    assert repo.rows["res-1"]["resolved_at"] is not None


# --- U-567: the straggler refetch is now measurable (bounding it is a separate unit) ---
#
# `_process_chaser_window` refetches every unresolved id that is NOT in the window,
# one GET each, every sweep, forever. Two classes never leave that set: items aged
# past the window while still open, and items whose Ramp transaction 404s. These
# drive the PUBLIC run_chaser_sweep rather than the private helper, so they pin the
# counters as an operator actually sees them — in the endpoint's JSON envelope.


def _seed_open(repo: "_FakeFollowUpRepo", ramp_transaction_id: str) -> None:
    repo.upsert_open_item(
        ramp_transaction_id=ramp_transaction_id,
        card_holder_ramp_user_id="user-1",
        card_holder_name="Pat",
        merchant_name="M",
        amount=Decimal("10.00"),
        transaction_date="2026-09-01",
        needs_memo=True,
        needs_receipt=False,
    )


_ACTIVE_USER = {"id": "user-1", "email": "pat@example.com", "status": "USER_ACTIVE"}


def test_u567_stragglers_refetched_counts_every_straggler():
    repo = _FakeFollowUpRepo()
    for rid in ("gone-1", "extra-1", "extra-2"):
        _seed_open(repo, rid)
    # in-window is fetched by the window call, so it is NOT a straggler
    txns = [_txn(txn_id="in-window")]
    svc = _make_service(
        txns,
        [_ACTIVE_USER],
        get_overrides={
            "gone-1": None,
            "extra-1": _txn(txn_id="extra-1"),
            "extra-2": _txn(txn_id="extra-2"),
        },
    )
    stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.stragglers_refetched == 3
    assert stats.transactions_fetched == 1


def test_u567_stragglers_gone_from_ramp_counts_only_falsy_and_skips_them():
    repo = _FakeFollowUpRepo()
    for rid in ("missing-a", "missing-b", "found-1"):
        _seed_open(repo, rid)
    seeded_upserts = repo.upsert_calls
    svc = _make_service(
        [],  # empty window: every unresolved row is a straggler
        [_ACTIVE_USER],
        get_overrides={
            "missing-a": None,
            "missing-b": {},  # falsy dict counts as gone too
            "found-1": _txn(txn_id="found-1"),
        },
    )
    stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.stragglers_refetched == 3
    assert stats.stragglers_gone_from_ramp == 2
    # a gone straggler never enters the window, so it is never re-upserted;
    # the one that came back real is.
    assert repo.upsert_calls == seeded_upserts + 1
    assert repo.resolve_calls == []


def test_u567_straggler_counters_are_zero_not_absent_when_none():
    repo = _FakeFollowUpRepo()
    svc = _make_service([_txn(txn_id="only")], [_ACTIVE_USER])
    stats = svc.run_chaser_sweep(follow_up_repo=repo)
    assert stats.stragglers_refetched == 0
    assert stats.stragglers_gone_from_ramp == 0

