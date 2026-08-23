"""Pure-logic tests for QBO void-detection query-diff (Bill, Purchase, VendorCredit)."""
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.base.errors import (
    QboNotFoundError,
    QboRateLimitError,
    QboUnexpectedError,
)
from integrations.intuit.qbo.base.ids import normalize_qbo_id
from integrations.intuit.qbo.bill.external.client import QboBillClient
from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
from integrations.intuit.qbo.reconciliation.business.service import (
    DEFAULT_VOID_MAX_CANDIDATES,
    DRIFT_QBO_VOIDED,
    ReconciliationService,
)


class _FakeIssueRepo:
    def __init__(
        self,
        *,
        seeded_issues=None,
        create_raises=False,
        key_fetch_raises=False,
    ):
        self.issues = []
        # Each seed: (realm_id, entity_type, qbo_id, status)
        self.seeded_issues = list(seeded_issues or [])
        self.create_raises = create_raises
        self.key_fetch_raises = key_fetch_raises
        self.key_fetch_calls = 0
        self.create_calls = 0

    def create(self, **kwargs):
        self.create_calls += 1
        if self.create_raises:
            raise RuntimeError("simulated INSERT failure")
        self.issues.append(kwargs)

    def read_unresolved_issue_keys_by_drift_type(self, drift_type):
        self.key_fetch_calls += 1
        if self.key_fetch_raises:
            raise RuntimeError("simulated key-fetch failure")
        if drift_type != DRIFT_QBO_VOIDED:
            return []
        keys = []
        for realm_id, entity_type, qbo_id, status in self.seeded_issues:
            if status == "resolved":
                continue
            if qbo_id is None:
                continue
            keys.append((realm_id, entity_type, qbo_id))
        return keys


def _fake_issue_service(
    *,
    seeded_issues=None,
    create_raises=False,
    key_fetch_raises=False,
):
    repo = _FakeIssueRepo(
        seeded_issues=seeded_issues,
        create_raises=create_raises,
        key_fetch_raises=key_fetch_raises,
    )
    svc = ReconciliationService(repo=repo)
    return svc, repo


# ------------------------------------------------------------------ #
# Bill fakes
# ------------------------------------------------------------------ #


class _FakeBillClient:
    def __init__(
        self,
        *,
        bills=None,
        ids=None,
        get_raises=None,
        query_raises=None,
        ids_raises=None,
        get_raises_by_id=None,
        realm_id=None,
    ):
        self.realm_id = realm_id
        self._bills = bills or []
        self._ids = list(ids) if ids is not None else []
        self._get_raises = get_raises
        self._query_raises = query_raises
        self._ids_raises = ids_raises
        self._get_raises_by_id = get_raises_by_id or {}
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_all_bills(self):
        if self._query_raises:
            raise self._query_raises
        return self._bills

    def query_all_bill_ids(self):
        if self._ids_raises:
            raise self._ids_raises
        return [normalize_qbo_id(i) for i in self._ids]

    def get_bill(self, bill_id):
        self.get_calls.append(bill_id)
        if bill_id in self._get_raises_by_id:
            raise self._get_raises_by_id[bill_id]
        if self._get_raises:
            raise self._get_raises
        return SimpleNamespace(id=bill_id)


class _FakeQboBillRepo:
    def __init__(self, *, by_qbo_id=None, by_realm=None):
        self._by_qbo_id = by_qbo_id
        self._by_realm = by_realm or []

    def read_by_qbo_id(self, qbo_id):
        return self._by_qbo_id

    def read_by_realm_id(self, realm_id):
        return self._by_realm


class _FakeBillMappingRepo:
    def __init__(self, *, mapping=None, mappings_by_local_id=None):
        self._mapping = mapping
        self._mappings_by_local_id = mappings_by_local_id or {}

    def read_by_qbo_bill_id(self, local_id):
        if local_id in self._mappings_by_local_id:
            return self._mappings_by_local_id[local_id]
        return self._mapping


def _patch_bill_stack(monkeypatch, *, client, qbo_repo, mapping_repo):
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.external.client.QboBillClient",
        lambda realm_id: _attach_realm(client, realm_id),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.persistence.repo.QboBillRepository",
        lambda: qbo_repo,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.connector.bill.persistence.repo.BillBillRepository",
        lambda: mapping_repo,
    )


def _attach_realm(client, realm_id):
    client.realm_id = realm_id
    return client


# ------------------------------------------------------------------ #
# Purchase fakes (void-diff only)
# ------------------------------------------------------------------ #


class _FakePurchaseVoidClient:
    def __init__(self, *, ids=None, get_raises=None, ids_raises=None, realm_id=None):
        self.realm_id = realm_id
        self._ids = list(ids) if ids is not None else []
        self._get_raises = get_raises
        self._ids_raises = ids_raises
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_all_purchases(self):
        return []

    def query_all_purchase_ids(self):
        if self._ids_raises:
            raise self._ids_raises
        return [normalize_qbo_id(i) for i in self._ids]

    def get_purchase(self, purchase_id):
        self.get_calls.append(purchase_id)
        if self._get_raises:
            raise self._get_raises
        return SimpleNamespace(id=purchase_id)


class _FakeExpenseIdentityService:
    """U-301a: Purchase's void detector now sources local_rows from
    dbo.Expense's own (Id, QboId) identity (ExpenseService), not
    qbo.Purchase/qbo.PurchaseExpense staging+mapping — every row here is
    inherently "mapped" (it only exists because it carries a QboId), so
    there is no separate mapping-repo concept left to fake."""

    def __init__(self, *, identity_rows=None):
        self._rows = list(identity_rows or [])

    def read_qbo_ids_by_realm_id(self, realm_id):
        return {row.qbo_id for row in self._rows}

    def read_qbo_identity_rows_by_realm_id(self, realm_id):
        return self._rows


def _patch_purchase_void_stack(monkeypatch, *, client, identity_rows):
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.external.client.QboPurchaseClient",
        lambda realm_id: _attach_realm(client, realm_id),
    )
    monkeypatch.setattr(
        "entities.expense.business.service.ExpenseService",
        lambda: _FakeExpenseIdentityService(identity_rows=identity_rows),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.business.service.QboPurchaseService",
        lambda: SimpleNamespace(upsert_from_external=lambda *a, **k: (None, [])),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector",
        lambda: SimpleNamespace(sync_from_qbo_purchase=lambda **k: None),
    )


