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


class _FakeQboBillRepo:
    def __init__(self, *, by_qbo_id=None):
        self._by_qbo_id = by_qbo_id

    def read_by_qbo_id(self, qbo_id):
        return self._by_qbo_id


class _FakeBillBillRepository:
    def __init__(self, mapping=None):
        self._mapping = mapping

    def read_by_qbo_bill_id(self, local_id):
        return self._mapping


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
    qbo_repo,
    mapping_repo,
    bill_service=None,
    connector=None,
):
    svc = bill_service or _FakeQboBillService()
    conn = connector or _FakeBillBillConnector()
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.external.client.QboBillClient",
        lambda realm_id: client,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.persistence.repo.QboBillRepository",
        lambda: qbo_repo,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.connector.bill.persistence.repo.BillBillRepository",
        lambda: mapping_repo,
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
        qbo_repo=_FakeQboBillRepo(by_qbo_id=None),
        mapping_repo=_FakeBillBillRepository(mapping=None),
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
        qbo_repo=_FakeQboBillRepo(by_qbo_id=None),
        mapping_repo=_FakeBillBillRepository(mapping=None),
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
        qbo_repo=_FakeQboBillRepo(by_qbo_id=None),
        mapping_repo=_FakeBillBillRepository(mapping=None),
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
