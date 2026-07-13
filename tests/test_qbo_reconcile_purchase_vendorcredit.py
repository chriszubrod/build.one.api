"""Pure-logic tests for Purchase and VendorCredit QBO reconciliation detectors."""
from types import SimpleNamespace

from integrations.intuit.qbo.base.errors import QboNotFoundError
from integrations.intuit.qbo.reconciliation.business.service import (
    DRIFT_QBO_MISSING_LOCALLY,
    DRIFT_QBO_VOIDED,
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


# ------------------------------------------------------------------ #
# Purchase fakes
# ------------------------------------------------------------------ #


class _FakePurchaseClient:
    def __init__(self, *, purchases=None, get_raises=None, query_raises=None):
        self._purchases = purchases or []
        self._get_raises = get_raises
        self._query_raises = query_raises

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_all_purchases(self):
        if self._query_raises:
            raise self._query_raises
        return self._purchases

    def get_purchase(self, purchase_id):
        if self._get_raises:
            raise self._get_raises
        return SimpleNamespace(id=purchase_id)


class _FakeQboPurchaseRepo:
    def __init__(self, *, by_qbo_id=None, by_realm=None):
        self._by_qbo_id = by_qbo_id
        self._by_realm = by_realm or []

    def read_by_qbo_id(self, qbo_id):
        return self._by_qbo_id

    def read_by_realm_id(self, realm_id):
        return self._by_realm


class _FakePurchaseMappingRepo:
    def __init__(self, mapping=None):
        self._mapping = mapping

    def read_by_qbo_purchase_id(self, local_id):
        return self._mapping


class _FakePurchaseService:
    def __init__(self):
        self.calls = []

    def upsert_from_external(self, qbo_purchase, realm_id):
        self.calls.append((qbo_purchase, realm_id))
        local = SimpleNamespace(id=99, qbo_id=qbo_purchase.id)
        return local, []


class _FakePurchaseConnector:
    def __init__(self):
        self.calls = []

    def sync_from_qbo_purchase(self, *, qbo_purchase, qbo_purchase_lines):
        self.calls.append((qbo_purchase, qbo_purchase_lines))


def _patch_purchase_stack(monkeypatch, *, client, qbo_repo, mapping_repo,
                          purchase_service=None, connector=None):
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.external.client.QboPurchaseClient",
        lambda realm_id: client,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository",
        lambda: qbo_repo,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.connector.expense.persistence.repo.PurchaseExpenseRepository",
        lambda: mapping_repo,
    )
    svc = purchase_service or _FakePurchaseService()
    conn = connector or _FakePurchaseConnector()
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.business.service.QboPurchaseService",
        lambda: svc,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector",
        lambda: conn,
    )
    return svc, conn


# ------------------------------------------------------------------ #
# VendorCredit fakes
# ------------------------------------------------------------------ #


class _FakeVendorCreditClient:
    def __init__(self, *, vendor_credits=None, get_raises=None, query_raises=None):
        self._vendor_credits = vendor_credits or []
        self._get_raises = get_raises
        self._query_raises = query_raises

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_all_vendor_credits(self):
        if self._query_raises:
            raise self._query_raises
        return self._vendor_credits

    def get_vendor_credit(self, vendor_credit_id):
        if self._get_raises:
            raise self._get_raises
        return SimpleNamespace(id=vendor_credit_id)


class _FakeQboVendorCreditRepo:
    def __init__(self, *, by_qbo_id=None, by_realm=None):
        self._by_qbo_id = by_qbo_id
        self._by_realm = by_realm or []

    def read_by_qbo_id_and_realm_id(self, qbo_id, realm_id):
        return self._by_qbo_id

    def read_by_realm_id(self, realm_id):
        return self._by_realm


class _FakeVendorCreditMappingRepo:
    def __init__(self, mapping=None):
        self._mapping = mapping

    def read_by_qbo_vendor_credit_id(self, local_id):
        return self._mapping


class _FakeVendorCreditService:
    def __init__(self):
        self.calls = []

    def upsert_from_external(self, qbo_vc, realm_id):
        self.calls.append((qbo_vc, realm_id))
        local = SimpleNamespace(id=88, qbo_id=qbo_vc.id)
        return local, []