# ------------------------------------------------------------------ #
# VendorCredit fakes (void-diff only)
# ------------------------------------------------------------------ #


class _FakeVendorCreditVoidClient:
    def __init__(self, *, ids=None, get_raises=None, ids_raises=None, realm_id=None):
        self.realm_id = realm_id
        self._ids = list(ids) if ids is not None else []
        self._get_raises = get_raises
        self._ids_raises = ids_raises
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_all_vendor_credits(self):
        return []

    def query_all_vendor_credit_ids(self):
        if self._ids_raises:
            raise self._ids_raises
        return [normalize_qbo_id(i) for i in self._ids]

    def get_vendor_credit(self, vendor_credit_id):
        self.get_calls.append(vendor_credit_id)
        if self._get_raises:
            raise self._get_raises
        return SimpleNamespace(id=vendor_credit_id)


class _FakeQboVendorCreditRepo:
    def __init__(self, *, by_realm=None):
        self._by_realm = by_realm or []

    def read_by_qbo_id_and_realm_id(self, qbo_id, realm_id):
        return None

    def read_by_realm_id(self, realm_id):
        return self._by_realm


class _FakeVendorCreditMappingRepo:
    def __init__(self, mapping=None):
        self._mapping = mapping

    def read_by_qbo_vendor_credit_id(self, local_id):
        return self._mapping


def _patch_vendor_credit_void_stack(monkeypatch, *, client, qbo_repo, mapping_repo):
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.external.client.QboVendorCreditClient",
        lambda realm_id: _attach_realm(client, realm_id),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.persistence.repo.QboVendorCreditRepository",
        lambda: qbo_repo,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo.VendorCreditBillCreditMappingRepository",
        lambda: mapping_repo,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.business.service.QboVendorCreditService",
        lambda: SimpleNamespace(upsert_from_external=lambda *a, **k: (None, [])),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service.VendorCreditBillCreditConnector",
        lambda: SimpleNamespace(sync_from_qbo_vendor_credit=lambda *a, **k: None),
    )


# ------------------------------------------------------------------ #
# Strict pager integration fake
# ------------------------------------------------------------------ #


class _PagedQueryHttpClient:
    def __init__(self, *, first_page_rows=None, first_page_data=None, second_page_data=None):
        self._first_page_rows = first_page_rows
        self._first_page_data = first_page_data
        self._second_page_data = second_page_data
        self.call_count = 0

    def get(self, path, *, params=None, operation_name=None):
        self.call_count += 1
        if self.call_count == 1:
            if self._first_page_data is not None:
                return self._first_page_data
            return {"QueryResponse": {"Bill": self._first_page_rows}}
        return self._second_page_data

    def close(self):
        pass


_FAULT_PAYLOAD = {
    "Error": [{"code": "4000", "Message": "query error"}],
    "type": "ValidationFault",
}


def _bill_row(bill_id):
    return {"Id": bill_id, "SyncToken": "0"}


# ------------------------------------------------------------------ #
# Bill tests
# ------------------------------------------------------------------ #


def test_void_diff_flags_only_absent_ids(monkeypatch):
    svc, repo = _fake_issue_service()
    local_a = SimpleNamespace(id=1, qbo_id="A")
    local_b = SimpleNamespace(id=2, qbo_id="B")
    local_c = SimpleNamespace(id=3, qbo_id="C")
    mappings = {
        1: SimpleNamespace(bill_id=101),
        2: SimpleNamespace(bill_id=102),
        3: SimpleNamespace(bill_id=103),
    }
    client = _FakeBillClient(
        ids=["A", "B"],
        get_raises_by_id={"C": QboNotFoundError("gone")},
    )
    _patch_bill_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboBillRepo(by_realm=[local_a, local_b, local_c]),
        mapping_repo=_FakeBillMappingRepo(mappings_by_local_id=mappings),
    )

    result = svc.reconcile_bills(realm_id="realm-1")

    assert result["flagged"] == 1
    void_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert len(void_issues) == 1
    assert void_issues[0]["qbo_id"] == "C"
    assert client.get_calls == ["C"]


@pytest.mark.parametrize(
    "ids_raises",
    [
        QboRateLimitError("rate limited"),
        QboUnexpectedError("bad response", detail="no QueryResponse"),
    ],
)
def test_void_diff_id_fetch_error_flags_nothing(monkeypatch, ids_raises):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=10, qbo_id="B-GONE")
    client = _FakeBillClient(ids_raises=ids_raises, get_raises=QboNotFoundError("gone"))
    _patch_bill_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboBillRepo(by_realm=[local]),
        mapping_repo=_FakeBillMappingRepo(mapping=SimpleNamespace(bill_id=55)),
    )

    result = svc.reconcile_bills(realm_id="realm-1")

    assert result["flagged"] == 0
    assert result["errors"] >= 1
    void_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert len(void_issues) == 0
    assert client.get_calls == []


def test_void_diff_partial_page_is_not_treated_as_complete():
    first_page = [_bill_row(str(i)) for i in range(1000)]
    http_client = _PagedQueryHttpClient(
        first_page_rows=first_page,
        second_page_data={"unexpected": True},
    )
    client = QboBillClient(realm_id="realm-test", http_client=http_client)

    with pytest.raises(QboUnexpectedError):
        client.query_all_bill_ids()

    http_client.call_count = 0
    legacy = client.query_all_bills()
    assert len(legacy) == 1000


def test_void_diff_query_fault_page_is_not_treated_as_complete():
    first_page = [_bill_row(str(i)) for i in range(1000)]
    http_client = _PagedQueryHttpClient(
        first_page_rows=first_page,
        second_page_data={"QueryResponse": {"Fault": _FAULT_PAYLOAD}},
    )
    client = QboBillClient(realm_id="realm-test", http_client=http_client)

    with pytest.raises(QboUnexpectedError):
        client.query_all_bill_ids()

    http_client.call_count = 0
    legacy = client.query_all_bills()
    assert len(legacy) == 1000


