"""Pure-logic tests for Bill QBO missing-locally reconciliation detector."""
from types import SimpleNamespace

from integrations.intuit.qbo.reconciliation.business.service import (
    DRIFT_QBO_MISSING_LOCALLY,
    ReconciliationService,
)


class _FakeIssueRepo:
    def __init__(self):
        self.issues = []

    def create(self, **kwargs):
        self.issues.append(kwargs)


def _fake_issue_service():
    repo = _FakeIssueRepo()
    svc = ReconciliationService(repo=repo)
    return svc, repo


class _FakeBillClient:
    def __init__(self, *, bills=None):
        self._bills = bills or []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_all_bills(self):
        return self._bills


class _FakeQboBillService:
    def __init__(self):
        self.calls = []
        self.upserted_locals = []

    def upsert_from_external(self, qbo_bill, realm_id):
        self.calls.append((qbo_bill, realm_id))
        local = SimpleNamespace(id=99, qbo_id=qbo_bill.id)
        self.upserted_locals.append(local)
        return local, []


class _FakeBillBillConnector:
    def __init__(self, *, raises=None):
        self.calls = []
        self._raises = raises

    def sync_from_qbo_bill(self, *, qbo_bill, qbo_bill_lines):
        self.calls.append((qbo_bill, qbo_bill_lines))
        if self._raises:
            raise self._raises


def _patch_bill_stack(
    monkeypatch,
    *,
    client,
    identity_rows=None,
    bill_service=None,
    connector=None,
):
    """U-305: identity_rows stands in for identity_drift.py's registry-driven
    read_qbo_identity_rows_by_realm_id — the (Id, QboId) rows dbo.Bill already
    carries for the realm, replacing the old qbo.Bill/qbo.BillBill
    staging+mapping fakes (_FakeQboBillRepo / _FakeBillBillRepository)."""
    svc = bill_service or _FakeQboBillService()
    conn = connector or _FakeBillBillConnector()
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.external.client.QboBillClient",
        lambda realm_id: client,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.base.identity_drift.read_qbo_identity_rows_by_realm_id",
        lambda spec, realm_id, **kwargs: list(identity_rows or []),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.business.service.QboBillService",
        lambda: svc,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.connector.bill.business.service.BillBillConnector",
        lambda: conn,
    )
    return svc, conn


def test_bill_missing_locally_autofix_on_routes_through_upsert_from_external(monkeypatch):
    monkeypatch.setenv("QBO_RECONCILE_BILL_AUTOFIX", "true")
    svc, repo = _fake_issue_service()
    qbo_bill = SimpleNamespace(id="B-1")
    client = _FakeBillClient(bills=[qbo_bill])
    bill_svc, connector = _patch_bill_stack(
        monkeypatch,
        client=client,
        identity_rows=[],
    )

    result = svc._reconcile_bill_qbo_missing_locally(realm_id="realm-1", run_id="run-1")

    assert result["auto_fixed"] == 1
    assert len(bill_svc.calls) == 1
    assert bill_svc.calls[0][0] is qbo_bill
    assert len(connector.calls) == 1
    synced_bill, synced_lines = connector.calls[0]
    assert synced_bill is not qbo_bill
    assert synced_bill.id == 99
    assert synced_bill.qbo_id == "B-1"
    assert synced_lines == []
    local_from_upsert = bill_svc.upserted_locals[0]
    assert synced_bill is local_from_upsert
    auto_issues = [i for i in repo.issues if i["action"] == "auto_fixed"]
    assert len(auto_issues) == 1
    assert auto_issues[0]["entity_type"] == "Bill"
    assert auto_issues[0]["qbo_id"] == "B-1"


def test_bill_missing_locally_autofix_on_skips_unmapped_vendor_without_crash_or_issue(
    monkeypatch,
):
    monkeypatch.setenv("QBO_RECONCILE_BILL_AUTOFIX", "true")
    svc, repo = _fake_issue_service()
    qbo_bill = SimpleNamespace(id="B-269")
    client = _FakeBillClient(bills=[qbo_bill])
    connector = _FakeBillBillConnector(
        raises=ValueError("No vendor mapping found for QBO vendor ref: 269"),
    )
    _patch_bill_stack(
        monkeypatch,
        client=client,
        identity_rows=[],
        connector=connector,
    )

    result = svc._reconcile_bill_qbo_missing_locally(realm_id="realm-1", run_id="run-1")

    assert result["skipped_unmapped"] == 1
    assert result["auto_fixed"] == 0
    assert repo.issues == []


def test_bill_missing_locally_autofix_off_counts_only_no_side_effects(monkeypatch):
    monkeypatch.delenv("QBO_RECONCILE_BILL_AUTOFIX", raising=False)
    svc, repo = _fake_issue_service()
    qbo_bill = SimpleNamespace(id="B-2")
    client = _FakeBillClient(bills=[qbo_bill])
    bill_svc, connector = _patch_bill_stack(
        monkeypatch,
        client=client,
        identity_rows=[],
    )

    result = svc._reconcile_bill_qbo_missing_locally(realm_id="realm-1", run_id="run-1")

    assert bill_svc.calls == []
    assert connector.calls == []
    assert result["missing"] == 1
    assert result["auto_fixed"] == 0
    summary = [
        i for i in repo.issues
        if i["drift_type"] == DRIFT_QBO_MISSING_LOCALLY
        and i["entity_type"] == "Bill"
        and i["action"] == "flagged"
        and i["severity"] == "low"
        and i["qbo_id"] is None
    ]
    assert len(summary) == 1
    assert "QBO_RECONCILE_BILL_AUTOFIX=false" in (summary[0].get("details") or "")


def test_bill_missing_locally_skips_dbo_mapped(monkeypatch):
    """U-305: a QBO bill already carrying a dbo.Bill.QboId is never counted
    missing, without any qbo.Bill/qbo.BillBill round trip."""
    monkeypatch.delenv("QBO_RECONCILE_BILL_AUTOFIX", raising=False)
    svc, repo = _fake_issue_service()
    qbo_bill = SimpleNamespace(id="B-MAPPED")
    client = _FakeBillClient(bills=[qbo_bill])
    bill_svc, connector = _patch_bill_stack(
        monkeypatch,
        client=client,
        identity_rows=[SimpleNamespace(id=1, qbo_id="B-MAPPED")],
    )

    result = svc._reconcile_bill_qbo_missing_locally(realm_id="realm-1", run_id="run-1")

    assert result["auto_fixed"] == 0
    assert result["missing"] == 0
    assert len(bill_svc.calls) == 0
    missing_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_MISSING_LOCALLY]
    assert len(missing_issues) == 0


def test_bill_missing_locally_identity_read_failure_is_isolated(monkeypatch):
    """U-305: a failure fetching dbo.Bill's bulk identity set degrades this
    ONE detector (flagged=1, errors=1) instead of propagating and taking down
    the whole reconcile_bills run — same isolation guarantee the per-record
    try/except gave before the repoint."""
    svc, repo = _fake_issue_service()
    qbo_bill = SimpleNamespace(id="B-3")
    client = _FakeBillClient(bills=[qbo_bill])

    def _raise_identity_read(spec, realm_id, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.external.client.QboBillClient",
        lambda realm_id: client,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.base.identity_drift.read_qbo_identity_rows_by_realm_id",
        _raise_identity_read,
    )

    result = svc._reconcile_bill_qbo_missing_locally(realm_id="realm-1", run_id="run-1")

    assert result == {"auto_fixed": 0, "missing": 0, "skipped_unmapped": 0, "flagged": 1, "errors": 1}
    assert repo.issues == []