class _FakeVendorCreditConnector:
    def __init__(self):
        self.calls = []

    def sync_from_qbo_vendor_credit(self, qbo_vc, qbo_lines):
        self.calls.append((qbo_vc, qbo_lines))


def _patch_vendor_credit_stack(monkeypatch, *, client, qbo_repo, mapping_repo,
                               vc_service=None, connector=None):
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.external.client.QboVendorCreditClient",
        lambda realm_id: client,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.persistence.repo.QboVendorCreditRepository",
        lambda: qbo_repo,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo.VendorCreditBillCreditMappingRepository",
        lambda: mapping_repo,
    )
    svc = vc_service or _FakeVendorCreditService()
    conn = connector or _FakeVendorCreditConnector()
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.business.service.QboVendorCreditService",
        lambda: svc,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service.VendorCreditBillCreditConnector",
        lambda: conn,
    )
    return svc, conn


# ------------------------------------------------------------------ #
# Purchase tests
# ------------------------------------------------------------------ #


def test_purchase_missing_locally_autofix_on(monkeypatch):
    monkeypatch.setenv("QBO_RECONCILE_PURCHASE_AUTOFIX", "true")
    svc, repo = _fake_issue_service()
    qbo_purchase = SimpleNamespace(id="P-1")
    client = _FakePurchaseClient(purchases=[qbo_purchase])
    purchase_svc, connector = _patch_purchase_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboPurchaseRepo(by_qbo_id=None),
        mapping_repo=_FakePurchaseMappingRepo(mapping=None),
    )

    result = svc.reconcile_purchases(realm_id="realm-1")

    assert result["auto_fixed"] >= 1
    assert len(purchase_svc.calls) == 1
    assert len(connector.calls) == 1
    auto_issues = [i for i in repo.issues if i["action"] == "auto_fixed"]
    assert any(i["entity_type"] == "Expense" and i["qbo_id"] == "P-1" for i in auto_issues)


def test_purchase_missing_locally_autofix_off(monkeypatch):
    monkeypatch.delenv("QBO_RECONCILE_PURCHASE_AUTOFIX", raising=False)
    svc, repo = _fake_issue_service()
    qbo_purchase = SimpleNamespace(id="P-2")
    client = _FakePurchaseClient(purchases=[qbo_purchase])
    purchase_svc, connector = _patch_purchase_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboPurchaseRepo(by_qbo_id=None),
        mapping_repo=_FakePurchaseMappingRepo(mapping=None),
    )

    result = svc.reconcile_purchases(realm_id="realm-1")

    assert result["auto_fixed"] == 0
    assert len(purchase_svc.calls) == 0
    assert len(connector.calls) == 0
    summary = [
        i for i in repo.issues
        if i["drift_type"] == DRIFT_QBO_MISSING_LOCALLY
        and i["entity_type"] == "Expense"
        and "QBO_RECONCILE_PURCHASE_AUTOFIX=false" in (i.get("details") or "")
    ]
    assert len(summary) == 1


def test_purchase_voided_flagged(monkeypatch):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=10, qbo_id="P-VOID")
    mapping = SimpleNamespace(expense_id=55)
    client = _FakePurchaseClient(get_raises=QboNotFoundError("not found"))
    _patch_purchase_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboPurchaseRepo(by_realm=[local]),
        mapping_repo=_FakePurchaseMappingRepo(mapping=mapping),
    )

    result = svc.reconcile_purchases(realm_id="realm-1")

    assert result["flagged"] >= 1
    void_issues = [
        i for i in repo.issues
        if i["drift_type"] == DRIFT_QBO_VOIDED and i["entity_type"] == "Expense"
    ]
    assert len(void_issues) >= 1
    assert "Expense id=55" in void_issues[0]["details"]