@pytest.mark.parametrize(
    "second_page_data",
    [
        {"QueryResponse": None},                       # explicit null QueryResponse
        {"QueryResponse": {"Bill": None}},             # explicit null row payload
        {"Fault": {}, "QueryResponse": {}},            # EMPTY (falsy) top-level Fault
        {"QueryResponse": {"Fault": {}, "Bill": []}},  # EMPTY (falsy) nested Fault
    ],
)
def test_void_diff_falsy_anomaly_signals_still_raise(second_page_data):
    """Anomalies are detected by key PRESENCE, never truthiness.

    Every body here is falsy at the point that matters — an empty Fault dict, an
    explicit JSON null — so a truthiness check (`if data.get("Fault")`,
    `... or []`) would wave it through as an empty page and silently truncate the
    id set. Presence checks catch all four.
    """
    http_client = _PagedQueryHttpClient(
        first_page_rows=[_bill_row(str(i)) for i in range(1000)],
        second_page_data=second_page_data,
    )
    client = QboBillClient(realm_id="realm-test", http_client=http_client)

    with pytest.raises(QboUnexpectedError):
        client.query_all_bill_ids()


@pytest.mark.parametrize("falsy_non_list", ["", 0, False, {}])
def test_void_diff_falsy_non_list_rows_raises(falsy_non_list):
    """A falsy NON-list entity payload must RAISE, not read as an empty page.

    Same slip as the QueryResponse coercion, one level down: `.get(entity) or []`
    turns "" / 0 / False into an empty page and truncates the id set. An absent key
    or an explicit [] are the only legitimate empty-page signals.
    """
    http_client = _PagedQueryHttpClient(
        first_page_rows=[_bill_row(str(i)) for i in range(1000)],
        second_page_data={"QueryResponse": {"Bill": falsy_non_list}},
    )
    client = QboBillClient(realm_id="realm-test", http_client=http_client)

    with pytest.raises(QboUnexpectedError):
        client.query_all_bill_ids()


@pytest.mark.parametrize("legitimate_empty", [None, []])
def test_void_diff_legitimate_empty_page_still_ends_pagination(legitimate_empty):
    """The flip side: an absent key or an explicit [] must NOT raise.

    QBO returns one of these for a genuinely empty result and for the page after
    an exact multiple of the page size, so over-tightening the guard above would
    make every full-page-boundary sweep abort.
    """
    page_two = {"QueryResponse": {}} if legitimate_empty is None else {"QueryResponse": {"Bill": []}}
    http_client = _PagedQueryHttpClient(
        first_page_rows=[_bill_row(str(i)) for i in range(1000)],
        second_page_data=page_two,
    )
    client = QboBillClient(realm_id="realm-test", http_client=http_client)

    assert len(client.query_all_bill_ids()) == 1000


@pytest.mark.parametrize("falsy_non_dict", [[], "", 0, False])
def test_void_diff_falsy_non_dict_query_response_raises(falsy_non_dict):
    """A falsy non-dict QueryResponse must RAISE, not read as an empty page.

    Regression pin for a real slip: writing the coercion as
    `data.get("QueryResponse") or {}` (instead of an explicit `is None` test)
    turns [] / "" / 0 / False into an empty dict, which yields zero rows, breaks
    pagination, and returns a TRUNCATED id set that looks complete — the exact
    failure this helper exists to prevent.
    """
    http_client = _PagedQueryHttpClient(
        first_page_rows=[_bill_row(str(i)) for i in range(1000)],
        second_page_data={"QueryResponse": falsy_non_dict},
    )
    client = QboBillClient(realm_id="realm-test", http_client=http_client)

    with pytest.raises(QboUnexpectedError):
        client.query_all_bill_ids()


@pytest.mark.parametrize(
    "client_cls, pager_name",
    [
        (QboBillClient, "query_all_bill_ids"),
        (QboPurchaseClient, "query_all_purchase_ids"),
        (QboVendorCreditClient, "query_all_vendor_credit_ids"),
    ],
)
def test_void_diff_top_level_fault_raises(client_cls, pager_name):
    """A top-level Fault must raise on its OWN merit.

    The body deliberately carries a well-formed empty QueryResponse alongside the
    Fault: without it, the older missing-QueryResponse guard would raise anyway and
    this test would pass even with the top-level Fault guard deleted (vacuous). With
    it, the ONLY thing standing between this body and a silent empty-page break is
    the `data.get("Fault")` guard.
    """
    http_client = _PagedQueryHttpClient(
        first_page_data={
            "Fault": _FAULT_PAYLOAD,
            "QueryResponse": {},
            "time": "2026-07-27T12:00:00.000-07:00",
        },
    )
    client = client_cls(realm_id="realm-test", http_client=http_client)

    with pytest.raises(QboUnexpectedError):
        getattr(client, pager_name)()


def test_void_diff_false_positive_is_never_flagged(monkeypatch):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=5, qbo_id="GHOST")
    client = _FakeBillClient(ids=[])
    _patch_bill_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboBillRepo(by_realm=[local]),
        mapping_repo=_FakeBillMappingRepo(mapping=SimpleNamespace(bill_id=77)),
    )

    result = svc.reconcile_bills(realm_id="realm-1")

    assert result["flagged"] == 0
    void_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert len(void_issues) == 0


def test_void_diff_candidate_ceiling_aborts(monkeypatch):
    monkeypatch.setenv("QBO_RECONCILE_VOID_MAX_CANDIDATES", "2")
    svc, repo = _fake_issue_service()
    mapped = [
        SimpleNamespace(id=i, qbo_id=f"B-{i}")
        for i in range(1, 6)
    ]
    mappings = {i: SimpleNamespace(bill_id=100 + i) for i in range(1, 6)}
    client = _FakeBillClient(ids=[])
    _patch_bill_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboBillRepo(by_realm=mapped),
        mapping_repo=_FakeBillMappingRepo(mappings_by_local_id=mappings),
    )

    result = svc.reconcile_bills(realm_id="realm-1")

    assert result["flagged"] == 0
    assert result["errors"] >= 1
    ceiling_issues = [
        i for i in repo.issues
        if "ceiling" in (i.get("details") or "").lower()
    ]
    assert len(ceiling_issues) == 1
    assert client.get_calls == []


