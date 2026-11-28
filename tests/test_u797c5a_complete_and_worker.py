"""U-797c5a round 1: completion enqueue, worker dispatch, refresh, by-id reads. Real BillCredit rows; fakes only at service seams."""

import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import entities.bill_credit.business.complete_service as complete_mod
from entities.bill_credit.business.complete_service import BillCreditCompleteService
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit.persistence import repo as bc_repo_mod
from entities.bill_credit.persistence.repo import BillCreditRepository
from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError, is_retryable_error
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business import service as conn_mod
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import VendorCreditPush
import integrations.intuit.qbo.vendorcredit.external.client as ext_client_mod
from integrations.intuit.qbo.base.errors import QboBudgetExceededError
from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService

REPO_ROOT = Path(__file__).resolve().parents[1]


def _credit(qbo_id=None, **overrides):
    values = dict(
        id=7, public_id="bc-7", row_version="rv", created_datetime=None, modified_datetime=None,
        vendor_id=3, credit_date="2026-11-01", credit_number="VC-100", total_amount=Decimal("30"),
        memo=None, is_draft=True, qbo_id=qbo_id, realm_id="realm-1" if qbo_id else None,
    )
    values.update(overrides)
    return BillCredit(**values)


# ------------------------------------------------------------------ completion

class _Outbox:
    calls = []
    error = None

    def __init__(self):
        pass

    def enqueue(self, **kwargs):
        _Outbox.calls.append(kwargs)
        if _Outbox.error is not None:
            raise _Outbox.error
        return SimpleNamespace(public_id="ob-1")


class _Auth:
    def __init__(self):
        pass

    def read_all(self):
        return [SimpleNamespace(realm_id="realm-1")]


@pytest.fixture
def outbox_and_auth(monkeypatch):
    _Outbox.calls = []
    _Outbox.error = None
    import integrations.intuit.qbo.outbox.business.service as outbox_svc
    import integrations.intuit.qbo.auth.business.service as auth_svc
    monkeypatch.setattr(outbox_svc, "QboOutboxService", _Outbox)
    monkeypatch.setattr(auth_svc, "QboAuthService", _Auth)
    return _Outbox


def _complete_service(monkeypatch, public_read):
    """public_read: what ReadBillCreditByPublicId maps to (selects QboId/RealmId since U-690).
    Any by-id read is a regression: completion no longer re-reads the credit by internal id."""
    svc = object.__new__(BillCreditCompleteService)

    class _BCS:
        def read_by_public_id(self, public_id):
            return public_read

        def read_by_id(self, id):
            raise AssertionError("completion must not re-read by internal id (U-690)")

        def update_by_public_id(self, **kwargs):
            return public_read

    class _Vendors:
        def read_by_id(self, id):
            return SimpleNamespace(id=3, public_id="v-3", name="Acme")

    class _Lines:
        def read_by_bill_credit_id(self, bill_credit_id):
            return []

    svc.bill_credit_service = _BCS()
    svc.vendor_service = _Vendors()
    svc.bill_credit_line_item_service = _Lines()
    svc.project_service = SimpleNamespace(read_by_id=lambda id: None)
    monkeypatch.setattr(BillCreditCompleteService, "_assert_no_holding_code_on_complete", lambda self, _id: None)
    monkeypatch.setattr(BillCreditCompleteService, "_upload_attachments_to_module_folder",
                        lambda self, **kw: {"errors": []})
    monkeypatch.setattr(BillCreditCompleteService, "_enqueue_box_excel", lambda self, **kw: None)
    monkeypatch.setattr(BillCreditCompleteService, "_enqueue_box_uploads", lambda self, **kw: None)
    monkeypatch.setattr(complete_mod.time, "sleep", lambda s: None)
    return svc


def test_completion_enqueues_for_credit_with_no_qbo_identity(monkeypatch, outbox_and_auth):
    svc = _complete_service(monkeypatch, _credit())
    result = svc.complete_bill_credit("bc-7")
    assert outbox_and_auth.calls == [{
        "kind": "sync_bill_credit_to_qbo",
        "entity_type": "BillCredit",
        "entity_public_id": "bc-7",
        "realm_id": "realm-1",
        "conn": None,
    }]
    assert result["qbo_sync"]["qbo_sync_queued"] is True
    assert result["status_code"] == 200