def test_purchase_detector_failure_isolation(monkeypatch):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=10, qbo_id="P-ISO")
    mapping = SimpleNamespace(expense_id=77)
    client = _FakePurchaseClient(
        purchases=[],
        query_raises=RuntimeError("query blew up"),
        get_raises=QboNotFoundError("gone"),
    )
    _patch_purchase_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboPurchaseRepo(by_realm=[local]),
        mapping_repo=_FakePurchaseMappingRepo(mapping=mapping),
    )

    result = svc.reconcile_purchases(realm_id="realm-1")

    assert isinstance(result, dict)
    assert result["errors"] >= 1
    assert result["flagged"] >= 1
    void_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert len(void_issues) >= 1


# ------------------------------------------------------------------ #
# VendorCredit tests
# ------------------------------------------------------------------ #


def test_vendor_credit_missing_locally_autofix_on(monkeypatch):
    monkeypatch.setenv("QBO_RECONCILE_VENDORCREDIT_AUTOFIX", "true")
    svc, repo = _fake_issue_service()
    qbo_vc = SimpleNamespace(id="VC-1", line=[])
    client = _FakeVendorCreditClient(vendor_credits=[qbo_vc])
    vc_svc, connector = _patch_vendor_credit_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboVendorCreditRepo(by_qbo_id=None),
        mapping_repo=_FakeVendorCreditMappingRepo(mapping=None),
    )

    result = svc.reconcile_vendor_credits(realm_id="realm-1")

    assert result["auto_fixed"] >= 1
    assert len(vc_svc.calls) == 1
    assert len(connector.calls) == 1
    auto_issues = [i for i in repo.issues if i["action"] == "auto_fixed"]
    assert any(i["entity_type"] == "BillCredit" and i["qbo_id"] == "VC-1" for i in auto_issues)


def test_vendor_credit_missing_locally_autofix_off(monkeypatch):
    monkeypatch.delenv("QBO_RECONCILE_VENDORCREDIT_AUTOFIX", raising=False)
    svc, repo = _fake_issue_service()
    qbo_vc = SimpleNamespace(id="VC-2", line=[])
    client = _FakeVendorCreditClient(vendor_credits=[qbo_vc])
    vc_svc, connector = _patch_vendor_credit_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboVendorCreditRepo(by_qbo_id=None),
        mapping_repo=_FakeVendorCreditMappingRepo(mapping=None),
    )

    result = svc.reconcile_vendor_credits(realm_id="realm-1")

    assert result["auto_fixed"] == 0
    assert len(vc_svc.calls) == 0
    assert len(connector.calls) == 0
    summary = [
        i for i in repo.issues
        if i["drift_type"] == DRIFT_QBO_MISSING_LOCALLY
        and i["entity_type"] == "BillCredit"
        and "QBO_RECONCILE_VENDORCREDIT_AUTOFIX=false" in (i.get("details") or "")
    ]
    assert len(summary) == 1


def test_vendor_credit_voided_flagged(monkeypatch):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=20, qbo_id="VC-VOID")
    mapping = SimpleNamespace(bill_credit_id=66)
    client = _FakeVendorCreditClient(get_raises=QboNotFoundError("not found"))
    _patch_vendor_credit_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboVendorCreditRepo(by_realm=[local]),
        mapping_repo=_FakeVendorCreditMappingRepo(mapping=mapping),
    )

    result = svc.reconcile_vendor_credits(realm_id="realm-1")

    assert result["flagged"] >= 1
    void_issues = [
        i for i in repo.issues
        if i["drift_type"] == DRIFT_QBO_VOIDED and i["entity_type"] == "BillCredit"
    ]
    assert len(void_issues) >= 1
    assert "BillCredit id=66" in void_issues[0]["details"]


def test_vendor_credit_detector_failure_isolation(monkeypatch):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=20, qbo_id="VC-ISO")
    mapping = SimpleNamespace(bill_credit_id=88)
    client = _FakeVendorCreditClient(
        vendor_credits=[],
        query_raises=RuntimeError("query blew up"),
        get_raises=QboNotFoundError("gone"),
    )
    _patch_vendor_credit_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboVendorCreditRepo(by_realm=[local]),
        mapping_repo=_FakeVendorCreditMappingRepo(mapping=mapping),
    )

    result = svc.reconcile_vendor_credits(realm_id="realm-1")

    assert isinstance(result, dict)
    assert result["errors"] >= 1
    assert result["flagged"] >= 1
    void_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert len(void_issues) >= 1