def test_void_diff_is_realm_scoped(monkeypatch):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=1, qbo_id="R1-BILL")
    other_realm_local = SimpleNamespace(id=99, qbo_id="R2-BILL")
    client = _FakeBillClient(
        ids=[],
        get_raises=QboNotFoundError("gone"),
    )

    class _RealmScopedRepo:
        def read_by_qbo_id(self, qbo_id):
            return None

        def read_by_realm_id(self, realm_id):
            if realm_id == "realm-target":
                return [local]
            return [other_realm_local]

    _patch_bill_stack(
        monkeypatch,
        client=client,
        qbo_repo=_RealmScopedRepo(),
        mapping_repo=_FakeBillMappingRepo(mapping=SimpleNamespace(bill_id=42)),
    )

    svc.reconcile_bills(realm_id="realm-target")

    assert client.realm_id == "realm-target"
    void_ids = {
        i["qbo_id"]
        for i in repo.issues
        if i["drift_type"] == DRIFT_QBO_VOIDED and i.get("qbo_id")
    }
    assert void_ids == {"R1-BILL"}


def test_void_diff_id_type_mismatch_does_not_flag(monkeypatch):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=1, qbo_id="123")
    client = _FakeBillClient(ids=[123])
    _patch_bill_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboBillRepo(by_realm=[local]),
        mapping_repo=_FakeBillMappingRepo(mapping=SimpleNamespace(bill_id=11)),
    )

    result = svc.reconcile_bills(realm_id="realm-1")

    assert result["flagged"] == 0
    assert client.get_calls == []


def test_void_diff_empty_edges(monkeypatch):
    svc, repo = _fake_issue_service()
    client = _FakeBillClient(ids=["LIVE"])
    _patch_bill_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboBillRepo(by_realm=[]),
        mapping_repo=_FakeBillMappingRepo(mapping=SimpleNamespace(bill_id=1)),
    )
    result = svc.reconcile_bills(realm_id="realm-1")
    assert result["flagged"] == 0
    assert not [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]

    svc2, repo2 = _fake_issue_service()
    bad_rows = [
        SimpleNamespace(id=1, qbo_id=None),
        SimpleNamespace(id=2, qbo_id=""),
        SimpleNamespace(id=3, qbo_id="   "),
    ]
    client2 = _FakeBillClient(ids=[])
    _patch_bill_stack(
        monkeypatch,
        client=client2,
        qbo_repo=_FakeQboBillRepo(by_realm=bad_rows),
        mapping_repo=_FakeBillMappingRepo(mapping=SimpleNamespace(bill_id=2)),
    )
    result2 = svc2.reconcile_bills(realm_id="realm-1")
    assert result2["flagged"] == 0
    assert not [i for i in repo2.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert client2.get_calls == []

    svc3, repo3 = _fake_issue_service()
    unmapped = SimpleNamespace(id=4, qbo_id="UNMAPPED")
    client3 = _FakeBillClient(ids=[])
    _patch_bill_stack(
        monkeypatch,
        client=client3,
        qbo_repo=_FakeQboBillRepo(by_realm=[unmapped]),
        mapping_repo=_FakeBillMappingRepo(mapping=None),
    )
    result3 = svc3.reconcile_bills(realm_id="realm-1")
    assert result3["flagged"] == 0
    assert not [i for i in repo3.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert client3.get_calls == []


def test_void_diff_matches_legacy_get_predicate(monkeypatch):
    """Equivalence holds only when candidate_count <= QBO_RECONCILE_VOID_MAX_CANDIDATES."""
    records = [
        ("A", 1, False),
        ("B", 2, False),
        ("C", 3, True),
        ("D", 4, False),
        ("E", 5, True),
        ("F", 6, False),
    ]
    deleted_count = sum(1 for _, _, deleted in records if deleted)
    assert deleted_count <= DEFAULT_VOID_MAX_CANDIDATES
    mapped_locals = [
        SimpleNamespace(id=local_id, qbo_id=qbo_id)
        for qbo_id, local_id, _deleted in records
    ]
    mappings = {
        local_id: SimpleNamespace(bill_id=100 + local_id)
        for _, local_id, _ in records
    }
    live_ids = [qbo_id for qbo_id, _, deleted in records if not deleted]
    get_raises_by_id = {
        qbo_id: QboNotFoundError("gone")
        for qbo_id, _, deleted in records
        if deleted
    }

    legacy_flagged = {
        qbo_id
        for qbo_id, _, deleted in records
        if deleted
    }

    client = _FakeBillClient(
        ids=live_ids,
        get_raises_by_id=get_raises_by_id,
    )
    svc, repo = _fake_issue_service()
    _patch_bill_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboBillRepo(by_realm=mapped_locals),
        mapping_repo=_FakeBillMappingRepo(mappings_by_local_id=mappings),
    )

    result = svc.reconcile_bills(realm_id="realm-1")

    new_flagged = {
        i["qbo_id"]
        for i in repo.issues
        if i["drift_type"] == DRIFT_QBO_VOIDED and i.get("qbo_id")
    }
    assert new_flagged == legacy_flagged
    assert result["flagged"] == len(legacy_flagged)


def test_normalize_qbo_id():
    assert normalize_qbo_id(None) is None
    assert normalize_qbo_id("") is None
    assert normalize_qbo_id("  ") is None
    assert normalize_qbo_id(123) == "123"
    assert normalize_qbo_id(" 45 ") == "45"


# ------------------------------------------------------------------ #
# Purchase-side variants (tests 1 and 2)
# ------------------------------------------------------------------ #


def test_purchase_void_diff_flags_only_absent_ids(monkeypatch):
    svc, repo = _fake_issue_service()
    local_a = SimpleNamespace(id=1, qbo_id="PA")
    local_b = SimpleNamespace(id=2, qbo_id="PB")
    local_c = SimpleNamespace(id=3, qbo_id="PC")
    client = _FakePurchaseVoidClient(
        ids=["PA", "PB"],
        get_raises=QboNotFoundError("gone"),
    )
    _patch_purchase_void_stack(
        monkeypatch,
        client=client,
        identity_rows=[local_a, local_b, local_c],
    )

    result = svc.reconcile_purchases(realm_id="realm-1")

    assert result["flagged"] == 1
    void_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert len(void_issues) == 1
    assert void_issues[0]["qbo_id"] == "PC"
    assert client.get_calls == ["PC"]


@pytest.mark.parametrize(
    "ids_raises",
    [
        QboRateLimitError("rate limited"),
        QboUnexpectedError("bad response", detail="no QueryResponse"),
    ],
)
def test_purchase_void_diff_id_fetch_error_flags_nothing(monkeypatch, ids_raises):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=10, qbo_id="P-GONE")
    client = _FakePurchaseVoidClient(ids_raises=ids_raises, get_raises=QboNotFoundError("gone"))
    _patch_purchase_void_stack(
        monkeypatch,
        client=client,
        identity_rows=[local],
    )

    result = svc.reconcile_purchases(realm_id="realm-1")

    assert result["flagged"] == 0
    assert result["errors"] >= 1
    void_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert len(void_issues) == 0
    assert client.get_calls == []