def test_completion_does_not_enqueue_when_public_read_shows_qbo_identity(monkeypatch, outbox_and_auth):
    """P1-3 (U-690): the public-id read selects QboId, so the guard reads it directly (no by-id read)."""
    svc = _complete_service(monkeypatch, public_read=_credit(qbo_id="880"))
    result = svc.complete_bill_credit("bc-7")
    assert outbox_and_auth.calls == []
    assert result["qbo_sync"]["qbo_sync_queued"] is False
    assert result["errors"] == []


def test_enqueue_failure_is_reported_not_raised(monkeypatch, outbox_and_auth):
    outbox_and_auth.error = RuntimeError("outbox down")
    svc = _complete_service(monkeypatch, _credit())
    result = svc.complete_bill_credit("bc-7")
    assert result["bill_credit_finalized"] is True
    assert result["status_code"] == 207
    assert result["errors"] == [{"step": "qbo_enqueue", "error": "outbox down"}]


def test_enqueue_without_qbo_auth_record_is_reported(monkeypatch, outbox_and_auth):
    import integrations.intuit.qbo.auth.business.service as auth_svc

    class _NoAuth:
        def __init__(self):
            pass

        def read_all(self):
            return []

    monkeypatch.setattr(auth_svc, "QboAuthService", _NoAuth)
    svc = _complete_service(monkeypatch, _credit())
    result = svc.complete_bill_credit("bc-7")
    assert outbox_and_auth.calls == []
    assert result["errors"] == [{"step": "qbo_enqueue", "error": "No QBO auth record configured"}]


# ------------------------------------------------------------------ public-id repo mapping (P1-3, U-690)

class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self._current = None

    def set_current(self, name):
        self._current = self._rows.get(name)

    def fetchone(self):
        return self._current


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


def _repo_with_rows(monkeypatch, rows):
    cursor = _Cursor(rows)
    monkeypatch.setattr(bc_repo_mod, "get_connection", lambda: _Conn(cursor))

    def _call(cursor, name, params):
        cursor.set_current(name)

    monkeypatch.setattr(bc_repo_mod, "call_procedure", _call)
    return BillCreditRepository()


def _db_row(**extra):
    base = dict(
        Id=7, PublicId="bc-7", RowVersion=b"\x00\x01", CreatedDatetime=None, ModifiedDatetime=None,
        VendorId=3, CreditDate="2026-11-01", CreditNumber="VC-100", TotalAmount=Decimal("30"),
        Memo=None, IsDraft=False,
    )
    base.update(extra)
    return SimpleNamespace(**base)


def test_public_id_read_maps_qbo_id_and_realm_id(monkeypatch):
    """U-690: ReadBillCreditByPublicId now selects QboId/RealmId, so the public-id read carries identity."""
    rows = {
        "ReadBillCreditByPublicId": _db_row(QboId="880", RealmId="realm-1"),
    }
    repo = _repo_with_rows(monkeypatch, rows)
    public = repo.read_by_public_id("bc-7")
    assert public.qbo_id == "880"
    assert public.realm_id == "realm-1"


# ------------------------------------------------------------------ worker

def _worker(monkeypatch):
    return QboOutboxWorker(repo=object(), api_budget=SimpleNamespace())


def test_dispatch_table_routes_bill_credit_kind(monkeypatch):
    worker = _worker(monkeypatch)
    assert worker._dispatch_table["sync_bill_credit_to_qbo"] == worker._handle_sync_bill_credit


def _handler_connector(calls, *, push_result, attach_exc=None):
    class _Conn:
        def __init__(self, *a, **kw):
            pass

        def sync_to_qbo_vendor_credit(self, bill_credit_id, realm_id):
            calls.append(("push", bill_credit_id, realm_id))
            return push_result

        def sync_attachments_to_qbo_vendor_credit(self, bill_credit_id, qbo_vendor_credit_id, realm_id, line_items):
            calls.append(("attach", bill_credit_id, qbo_vendor_credit_id, realm_id, line_items))
            if attach_exc is not None:
                raise attach_exc
            return 0, []

    return _Conn


