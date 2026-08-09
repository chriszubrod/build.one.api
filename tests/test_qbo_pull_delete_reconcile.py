"""Pure-logic tests for U-212 — the pull-side delete-reconcile strict gate.

Covers: fault-610 → QboNotFoundError mapping (deleted transactions signal as
HTTP 400/610, not 404 — this also powers the reconcile void detectors),
malformed-2xx raising instead of silent {}, the strict gate's abort/ceiling/
confirm semantics, and the bill service's wiring onto the gate.
"""
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.base.delete_reconcile import strict_confirmed_deleted_ids
from integrations.intuit.qbo.base.errors import (
    QboDuplicateError,
    QboMalformedResponseError,
    QboNotFoundError,
    QboSyncTokenMismatchError,
    QboValidationError,
)

REALM_ID = "realm-test"


# --------------------------------------------------------------------------- #
# Client: fault-610 mapping on HTTP 400
# --------------------------------------------------------------------------- #


def _fake_response(status, fault_code=None, text='{"x": 1}'):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = MagicMock()
    resp.headers.get.return_value = None
    if fault_code is not None:
        resp.json.return_value = {"Fault": {"Error": [{"code": fault_code, "Message": "m"}]}}
    else:
        resp.json.return_value = {}
    return resp


def _make_client():
    return QboHttpClient(
        realm_id=REALM_ID,
        auth_service=MagicMock(),
        http_client=MagicMock(),
        api_budget=MagicMock(),
    )


def _raise_for(resp):
    _make_client()._raise_for_status(
        response=resp,
        method="GET",
        request_path="/bill/1",
        correlation_id="c",
        operation_name="op",
        duration_ms=1.0,
    )


def test_400_fault_610_maps_to_not_found():
    """Deleted transactions come back as HTTP 400 / fault 610 — must surface
    as QboNotFoundError so void detectors and delete-confirm can see them."""
    with pytest.raises(QboNotFoundError):
        _raise_for(_fake_response(400, fault_code="610"))


def test_400_fault_5010_still_sync_token_mismatch():
    with pytest.raises(QboSyncTokenMismatchError):
        _raise_for(_fake_response(400, fault_code="5010"))


def test_400_fault_6140_still_duplicate():
    with pytest.raises(QboDuplicateError):
        _raise_for(_fake_response(400, fault_code="6140"))


def test_400_other_fault_still_validation():
    with pytest.raises(QboValidationError):
        _raise_for(_fake_response(400, fault_code="2010"))


# --------------------------------------------------------------------------- #
# Client: empty/unparseable 2xx raises instead of silent {}
# --------------------------------------------------------------------------- #


def _send_once(client, body_text, json_raises=False):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = body_text
    if json_raises:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = {"QueryResponse": {}}
    client.auth_service.ensure_valid_token.return_value = MagicMock(access_token="tok")
    with patch.object(client, "_send_http", return_value=resp):
        return client._send_once(
            method="GET",
            url="https://qbo/query",
            request_path="query",
            params={},
            json_body=None,
            correlation_id="c",
            operation_name="op",
        )


def test_empty_2xx_body_raises_retryable_malformed_error():
    client = _make_client()
    client._api_budget.record_call_or_raise.return_value = MagicMock(blocked=False)
    with pytest.raises(QboMalformedResponseError) as exc_info:
        _send_once(client, body_text="")
    assert exc_info.value.is_retryable is True


def test_unparseable_2xx_body_raises_malformed_error():
    client = _make_client()
    client._api_budget.record_call_or_raise.return_value = MagicMock(blocked=False)
    with pytest.raises(QboMalformedResponseError):
        _send_once(client, body_text="<html>gateway error</html>", json_raises=True)


def test_valid_2xx_body_still_returns_parsed_json():
    client = _make_client()
    client._api_budget.record_call_or_raise.return_value = MagicMock(blocked=False)
    assert _send_once(client, body_text='{"QueryResponse": {}}') == {"QueryResponse": {}}


# --------------------------------------------------------------------------- #
# Strict gate: abort / ceiling / confirm semantics
# --------------------------------------------------------------------------- #


def _gate(**overrides):
    kwargs = dict(
        entity_type="Bill",
        realm_id=REALM_ID,
        fetch_live_ids=lambda: ["1", "2", "3"],
        confirm_get=MagicMock(),
        local_qbo_ids=["1", "2", "3"],
    )
    kwargs.update(overrides)
    return strict_confirmed_deleted_ids(**kwargs)


def test_gate_all_live_returns_empty_set():
    assert _gate() == set()