# U-301a: Purchase's own dedup-cache loop (service.py's `key = (realm_id, "Expense",
# qbo_id)` / `if key in void_keys: ... else: void_keys.add(key)`) is a hand-copied
# per-family loop, NOT the shared detect_void_absent_candidates engine — removing
# "purchase" from _VOID_DETECTOR_CASES (below) means Bill/VendorCredit's continued
# instances no longer exercise Purchase's own copy of it at all. These three tests
# restore that coverage directly (mirroring test_void_issue_deduped_when_unresolved_issue_exists
# / test_void_issue_written_when_no_existing_issue / test_dedupe_is_idempotent_within_one_run).
def test_purchase_void_issue_deduped_when_unresolved_issue_exists(monkeypatch):
    realm_id = "realm-1"
    qbo_id = "GONE-404"
    svc, repo = _fake_issue_service(seeded_issues=[(realm_id, "Expense", qbo_id, "open")])
    local = SimpleNamespace(id=1, qbo_id=qbo_id)
    client = _FakePurchaseVoidClient(ids=[], get_raises=QboNotFoundError("gone"))
    _patch_purchase_void_stack(monkeypatch, client=client, identity_rows=[local])

    result = svc.reconcile_purchases(realm_id=realm_id)

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 1
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 0


def test_purchase_void_issue_written_when_no_existing_issue(monkeypatch):
    realm_id = "realm-1"
    qbo_id = "GONE-404"
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=1, qbo_id=qbo_id)
    client = _FakePurchaseVoidClient(ids=[], get_raises=QboNotFoundError("gone"))
    _patch_purchase_void_stack(monkeypatch, client=client, identity_rows=[local])

    result = svc.reconcile_purchases(realm_id=realm_id)

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 0
    void_issues = _void_issues_for_qbo_id(repo, qbo_id)
    assert len(void_issues) == 1
    assert void_issues[0]["entity_type"] == "Expense"


def test_purchase_dedupe_is_idempotent_within_one_run(monkeypatch):
    realm_id = "realm-1"
    qbo_id = "SHARED-404"
    local_a = SimpleNamespace(id=1, qbo_id=qbo_id)
    local_b = SimpleNamespace(id=2, qbo_id=qbo_id)
    svc, repo = _fake_issue_service()
    client = _FakePurchaseVoidClient(ids=[], get_raises=QboNotFoundError("gone"))
    _patch_purchase_void_stack(monkeypatch, client=client, identity_rows=[local_a, local_b])

    result = svc.reconcile_purchases(realm_id=realm_id)

    assert result["flagged"] == 2
    assert result["flagged_deduped"] == 1
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 1


# ------------------------------------------------------------------ #
# VendorCredit-side variants (tests 1 and 2)
# ------------------------------------------------------------------ #


def test_vendor_credit_void_diff_flags_only_absent_ids(monkeypatch):
    svc, repo = _fake_issue_service()
    local_a = SimpleNamespace(id=1, qbo_id="VA")
    local_b = SimpleNamespace(id=2, qbo_id="VB")
    local_c = SimpleNamespace(id=3, qbo_id="VC")
    client = _FakeVendorCreditVoidClient(
        ids=["VA", "VB"],
        get_raises=QboNotFoundError("gone"),
    )

    class _VendorCreditMappingById:
        def read_by_qbo_vendor_credit_id(self, local_id):
            return SimpleNamespace(bill_credit_id=300 + local_id)

    _patch_vendor_credit_void_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboVendorCreditRepo(by_realm=[local_a, local_b, local_c]),
        mapping_repo=_VendorCreditMappingById(),
    )

    result = svc.reconcile_vendor_credits(realm_id="realm-1")

    assert result["flagged"] == 1
    void_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert len(void_issues) == 1
    assert void_issues[0]["qbo_id"] == "VC"
    assert client.get_calls == ["VC"]


@pytest.mark.parametrize(
    "ids_raises",
    [
        QboRateLimitError("rate limited"),
        QboUnexpectedError("bad response", detail="no QueryResponse"),
    ],
)
def test_vendor_credit_void_diff_id_fetch_error_flags_nothing(monkeypatch, ids_raises):
    svc, repo = _fake_issue_service()
    local = SimpleNamespace(id=20, qbo_id="VC-GONE")
    client = _FakeVendorCreditVoidClient(ids_raises=ids_raises, get_raises=QboNotFoundError("gone"))
    _patch_vendor_credit_void_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboVendorCreditRepo(by_realm=[local]),
        mapping_repo=_FakeVendorCreditMappingRepo(mapping=SimpleNamespace(bill_credit_id=66)),
    )

    result = svc.reconcile_vendor_credits(realm_id="realm-1")

    assert result["flagged"] == 0
    assert result["errors"] >= 1
    void_issues = [i for i in repo.issues if i["drift_type"] == DRIFT_QBO_VOIDED]
    assert len(void_issues) == 0
    assert client.get_calls == []


# ------------------------------------------------------------------ #
# U-160: VendorCredit full-record pager + void-issue dedupe
# ------------------------------------------------------------------ #


