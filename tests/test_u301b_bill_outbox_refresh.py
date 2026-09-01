"""U-301b/U-355: outbox worker's Bill refresh-on-SyncToken-mismatch.

Originally repointed (U-301b) onto dbo.Bill's own native QboId (U-238a) via a
fast path / legacy qbo.BillBill fallback / hard-refuse-on-conflict split. U-355
retired qbo.BillBill entirely, so `_refresh_bill` is now dbo-only:
`verify_identity_dbo_only` (base/identity_consistency.py) replaces the retired
`verify_bill_qbo_identity` wrapper, and there is no legacy two-hop left to fall
back to. These tests were rewritten for the dbo-only shape; the original file
(918 "done" sync_bill_to_qbo outbox rows in prod as of U-301b) still applies —
no prior coverage existed for _refresh_bill before that unit.
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
    """`read_by_public_id` returns the fixture bill; `read_by_qbo_identity`
    reproduces verify_identity_dbo_only's own direct-read contract: return a
    row whose `.id` either matches (verify succeeds), differs (a reassigned/
    stolen identity), or is None (the identity resolves to nothing anymore)."""

    def __init__(self, bill, *, fresh_by_identity=None):
        self._bill = bill
        self._fresh_by_identity = fresh_by_identity

    def read_by_public_id(self, public_id):
        return self._bill

    def read_by_qbo_identity(self, qbo_id, realm_id):
        return self._fresh_by_identity


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
    fresh_by_identity=None,
    client=None,
    issue_repo=None,
):
    monkeypatch.setattr(
        "entities.bill.business.service.BillService",
        lambda: _FakeBillService(bill, fresh_by_identity=fresh_by_identity),
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


def test_refresh_bill_proceeds_when_dbo_identity_verifies(monkeypatch):
    """bill.qbo_id set AND a fresh dbo-only re-read resolves back to this same
    Bill -- verify_identity_dbo_only trusts it, no mapping table involved."""
    bill = SimpleNamespace(id=42, qbo_id="QBO-42", realm_id="realm-1")
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill, fresh_by_identity=SimpleNamespace(id=42),
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    worker._refresh_bill(_row())

    assert client.get_bill_calls == ["QBO-42"]
    assert len(connector.calls) == 1
    assert issue_repo.created == []


def test_refresh_bill_no_qbo_id_is_a_noop(monkeypatch):
    """bill.qbo_id is None (never pushed) -- nothing to refresh from, silently
    returns. No legacy mapping-table hop left to fall back to (U-355)."""
    bill = SimpleNamespace(id=42, qbo_id=None, realm_id="realm-1")
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill,
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    worker._refresh_bill(_row())

    assert client.get_bill_calls == []
    assert connector.calls == []
    assert issue_repo.created == []


def test_refresh_bill_returns_early_when_bill_not_found(monkeypatch):
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=None,
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    worker._refresh_bill(_row())

    assert client.get_bill_calls == []
    assert connector.calls == []
    assert issue_repo.created == []


@pytest.mark.parametrize(
    "label,fresh_by_identity",
    [
        ("reassigned: fresh read resolves to a DIFFERENT Bill id", SimpleNamespace(id=99)),
        ("vanished: fresh read finds no row at all", None),
    ],
    ids=["reassigned", "vanished"],
)
def test_refresh_bill_hard_refuses_on_genuine_identity_conflict(
    monkeypatch, label, fresh_by_identity
):
    """bill.qbo_id set but a fresh dbo-only re-read no longer resolves back to
    this same Bill -- U-355's hard-refuse: never call client.get_bill with
    disputed identity, record a bill_identity_conflict ReconciliationIssue AND
    raise (never a silent return -- silently returning here would let
    BillBillConnector.sync_to_qbo_bill's already-pushed short-circuit complete
    the retried push as a no-op "done" with the conflict surfaced nowhere but
    the ReconciliationIssue)."""
    bill = SimpleNamespace(id=42, qbo_id="QBO-42-DBO", realm_id="realm-1")
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill, fresh_by_identity=fresh_by_identity,
    )

    worker = QboOutboxWorker(repo=SimpleNamespace())
    with pytest.raises(ValueError, match="identity conflict"):
        worker._refresh_bill(_row(entity_public_id="bill-pid-conflict"))

    # Never touches QBO or the local cache with disputed identity.
    assert client.get_bill_calls == [], label
    assert qbo_bill_service.calls == [], label
    assert connector.calls == [], label

    assert len(issue_repo.created) == 1, label
    issue = issue_repo.created[0]
    assert issue["drift_type"] == "bill_identity_conflict"
    assert issue["entity_type"] == "Bill"
    assert issue["entity_public_id"] == "bill-pid-conflict"
    assert issue["qbo_id"] == "QBO-42-DBO"


def test_refresh_bill_conflict_raise_dead_letters_immediately_via_process_inner(monkeypatch):
    """Closes the gap an adversarial review found (U-301b): _refresh_bill in
    isolation proves the hard-refuse fires, but only driving it through
    _process_inner (the real caller, via a QboSyncTokenMismatchError from the
    dispatch-table handler) proves the OUTBOX ROW actually ends up
    dead-lettered rather than silently marked done. is_retryable_error walks
    __cause__/__context__, so this also pins that
    _record_bill_identity_conflict's catch-and-reraise (which manually clears
    the conflict ValueError's __context__ before a bare `raise`) is
    load-bearing: without it, the original QboSyncTokenMismatchError's
    is_retryable=True would leak through __context__ and misclassify this
    ValueError as retryable -- `raise ... from None` alone does NOT clear
    __context__'s value (only __cause__ and the display-only
    __suppress_context__ flag), which is why the plain `from None` form isn't
    enough here."""
    from integrations.intuit.qbo.outbox.business.model import QboOutbox
    from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError

    bill = SimpleNamespace(id=42, qbo_id="QBO-42-DBO", realm_id="realm-1")
    client, qbo_bill_service, connector, issue_repo = _patch_bill_refresh_stack(
        monkeypatch, bill=bill, fresh_by_identity=SimpleNamespace(id=99),
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
