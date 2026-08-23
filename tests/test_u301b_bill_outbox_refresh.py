"""U-301b: outbox worker's Bill refresh-on-SyncToken-mismatch, repointed onto
dbo.Bill's own native QboId (U-238a) via the shared verify_bill_qbo_identity
wrapper, with a fast path / legacy fallback / hard-refuse-on-conflict split.

No prior test coverage existed for _refresh_bill at all (grepped tests/ for
QboOutboxWorker/_refresh_from_qbo before writing this file) despite this path
being live-exercised (918 "done" sync_bill_to_qbo outbox rows in prod as of
this unit) -- these tests close that gap for the branches this unit's
repoint introduces/changes.
"""
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker


class _FakeIssueRepo:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)


class _FakeBillService:
    def __init__(self, bill):
        self._bill = bill

    def read_by_public_id(self, public_id):
        return self._bill


class _FakeBillBillRepo:
    def __init__(self, *, mapping=None):
        self._mapping = mapping

    def read_by_bill_id(self, bill_id):
        return self._mapping


class _FakeQboBillRepo:
    def __init__(self, *, by_id=None):
        self._by_id = by_id or {}

    def read_by_id(self, id_):
        return self._by_id.get(id_)


class _FakeQboBillClient:
    def __init__(self, *, get_bill_return=None):
        self.get_bill_calls = []
        self._get_bill_return = get_bill_return

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_bill(self, qbo_id):
        self.get_bill_calls.append(qbo_id)
        return self._get_bill_return or SimpleNamespace(id=qbo_id)


class _FakeQboBillService:
    def __init__(self):
        self.calls = []

    def upsert_from_external(self, qbo_bill, realm_id):
        self.calls.append((qbo_bill, realm_id))
        return SimpleNamespace(id=999, qbo_id=qbo_bill.id), []


class _FakeBillBillConnector:
    def __init__(self):
        self.calls = []

    def sync_from_qbo_bill(self, *, qbo_bill, qbo_bill_lines):
        self.calls.append((qbo_bill, qbo_bill_lines))


def _patch_bill_refresh_stack(
    monkeypatch,
    *,
    bill,
    bill_bill_mapping=None,
    qbo_bill_by_id=None,
    client=None,
    issue_repo=None,
):
    monkeypatch.setattr(
        "entities.bill.business.service.BillService", lambda: _FakeBillService(bill)
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.connector.bill.persistence.repo.BillBillRepository",
        lambda: _FakeBillBillRepo(mapping=bill_bill_mapping),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.persistence.repo.QboBillRepository",
        lambda: _FakeQboBillRepo(by_id=qbo_bill_by_id or {}),
    )
    fake_client = client or _FakeQboBillClient()
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.external.client.QboBillClient",
        lambda realm_id: fake_client,
    )
    qbo_bill_service = _FakeQboBillService()
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.business.service.QboBillService",
        lambda: qbo_bill_service,
    )
    connector = _FakeBillBillConnector()
    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.connector.bill.business.service.BillBillConnector",
        lambda: connector,
    )
    repo = issue_repo or _FakeIssueRepo()
    monkeypatch.setattr(
        "integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository",
        lambda: repo,
    )
    return fake_client, qbo_bill_service, connector, repo


def _row(*, entity_public_id="bill-pid-1", realm_id="realm-1", public_id="outbox-pid-1"):
    return SimpleNamespace(
        entity_public_id=entity_public_id, realm_id=realm_id, public_id=public_id
    )


def _conflicting_bill_fixture():
    """bill.qbo_id set AND a BillBill mapping exists whose resolved external
    QboId genuinely DISAGREES -- shared by both hard-refuse tests below."""
    bill = SimpleNamespace(id=42, qbo_id="QBO-42-DBO")
    mapping = SimpleNamespace(id=7, bill_id=42, qbo_bill_id=501)
    qbo_bill_by_id = {501: SimpleNamespace(id=501, qbo_id="QBO-42-STOLEN")}
    return bill, mapping, qbo_bill_by_id