class _QueryStringRecordingHttpClient:
    """Records QBO query strings for pagination assertions."""

    def __init__(self, *, pages=None):
        self._pages = list(pages or [])
        self.query_strings = []
        self._call = 0

    def get(self, path, *, params=None, operation_name=None):
        query = (params or {}).get("query", "")
        self.query_strings.append(query)
        idx = self._call
        self._call += 1
        if idx < len(self._pages):
            return self._pages[idx]
        return {"QueryResponse": {"VendorCredit": []}}

    def close(self):
        pass


def _vendor_credit_row(vc_id):
    return {"Id": str(vc_id), "SyncToken": "0"}


def test_vendor_credit_full_record_pager_pages_at_1000():
    first_page = [_vendor_credit_row(i) for i in range(1, 1001)]
    second_page = [_vendor_credit_row(i) for i in range(1001, 1003)]
    http_client = _QueryStringRecordingHttpClient(
        pages=[
            {"QueryResponse": {"VendorCredit": first_page}},
            {"QueryResponse": {"VendorCredit": second_page}},
        ],
    )
    client = QboVendorCreditClient(realm_id="realm-test", http_client=http_client)

    result = client.query_all_vendor_credits()

    assert len(result) == 1002
    assert len(http_client.query_strings) == 2
    assert "MAXRESULTS 1000" in http_client.query_strings[0]
    assert "STARTPOSITION 1" in http_client.query_strings[0]
    assert "STARTPOSITION 1001" in http_client.query_strings[1]
    assert "MAXRESULTS 1000" in http_client.query_strings[1]


def test_vendor_credit_full_record_pager_stops_on_short_page():
    first_page = [_vendor_credit_row(i) for i in range(1, 1001)]
    short_page = [_vendor_credit_row(1001)]
    http_client = _QueryStringRecordingHttpClient(
        pages=[
            {"QueryResponse": {"VendorCredit": first_page}},
            {"QueryResponse": {"VendorCredit": short_page}},
        ],
    )
    client = QboVendorCreditClient(realm_id="realm-test", http_client=http_client)

    result = client.query_all_vendor_credits()

    assert len(result) == 1001
    assert len(http_client.query_strings) == 2


# U-301a: "purchase" removed from this list. Its local_rows source is now
# dbo.Expense's own identity (ExpenseService), structurally different from
# bill/vendor_credit's still-qbo.*-staging-backed qbo_repo+mapping_repo shape
# these generic cases assume — forcing it back into this abstraction would be
# a special case bandaid, not a real fit. detect_void_absent_candidates itself
# is untouched, so bill/vendor_credit continue to prove its shared control
# flow identically; purchase's own family-specific behavior — including its
# own copy of the dedup-cache loop, NOT shared code, so bill/vendor_credit's
# continued instances don't exercise it — is covered by the dedicated tests
# above in THIS file (test_purchase_void_diff_*,
# test_purchase_void_issue_deduped_when_unresolved_issue_exists,
# test_purchase_void_issue_written_when_no_existing_issue,
# test_purchase_dedupe_is_idempotent_within_one_run) and
# test_key_fetch_is_once_per_run_across_detectors below, plus
# tests/test_qbo_reconcile_purchase_vendorcredit.py's missing-locally tests.
# Re-add it here once bill/vendor_credit's own fan-out repoint (booked
# follow-up to this pilot) lands and the shape is uniform again.
_VOID_DETECTOR_CASES = [
    pytest.param(
        "bill",
        "Bill",
        "reconcile_bills",
        id="bill",
    ),
    pytest.param(
        "vendor_credit",
        "BillCredit",
        "reconcile_vendor_credits",
        id="vendor_credit",
    ),
]


def _void_issues_for_qbo_id(repo, qbo_id):
    return [
        i
        for i in repo.issues
        if i["drift_type"] == DRIFT_QBO_VOIDED and i.get("qbo_id") == qbo_id
    ]


def _patch_void_detector(monkeypatch, detector, *, client, qbo_repo, mapping_repo):
    if detector == "bill":
        _patch_bill_stack(
            monkeypatch,
            client=client,
            qbo_repo=qbo_repo,
            mapping_repo=mapping_repo,
        )
    else:
        _patch_vendor_credit_void_stack(
            monkeypatch,
            client=client,
            qbo_repo=qbo_repo,
            mapping_repo=mapping_repo,
        )


def _make_void_client(detector, *, ids=None, get_raises=None, get_raises_by_id=None):
    if detector == "bill":
        return _FakeBillClient(
            ids=ids if ids is not None else [],
            get_raises=get_raises,
            get_raises_by_id=get_raises_by_id or {},
        )
    return _FakeVendorCreditVoidClient(
        ids=ids if ids is not None else [],
        get_raises=get_raises,
    )


def _make_void_qbo_repo(detector, locals_list):
    if detector == "bill":
        return _FakeQboBillRepo(by_realm=locals_list)
    return _FakeQboVendorCreditRepo(by_realm=locals_list)


def _make_void_mapping_repo(detector, mappings_by_local_id=None, mapping=None):
    if detector == "bill":
        return _FakeBillMappingRepo(
            mappings_by_local_id=mappings_by_local_id or {},
            mapping=mapping,
        )

    if mappings_by_local_id:

        class _VendorCreditMappingById:
            def read_by_qbo_vendor_credit_id(self, local_id):
                return mappings_by_local_id[local_id]

        return _VendorCreditMappingById()
    return _FakeVendorCreditMappingRepo(mapping=mapping)


def _run_void_reconcile(svc, reconcile_fn, realm_id):
    return getattr(svc, reconcile_fn)(realm_id=realm_id)


def _standard_void_locals(detector, qbo_id, *, local_id=1):
    return [SimpleNamespace(id=local_id, qbo_id=qbo_id)]


def _standard_void_mapping(detector, local_id):
    if detector == "bill":
        return SimpleNamespace(bill_id=100 + local_id)
    return SimpleNamespace(bill_credit_id=300 + local_id)