def test_gate_id_fetch_failure_aborts_with_none():
    def boom():
        raise RuntimeError("page dropped")

    assert _gate(fetch_live_ids=boom) is None


def test_gate_ceiling_exceeded_aborts_and_records_issue(monkeypatch):
    monkeypatch.setenv("QBO_PULL_DELETE_MAX_CANDIDATES", "10")
    with patch(
        "integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository"
    ) as repo_cls:
        result = _gate(
            local_qbo_ids=[str(i) for i in range(100)],
            fetch_live_ids=lambda: [],
        )
    assert result is None
    repo_cls.return_value.create.assert_called_once()


def test_gate_confirms_only_not_found():
    def confirm(qbo_id):
        if qbo_id == "9":
            raise QboNotFoundError("gone")  # confirmed deleted
        if qbo_id == "8":
            raise RuntimeError("transient")  # confirm failed → skip
        return object()  # alive despite id-set absence → skip

    result = _gate(
        local_qbo_ids=["1", "7", "8", "9"],
        fetch_live_ids=lambda: ["1"],
        confirm_get=confirm,
    )
    assert result == {"9"}


def test_gate_ignores_empty_local_ids():
    assert _gate(local_qbo_ids=[None, "", "1"]) == set()


def test_gate_empty_candidates_skips_live_fetch_entirely():
    fetch = MagicMock()
    assert _gate(local_qbo_ids=[], fetch_live_ids=fetch) == set()
    fetch.assert_not_called()


def test_gate_normalizes_both_sides_of_the_diff():
    """ids.py contract: int-typed or padded local ids must not false-diff
    as absent against the normalized live set."""
    confirm = MagicMock()
    result = _gate(local_qbo_ids=[123, " 2 ", "3"], fetch_live_ids=lambda: ["123", "2", "3"], confirm_get=confirm)
    assert result == set()
    confirm.assert_not_called()


def test_gate_systemic_confirm_error_aborts_whole_reconcile():
    """A tripped budget breaker / rate limit / auth failure mid-confirm cannot
    succeed for later candidates — abort and delete nothing, rather than
    burning a metered call per remaining candidate."""
    from integrations.intuit.qbo.base.errors import QboBudgetExceededError

    calls = []

    def confirm(qbo_id):
        calls.append(qbo_id)
        if qbo_id == "7":
            raise QboNotFoundError("gone")
        raise QboBudgetExceededError("blocked")

    result = _gate(
        local_qbo_ids=["7", "8", "9"],
        fetch_live_ids=lambda: [],
        confirm_get=confirm,
    )
    assert result is None
    assert calls == ["7", "8"]  # aborted immediately on the systemic error


# --------------------------------------------------------------------------- #
# Bill service wiring onto the gate
# --------------------------------------------------------------------------- #


def _make_bill_service():
    from integrations.intuit.qbo.bill.business.service import QboBillService

    repo = MagicMock()
    line_repo = MagicMock()
    return QboBillService(repo=repo, line_repo=line_repo), repo


def test_bill_reconcile_aborted_gate_deletes_nothing():
    svc, repo = _make_bill_service()
    local = MagicMock(qbo_id="42", id=1)
    repo.read_by_realm_id.return_value = [local]
    with patch(
        "integrations.intuit.qbo.bill.business.service.QboBillClient"
    ), patch(
        "integrations.intuit.qbo.base.delete_reconcile.strict_confirmed_deleted_ids",
        return_value=None,
    ):
        assert svc._reconcile_deleted_bills(REALM_ID) == 0
    repo.delete_by_qbo_id.assert_not_called()


def test_bill_reconcile_deletes_only_confirmed():
    svc, repo = _make_bill_service()
    confirmed_local = MagicMock(qbo_id="42", id=1)
    unconfirmed_local = MagicMock(qbo_id="43", id=2)
    repo.read_by_realm_id.return_value = [confirmed_local, unconfirmed_local]
    svc.line_repo.read_by_qbo_bill_id.return_value = []
    with patch(
        "integrations.intuit.qbo.bill.business.service.QboBillClient"
    ), patch(
        "integrations.intuit.qbo.base.delete_reconcile.strict_confirmed_deleted_ids",
        return_value={"42"},
    ), patch(
        "integrations.intuit.qbo.bill.connector.bill.persistence.repo.BillBillRepository"
    ) as bb_repo_cls, patch(
        "integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo.BillLineItemBillLineRepository"
    ), patch(
        "entities.bill.business.service.BillService"
    ):
        bb_repo_cls.return_value.read_by_qbo_bill_id.return_value = None
        assert svc._reconcile_deleted_bills(REALM_ID) == 1
    repo.delete_by_qbo_id.assert_called_once_with("42")