@pytest.mark.parametrize(
    "label,bill_bill_mapping,qbo_bill_by_id",
    [
        (
            "unmapped: no BillBill mapping row yet -- verify trusts bill.qbo_id "
            "(nothing to disagree with)",
            None,
            {},
        ),
        (
            "mapping_agrees: BillBill mapping exists and its resolved external "
            "QboId matches -- verify confirms trust",
            SimpleNamespace(id=7, bill_id=42, qbo_bill_id=501),
            {501: SimpleNamespace(id=501, qbo_id="QBO-42")},
        ),
        (
            "mapping_dangling: BillBill mapping exists but its QboBillId no "
            "longer resolves to anything (orphaned mapping surviving a partial "
            "pull) -- _verify_dbo_qbo_identity's shared engine (inherited, not "
            "introduced by this diff -- the same KNOWN RESIDUAL the 3 sibling "
            "wrappers document) falls through to trusting bill.qbo_id here too, "
            "identically to the no-mapping case, NOT a hard-refuse (only a "
            "resolved-and-disagreeing external id triggers that)",
            SimpleNamespace(id=7, bill_id=42, qbo_bill_id=501),
            {},
        ),
    ],
    ids=["unmapped", "mapping_agrees", "mapping_dangling"],
)
def test_refresh_bill_fast_path_trusts_dbo_native_identity(
    monkeypatch, label, bill_bill_mapping, qbo_bill_by_id
):
    bill = SimpleNamespace(id=42, qbo_id="QBO-42")
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill, bill_bill_mapping=bill_bill_mapping, qbo_bill_by_id=qbo_bill_by_id
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    worker._refresh_bill(_row())

    assert client.get_bill_calls == ["QBO-42"], label
    assert len(connector.calls) == 1, label
    assert issue_repo.created == [], label


def test_refresh_bill_falls_through_to_legacy_lookup_when_no_dbo_qbo_id(monkeypatch):
    """bill.qbo_id is None (not yet migrated) -- falls through to the legacy
    qbo.BillBill -> qbo.Bill two-hop, unchanged from pre-U-301b behavior."""
    bill = SimpleNamespace(id=42, qbo_id=None)
    mapping = SimpleNamespace(id=7, bill_id=42, qbo_bill_id=501)
    qbo_bill_by_id = {501: SimpleNamespace(id=501, qbo_id="QBO-LEGACY-42")}
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill, bill_bill_mapping=mapping, qbo_bill_by_id=qbo_bill_by_id
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    worker._refresh_bill(_row())

    assert client.get_bill_calls == ["QBO-LEGACY-42"]
    assert len(connector.calls) == 1
    assert issue_repo.created == []


def test_refresh_bill_fast_path_trusts_dbo_native_identity_when_mapping_dangling(monkeypatch):
    """_verify_dbo_qbo_identity's shared engine (inherited, not introduced by
    this diff — the same KNOWN RESIDUAL the 3 sibling wrappers document) has a
    third branch beyond "no mapping" / "mapping disagrees": a BillBill mapping
    row exists but its QboBillId no longer resolves to anything (or resolves
    to a row with a falsy qbo_id) -- e.g. an orphaned mapping surviving a
    partial pull. This falls through to trusting bill.qbo_id, identically to
    the no-mapping-at-all case, NOT a hard-refuse (only a resolved-and-
    disagreeing external id triggers that)."""
    bill = SimpleNamespace(id=42, qbo_id="QBO-42")
    mapping = SimpleNamespace(id=7, bill_id=42, qbo_bill_id=501)
    # qbo_bill_by_id has no entry for 501 -- read_by_id returns None (dangling FK).
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill, bill_bill_mapping=mapping, qbo_bill_by_id={}
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    worker._refresh_bill(_row())

    assert client.get_bill_calls == ["QBO-42"]
    assert len(connector.calls) == 1
    assert issue_repo.created == []


def test_refresh_bill_no_mapping_and_no_dbo_qbo_id_is_a_noop(monkeypatch):
    """Preserves pre-U-301b behavior: nothing to refresh from, silently returns."""
    bill = SimpleNamespace(id=42, qbo_id=None)
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill, bill_bill_mapping=None
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    worker._refresh_bill(_row())

    assert client.get_bill_calls == []
    assert connector.calls == []
    assert issue_repo.created == []