@pytest.mark.parametrize("detector,entity_type,reconcile_fn", _VOID_DETECTOR_CASES)
def test_void_issue_deduped_when_unresolved_issue_exists(
    monkeypatch, detector, entity_type, reconcile_fn
):
    realm_id = "realm-1"
    qbo_id = "GONE-404"
    svc, repo = _fake_issue_service(
        seeded_issues=[(realm_id, entity_type, qbo_id, "open")],
    )
    local = _standard_void_locals(detector, qbo_id)[0]
    client = _make_void_client(
        detector,
        get_raises=QboNotFoundError("gone"),
    )
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client,
        qbo_repo=_make_void_qbo_repo(detector, [local]),
        mapping_repo=_make_void_mapping_repo(
            detector,
            mappings_by_local_id={local.id: _standard_void_mapping(detector, local.id)},
        ),
    )

    result = _run_void_reconcile(svc, reconcile_fn, realm_id)

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 1
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 0
    assert client.get_calls == [qbo_id]


@pytest.mark.parametrize("detector,entity_type,reconcile_fn", _VOID_DETECTOR_CASES)
def test_void_issue_written_when_no_existing_issue(
    monkeypatch, detector, entity_type, reconcile_fn
):
    realm_id = "realm-1"
    qbo_id = "GONE-404"
    svc, repo = _fake_issue_service()
    local = _standard_void_locals(detector, qbo_id)[0]
    client = _make_void_client(
        detector,
        get_raises=QboNotFoundError("gone"),
    )
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client,
        qbo_repo=_make_void_qbo_repo(detector, [local]),
        mapping_repo=_make_void_mapping_repo(
            detector,
            mappings_by_local_id={local.id: _standard_void_mapping(detector, local.id)},
        ),
    )

    result = _run_void_reconcile(svc, reconcile_fn, realm_id)

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 0
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 1


@pytest.mark.parametrize("detector,entity_type,reconcile_fn", _VOID_DETECTOR_CASES)
def test_resolved_issue_does_not_suppress(
    monkeypatch, detector, entity_type, reconcile_fn
):
    realm_id = "realm-1"
    qbo_id = "GONE-404"
    svc, repo = _fake_issue_service(
        seeded_issues=[(realm_id, entity_type, qbo_id, "resolved")],
    )
    local = _standard_void_locals(detector, qbo_id)[0]
    client = _make_void_client(
        detector,
        get_raises=QboNotFoundError("gone"),
    )
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client,
        qbo_repo=_make_void_qbo_repo(detector, [local]),
        mapping_repo=_make_void_mapping_repo(
            detector,
            mappings_by_local_id={local.id: _standard_void_mapping(detector, local.id)},
        ),
    )

    result = _run_void_reconcile(svc, reconcile_fn, realm_id)

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 0
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 1


@pytest.mark.parametrize("detector,entity_type,reconcile_fn", _VOID_DETECTOR_CASES)
def test_acknowledged_issue_does_suppress(
    monkeypatch, detector, entity_type, reconcile_fn
):
    realm_id = "realm-1"
    qbo_id = "GONE-404"
    svc, repo = _fake_issue_service(
        seeded_issues=[(realm_id, entity_type, qbo_id, "acknowledged")],
    )
    local = _standard_void_locals(detector, qbo_id)[0]
    client = _make_void_client(
        detector,
        get_raises=QboNotFoundError("gone"),
    )
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client,
        qbo_repo=_make_void_qbo_repo(detector, [local]),
        mapping_repo=_make_void_mapping_repo(
            detector,
            mappings_by_local_id={local.id: _standard_void_mapping(detector, local.id)},
        ),
    )

    result = _run_void_reconcile(svc, reconcile_fn, realm_id)

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 1
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 0


@pytest.mark.parametrize("detector,entity_type,reconcile_fn", _VOID_DETECTOR_CASES)
def test_dedupe_key_isolates_realm_and_entity_type(
    monkeypatch, detector, entity_type, reconcile_fn
):
    realm_id = "realm-1"
    qbo_id = "GONE-404"
    other_entity = {"Bill": "Expense", "Expense": "Bill", "BillCredit": "Bill"}[
        entity_type
    ]
    svc, repo = _fake_issue_service(
        seeded_issues=[
            ("other-realm", entity_type, qbo_id, "open"),
            (realm_id, other_entity, qbo_id, "open"),
        ],
    )
    local = _standard_void_locals(detector, qbo_id)[0]
    client = _make_void_client(
        detector,
        get_raises=QboNotFoundError("gone"),
    )
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client,
        qbo_repo=_make_void_qbo_repo(detector, [local]),
        mapping_repo=_make_void_mapping_repo(
            detector,
            mappings_by_local_id={local.id: _standard_void_mapping(detector, local.id)},
        ),
    )

    result = _run_void_reconcile(svc, reconcile_fn, realm_id)

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 0
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 1


@pytest.mark.parametrize("detector,entity_type,reconcile_fn", _VOID_DETECTOR_CASES)
def test_dedupe_is_idempotent_within_one_run(
    monkeypatch, detector, entity_type, reconcile_fn
):
    realm_id = "realm-1"
    qbo_id = "SHARED-404"
    local_a = SimpleNamespace(id=1, qbo_id=qbo_id)
    local_b = SimpleNamespace(id=2, qbo_id=qbo_id)
    mappings = {
        1: _standard_void_mapping(detector, 1),
        2: _standard_void_mapping(detector, 2),
    }
    svc, repo = _fake_issue_service()
    client = _make_void_client(
        detector,
        get_raises=QboNotFoundError("gone"),
    )
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client,
        qbo_repo=_make_void_qbo_repo(detector, [local_a, local_b]),
        mapping_repo=_make_void_mapping_repo(detector, mappings_by_local_id=mappings),
    )

    result = _run_void_reconcile(svc, reconcile_fn, realm_id)

    assert result["flagged"] == 2
    assert result["flagged_deduped"] == 1
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 1


@pytest.mark.parametrize("detector,entity_type,reconcile_fn", _VOID_DETECTOR_CASES)
def test_key_fetch_failure_fails_open(
    monkeypatch, detector, entity_type, reconcile_fn
):
    """Suppression must NEVER be the failure mode — key-fetch failure writes anyway."""
    realm_id = "realm-1"
    qbo_id = "GONE-404"
    svc, repo = _fake_issue_service(key_fetch_raises=True)
    local = _standard_void_locals(detector, qbo_id)[0]
    client = _make_void_client(
        detector,
        get_raises=QboNotFoundError("gone"),
    )
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client,
        qbo_repo=_make_void_qbo_repo(detector, [local]),
        mapping_repo=_make_void_mapping_repo(
            detector,
            mappings_by_local_id={local.id: _standard_void_mapping(detector, local.id)},
        ),
    )

    result = _run_void_reconcile(svc, reconcile_fn, realm_id)

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 0
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 1