def _handler_env(monkeypatch, conn):
    import entities.bill_credit.business.service as bc_service_mod

    class _BCS:
        def __init__(self):
            pass

        def read_by_public_id(self, public_id):
            return _credit()

    monkeypatch.setattr(bc_service_mod, "BillCreditService", _BCS)
    monkeypatch.setattr(conn_mod, "VendorCreditBillCreditConnector", conn)
    return _worker(monkeypatch)


def test_handler_attaches_with_the_lines_the_push_already_read(monkeypatch):
    calls = []
    sentinel_lines = [object()]
    conn = _handler_connector(calls, push_result=VendorCreditPush(qbo_id="880", line_items=sentinel_lines))
    worker = _handler_env(monkeypatch, conn)
    worker._handle_sync_bill_credit(SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1"))
    assert calls == [("push", 7, "realm-1"), ("attach", 7, "880", "realm-1", sentinel_lines)]


def test_handler_skips_attachments_when_the_push_vouches_for_no_qbo_id(monkeypatch):
    calls = []
    conn = _handler_connector(calls, push_result=VendorCreditPush(qbo_id=None, line_items=[]))
    worker = _handler_env(monkeypatch, conn)
    worker._handle_sync_bill_credit(SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1"))
    assert [c[0] for c in calls] == ["push"]


def test_attachment_read_failure_after_create_does_not_fail_the_row(monkeypatch):
    """F2: a transient read error in the post-create attachment step must not fail a row whose credit exists."""
    calls = []
    conn = _handler_connector(
        calls, push_result=VendorCreditPush(qbo_id="880", line_items=[]),
        attach_exc=RuntimeError("transient read error"),
    )
    worker = _handler_env(monkeypatch, conn)
    worker._handle_sync_bill_credit(SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1"))


def test_attachment_budget_failure_parks_the_row(monkeypatch):
    calls = []
    conn = _handler_connector(
        calls, push_result=VendorCreditPush(qbo_id="880", line_items=[]),
        attach_exc=QboBudgetExceededError("budget"),
    )
    worker = _handler_env(monkeypatch, conn)
    with pytest.raises(QboBudgetExceededError):
        worker._handle_sync_bill_credit(SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1"))

def test_bill_credit_handler_raises_when_credit_missing(monkeypatch):
    import entities.bill_credit.business.service as bc_service_mod

    class _BCS:
        def __init__(self):
            pass

        def read_by_public_id(self, public_id):
            return None

    monkeypatch.setattr(bc_service_mod, "BillCreditService", _BCS)
    worker = _worker(monkeypatch)
    with pytest.raises(ValueError, match="BillCredit not found"):
        worker._handle_sync_bill_credit(SimpleNamespace(entity_public_id="bc-x", realm_id="r"))


def test_refresh_dispatches_bill_credit_entity(monkeypatch):
    worker = _worker(monkeypatch)
    seen = []
    monkeypatch.setattr(worker, "_refresh_bill_credit", lambda row: seen.append(row.entity_type))
    worker._refresh_from_qbo(SimpleNamespace(entity_type="BillCredit"))
    assert seen == ["BillCredit"]


def test_refresh_gate_reads_qbo_id_from_the_public_id_read(monkeypatch):
    """P1-3 (U-690): the public-id read carries QboId; the no-op gate uses it with no by-id read."""
    import entities.bill_credit.business.service as bc_service_mod

    class _BCS:
        def __init__(self):
            pass

        def read_by_public_id(self, public_id):
            return _credit(qbo_id="880")

        def read_by_id(self, id):
            raise AssertionError("refresh must not re-read by internal id (U-690)")

        def read_by_qbo_identity(self, qbo_id, realm_id=None):
            return _credit(qbo_id="880") if qbo_id == "880" else None

    class _Client:
        def __init__(self, *, realm_id, **kw):
            self.realm_id = realm_id

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_vendor_credit(self, vendor_credit_id):
            return SimpleNamespace(id=vendor_credit_id)

    projected = []
    monkeypatch.setattr(bc_service_mod, "BillCreditService", _BCS)
    monkeypatch.setattr(ext_client_mod, "QboVendorCreditClient", _Client)
    monkeypatch.setattr(QboVendorCreditService, "upsert_from_external",
                        lambda self, qbo_vc, realm_id: (SimpleNamespace(id=500), []))

    class _Conn:
        def __init__(self, *a, **kw):
            pass

        def sync_from_qbo_vendor_credit(self, qbo_vc, qbo_lines):
            projected.append(qbo_vc)

    monkeypatch.setattr(conn_mod, "VendorCreditBillCreditConnector", _Conn)
    worker = _worker(monkeypatch)
    worker._refresh_bill_credit(SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1"))
    assert len(projected) == 1


def test_refresh_without_qbo_identity_is_a_noop(monkeypatch):
    import entities.bill_credit.business.service as bc_service_mod

    class _BCS:
        def __init__(self):
            pass

        def read_by_public_id(self, public_id):
            return _credit(qbo_id=None)

        def read_by_id(self, id):
            return _credit(qbo_id=None)

    class _NoCall:
        def __init__(self, *a, **kw):
            raise AssertionError("no QBO client may be built for a credit never pushed")

    monkeypatch.setattr(bc_service_mod, "BillCreditService", _BCS)
    monkeypatch.setattr(ext_client_mod, "QboVendorCreditClient", _NoCall)
    worker = _worker(monkeypatch)
    worker._refresh_bill_credit(SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1"))


def test_refresh_identity_conflict_is_not_retryable_after_sync_token_mismatch(monkeypatch):
    """P2-4: the conflict raised inside the SyncToken except block must not carry the retryable context."""
    import entities.bill_credit.business.service as bc_service_mod
    import integrations.intuit.qbo.base.reconciliation_recorder as recorder
    import integrations.intuit.qbo.reconciliation.persistence.repo as recon_repo

    class _BCS:
        def __init__(self):
            pass

        def read_by_public_id(self, public_id):
            return _credit(qbo_id="880")

        def read_by_id(self, id):
            return _credit(qbo_id="880")

        def read_by_qbo_identity(self, qbo_id, realm_id=None):
            return None  # identity no longer resolves back to this credit

    recorded = []
    monkeypatch.setattr(bc_service_mod, "BillCreditService", _BCS)
    monkeypatch.setattr(recorder, "record_mapping_issue", lambda repo, **kw: recorded.append(kw))
    monkeypatch.setattr(recon_repo, "ReconciliationIssueRepository", lambda: object())
    worker = _worker(monkeypatch)
    row = SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1", public_id="ob-1")

    with pytest.raises(ValueError) as excinfo:
        try:
            raise QboSyncTokenMismatchError("stale", request_path="/vendorcredit", request_method="POST")
        except QboSyncTokenMismatchError:
            worker._refresh_bill_credit(row)
    assert recorded and recorded[0]["drift_type"] == "bill_credit_identity_conflict"
    assert not is_retryable_error(excinfo.value)


def test_known_kinds_lists_the_new_kind_and_keeps_existing_ones():
    source = (REPO_ROOT / "scripts" / "retry_qbo_outbox_dead_letters.py").read_text()
    block = re.search(r"KNOWN_KINDS = frozenset\(\{(.*?)\}\)", source, re.S).group(1)
    kinds = set(re.findall(r'"([a-z_]+)"', block))
    assert kinds == {"sync_bill_to_qbo", "sync_invoice_to_qbo", "recode_purchase_line", "sync_bill_credit_to_qbo"}


def test_attachment_write_refusal_parks_the_row(monkeypatch):
    # Mutation M10 (EM pin): a QBO write refusal in the attachment step must propagate (park), not be logged as done.
    from integrations.intuit.qbo.base.errors import QboWriteRefusedError

    calls = []
    conn = _handler_connector(
        calls, push_result=VendorCreditPush(qbo_id="880", line_items=[]),
        attach_exc=QboWriteRefusedError("ALLOW_QBO_WRITES off"),
    )
    worker = _handler_env(monkeypatch, conn)
    with pytest.raises(QboWriteRefusedError):
        worker._handle_sync_bill_credit(SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1"))