def test_refresh_bill_hard_refuses_on_genuine_identity_conflict(monkeypatch):
    """bill.qbo_id set AND a BillBill mapping exists whose resolved external
    QboId DISAGREES -- U-301b's Chris-approved design: refuse to refresh
    (never call client.get_bill with disputed identity), record a
    bill_identity_conflict ReconciliationIssue AND raise (never a silent
    return -- silently returning here would let BillBillConnector.sync_to_qbo_bill's
    existing-mapping short-circuit complete the retried push as a no-op
    "done" with the conflict surfaced nowhere but the ReconciliationIssue)."""
    bill, mapping, qbo_bill_by_id = _conflicting_bill_fixture()
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill, bill_bill_mapping=mapping, qbo_bill_by_id=qbo_bill_by_id
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    with pytest.raises(ValueError, match="identity conflict"):
        worker._refresh_bill(_row(entity_public_id="bill-pid-conflict"))

    # Never touches QBO or the local cache with disputed identity.
    assert client.get_bill_calls == []
    assert qbo_bill_service.calls == []
    assert connector.calls == []

    assert len(issue_repo.created) == 1
    issue = issue_repo.created[0]
    assert issue["drift_type"] == "bill_identity_conflict"
    assert issue["entity_type"] == "Bill"
    assert issue["entity_public_id"] == "bill-pid-conflict"
    assert issue["qbo_id"] == "QBO-42-DBO"
    assert "QBO-42-STOLEN" in issue["details"]


def test_refresh_bill_conflict_raise_dead_letters_immediately_via_process_inner(monkeypatch):
    """Closes the gap an adversarial review found: _refresh_bill in isolation
    proves the hard-refuse fires, but only driving it through _process_inner
    (the real caller, via a QboSyncTokenMismatchError from the dispatch-table
    handler) proves the OUTBOX ROW actually ends up dead-lettered rather than
    silently marked done. is_retryable_error walks __cause__/__context__, so this
    also pins that _record_bill_identity_conflict's catch-and-reraise (which
    manually clears the conflict ValueError's __context__ before a bare `raise`)
    is load-bearing: without it, the original QboSyncTokenMismatchError's
    is_retryable=True would leak through __context__ and misclassify this
    ValueError as retryable -- `raise ... from None` alone does NOT clear
    __context__'s value (only __cause__ and the display-only
    __suppress_context__ flag), which is why the plain `from None` form isn't
    enough here."""
    from integrations.intuit.qbo.outbox.business.model import QboOutbox
    from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError

    bill, mapping, qbo_bill_by_id = _conflicting_bill_fixture()
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill, bill_bill_mapping=mapping, qbo_bill_by_id=qbo_bill_by_id
    )

    # First handler(row) call raises the mismatch; the retried call (which
    # _refresh_bill's raise should prevent from ever running) would raise
    # AssertionError if reached, so the test fails loudly if the hard-refuse
    # doesn't actually stop the retry.
    call_count = {"n": 0}

    def fake_handler(row):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise QboSyncTokenMismatchError("stale token", code="610")
        raise AssertionError("handler(row) must not be retried after a hard-refuse")

    dead_lettered = {}

    class _FakeOutboxRepo:
        def mark_dead_letter(self, *, id, row_version, last_error):
            dead_lettered["id"] = id
            dead_lettered["last_error"] = last_error

        def mark_failed(self, **kwargs):
            raise AssertionError("must dead-letter immediately, not schedule a retry")

        def mark_done(self, **kwargs):
            raise AssertionError("must not mark done — the conflict was never resolved")

    worker = QboOutboxWorker(repo=_FakeOutboxRepo())
    worker._dispatch_table["sync_bill_to_qbo"] = fake_handler

    row = QboOutbox(
        id=1,
        public_id="outbox-pid-1",
        row_version="AAAA",
        kind="sync_bill_to_qbo",
        entity_type="Bill",
        entity_public_id="bill-pid-conflict",
        realm_id="realm-1",
        attempts=0,
        correlation_id=None,
        request_id="req-1",
    )

    worker._process_inner(row)

    assert call_count["n"] == 1, "handler(row) must not be retried after a hard-refuse"
    assert dead_lettered.get("id") == 1
    assert len(issue_repo.created) == 1


def test_refresh_bill_returns_early_when_bill_not_found(monkeypatch):
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=None
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    worker._refresh_bill(_row())

    assert client.get_bill_calls == []
    assert connector.calls == []
    assert issue_repo.created == []