@pytest.mark.parametrize("detector,entity_type,reconcile_fn", _VOID_DETECTOR_CASES)
def test_failed_issue_write_is_not_cached_as_deduped(
    monkeypatch, detector, entity_type, reconcile_fn
):
    realm_id = "realm-1"
    qbo_id = "SHARED-404"
    local_a = SimpleNamespace(id=1, qbo_id=qbo_id)
    local_b = SimpleNamespace(id=2, qbo_id=qbo_id)
    mappings = {
        1: _standard_void_mapping(detector, 1),
        2: _standard_void_mapping(detector, 2),
    }
    svc, repo = _fake_issue_service(create_raises=True)
    client = _make_void_client(
        detector,
        get_raises=QboNotFoundError("gone"),
    )
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client,
        qbo_repo=_make_void_qbo_repo(detector, [local_a, local_b]),
        mapping_repo=_make_void_mapping_repo(detector, mappings_by_local_id=mappings),
    )

    result = _run_void_reconcile(svc, reconcile_fn, realm_id)

    assert repo.create_calls == 2
    assert result["flagged_deduped"] == 0
    assert len(_void_issues_for_qbo_id(repo, qbo_id)) == 0


def test_ceiling_summary_is_never_deduped(monkeypatch):
    """The ceiling summary is the mass-deletion alarm — deduping it would silence the alarm."""
    monkeypatch.setenv("QBO_RECONCILE_VOID_MAX_CANDIDATES", "2")
    svc, repo = _fake_issue_service()
    repo.issues.append(
        {
            "drift_type": DRIFT_QBO_VOIDED,
            "action": "flagged",
            "entity_type": "Bill",
            "qbo_id": None,
            "realm_id": "realm-1",
            "details": "Void detection aborted: prior run ceiling summary",
        }
    )
    mapped = [SimpleNamespace(id=i, qbo_id=f"B-{i}") for i in range(1, 6)]
    mappings = {i: SimpleNamespace(bill_id=100 + i) for i in range(1, 6)}
    client = _FakeBillClient(ids=[])
    _patch_bill_stack(
        monkeypatch,
        client=client,
        qbo_repo=_FakeQboBillRepo(by_realm=mapped),
        mapping_repo=_FakeBillMappingRepo(mappings_by_local_id=mappings),
    )

    result = svc.reconcile_bills(realm_id="realm-1")

    ceiling_issues = [
        i
        for i in repo.issues
        if i.get("qbo_id") is None and "ceiling" in (i.get("details") or "").lower()
    ]
    assert result["flagged"] == 0
    assert result["errors"] >= 1
    assert len(ceiling_issues) == 2
    assert client.get_calls == []


@pytest.mark.parametrize("detector,entity_type,reconcile_fn", _VOID_DETECTOR_CASES)
def test_key_fetch_is_lazy_when_no_voids(
    monkeypatch, detector, entity_type, reconcile_fn
):
    realm_id = "realm-1"
    qbo_id = "MAYBE-GONE"
    local = _standard_void_locals(detector, qbo_id)[0]

    # Candidates exist but every GET returns 200 — no 404, no key fetch.
    svc_clean, repo_clean = _fake_issue_service()
    client_ok = _make_void_client(detector)
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client_ok,
        qbo_repo=_make_void_qbo_repo(detector, [local]),
        mapping_repo=_make_void_mapping_repo(
            detector,
            mappings_by_local_id={local.id: _standard_void_mapping(detector, local.id)},
        ),
    )
    _run_void_reconcile(svc_clean, reconcile_fn, realm_id)
    assert repo_clean.key_fetch_calls == 0

    # Zero local candidates — early return, no key fetch.
    svc_empty, repo_empty = _fake_issue_service()
    client_empty = _make_void_client(detector)
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client_empty,
        qbo_repo=_make_void_qbo_repo(detector, []),
        mapping_repo=_make_void_mapping_repo(
            detector,
            mapping=_standard_void_mapping(detector, 1),
        ),
    )
    _run_void_reconcile(svc_empty, reconcile_fn, realm_id)
    assert repo_empty.key_fetch_calls == 0

    # Confirmed 404 — key fetch happens exactly once.
    svc_void, repo_void = _fake_issue_service()
    client_void = _make_void_client(
        detector,
        get_raises=QboNotFoundError("gone"),
    )
    _patch_void_detector(
        monkeypatch,
        detector,
        client=client_void,
        qbo_repo=_make_void_qbo_repo(detector, [local]),
        mapping_repo=_make_void_mapping_repo(
            detector,
            mappings_by_local_id={local.id: _standard_void_mapping(detector, local.id)},
        ),
    )
    _run_void_reconcile(svc_void, reconcile_fn, realm_id)
    assert repo_void.key_fetch_calls == 1


def test_key_fetch_is_once_per_run_across_detectors(monkeypatch):
    realm_id = "realm-1"
    svc, repo = _fake_issue_service()

    bill_local = SimpleNamespace(id=1, qbo_id="B-GONE")
    bill_client = _FakeBillClient(
        ids=[],
        get_raises_by_id={"B-GONE": QboNotFoundError("gone")},
    )
    _patch_bill_stack(
        monkeypatch,
        client=bill_client,
        qbo_repo=_FakeQboBillRepo(by_realm=[bill_local]),
        mapping_repo=_FakeBillMappingRepo(
            mappings_by_local_id={1: SimpleNamespace(bill_id=101)},
        ),
    )

    purchase_local = SimpleNamespace(id=2, qbo_id="P-GONE")
    purchase_client = _FakePurchaseVoidClient(
        ids=[],
        get_raises=QboNotFoundError("gone"),
    )
    _patch_purchase_void_stack(
        monkeypatch,
        client=purchase_client,
        identity_rows=[purchase_local],
    )

    svc.reconcile_bills(realm_id=realm_id)
    svc.reconcile_purchases(realm_id=realm_id)

    assert repo.key_fetch_calls == 1
    assert bill_client.get_calls == ["B-GONE"]
    assert purchase_client.get_calls == ["P-GONE"]
