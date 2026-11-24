"""U-690: the BillCredit / BillCreditLineItem public-id and by-list reads select QboId/RealmId.

Before U-690 the public-id read and the per-parent line list read did not select QboId/RealmId,
so completion and the credit refresh re-read every row by internal id to see identity. These tests
pin the SQL contract, the repo mapping, the direct completion path, the stale-orphan matcher's
view of live lines, and the worker's single public-id read. Fakes only; no database.
"""

import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import entities.bill_credit.business.complete_service as complete_mod
from entities.bill_credit.business.complete_service import BillCreditCompleteService
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit.persistence import repo as bc_repo_mod
from entities.bill_credit.persistence.repo import BillCreditRepository
from entities.bill_credit_line_item.persistence import repo as bcli_repo_mod
from entities.bill_credit_line_item.persistence.repo import BillCreditLineItemRepository
from integrations.intuit.qbo.base.line_orphan_adopt import find_stale_identity_orphan
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business import service as conn_mod
import integrations.intuit.qbo.vendorcredit.external.client as ext_client_mod
from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService

REPO_ROOT = Path(__file__).resolve().parents[1]
BC_SQL = REPO_ROOT / "entities" / "bill_credit" / "sql" / "dbo.bill_credit.sql"
BCLI_SQL = REPO_ROOT / "entities" / "bill_credit_line_item" / "sql" / "dbo.bill_credit_line_item.sql"


# ------------------------------------------------------------------ SQL contract

def _proc_select_columns(sql_path: Path, proc_name: str) -> str:
    """Return the SELECT column list of one proc, scoped to its own GO-delimited batch."""
    text = sql_path.read_text(encoding="utf-8")
    batches = re.split(r"^\s*GO\s*$", text, flags=re.MULTILINE)
    matches = [b for b in batches if re.search(rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+{proc_name}\b", b)]
    assert len(matches) == 1, f"expected exactly one batch for {proc_name}, found {len(matches)}"
    body = matches[0]
    select = re.search(r"SELECT(.*?)FROM\s+dbo\.", body, flags=re.DOTALL)
    assert select, f"no SELECT ... FROM in {proc_name}"
    return select.group(1)


def test_read_bill_credit_by_public_id_selects_qbo_identity():
    columns = _proc_select_columns(BC_SQL, "ReadBillCreditByPublicId")
    assert "[QboId]" in columns
    assert "[RealmId]" in columns


def test_read_bill_credit_line_items_by_bill_credit_id_selects_qbo_identity():
    columns = _proc_select_columns(BCLI_SQL, "ReadBillCreditLineItemsByBillCreditId")
    assert "[QboId]" in columns
    assert "[RealmId]" in columns


# ------------------------------------------------------------------ repo mapping

class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self._current = None

    def set_current(self, name):
        self._current = self._rows.get(name)

    def fetchone(self):
        return self._current

    def fetchall(self):
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


def _patch_procs(monkeypatch, module, rows):
    cursor = _Cursor(rows)
    monkeypatch.setattr(module, "get_connection", lambda: _Conn(cursor))
    monkeypatch.setattr(module, "call_procedure", lambda cursor, name, params: cursor.set_current(name))


def _bc_row(**extra):
    base = dict(
        Id=7, PublicId="bc-7", RowVersion=b"\x00\x01", CreatedDatetime=None, ModifiedDatetime=None,
        VendorId=3, CreditDate="2026-11-01", CreditNumber="VC-100", TotalAmount=Decimal("30"),
        Memo=None, IsDraft=True,
    )
    base.update(extra)
    return SimpleNamespace(**base)


def test_bill_credit_public_id_read_maps_qbo_id_and_realm_id(monkeypatch):
    _patch_procs(monkeypatch, bc_repo_mod, {
        "ReadBillCreditByPublicId": _bc_row(QboId="880", RealmId="realm-1"),
    })
    credit = BillCreditRepository().read_by_public_id("bc-7")
    assert credit.qbo_id == "880"
    assert credit.realm_id == "realm-1"


def _bcli_row(**extra):
    base = dict(
        Id=1, PublicId="bcli-1", RowVersion=b"\x00\x01", CreatedDatetime=None, ModifiedDatetime=None,
        BillCreditId=7, SubCostCodeId=10, ProjectId=20, Description="Fuel", Quantity=Decimal("1"),
        UnitPrice=Decimal("30"), Amount=Decimal("30"), IsBillable=False, IsBilled=False,
        BillableAmount=None, IsDraft=True,
    )
    base.update(extra)
    return SimpleNamespace(**base)


def test_bill_credit_line_list_read_maps_qbo_id_and_realm_id(monkeypatch):
    _patch_procs(monkeypatch, bcli_repo_mod, {
        "ReadBillCreditLineItemsByBillCreditId": [
            _bcli_row(Id=1, QboId="10", RealmId="realm-1"),
            _bcli_row(Id=2, QboId="11", RealmId="realm-1"),
        ],
    })
    lines = BillCreditLineItemRepository().read_by_bill_credit_id(7)
    assert [(line.id, line.qbo_id, line.realm_id) for line in lines] == [
        (1, "10", "realm-1"),
        (2, "11", "realm-1"),
    ]


# ------------------------------------------------------------------ stale-orphan matcher sees live siblings

def _fp(line):
    return (line.description, line.amount, line.quantity, line.unit_price)


def _line(id, qbo_id):
    return SimpleNamespace(id=id, qbo_id=qbo_id, description="Fuel", amount=Decimal("30"),
                           quantity=Decimal("1"), unit_price=Decimal("30"))


def test_live_sibling_with_identical_fingerprint_is_not_stolen():
    """A line still bound to a live QBO line id is never re-adopted, even with identical content."""
    existing = [_line(1, "10"), _line(2, "11")]  # both carry qbo_ids now that the list read selects them
    picked = find_stale_identity_orphan(
        existing_lines=existing,
        live_qbo_line_ids=frozenset({"10", "11"}),
        fingerprint=_fp,
        target=_fp(_line(0, None)),
    )
    assert picked is None


def test_line_whose_qbo_id_is_no_longer_live_is_adopted():
    existing = [_line(1, "10"), _line(2, "99")]
    picked = find_stale_identity_orphan(
        existing_lines=existing,
        live_qbo_line_ids=frozenset({"10"}),
        fingerprint=_fp,
        target=_fp(_line(0, None)),
    )
    assert picked is not None and picked.id == 2


# ------------------------------------------------------------------ completion: direct path, no by-id reads

def _credit(qbo_id=None, **overrides):
    values = dict(
        id=7, public_id="bc-7", row_version="rv", created_datetime=None, modified_datetime=None,
        vendor_id=3, credit_date="2026-11-01", credit_number="VC-100", total_amount=Decimal("30"),
        memo=None, is_draft=True, qbo_id=qbo_id, realm_id="realm-1" if qbo_id else None,
    )
    values.update(overrides)
    return BillCredit(**values)


class _NoByIdRead:
    """Any by-id read is a regression: U-690 removed them all from the completion path."""

    def read_by_id(self, id):
        raise AssertionError("completion must not re-read by internal id (U-690)")


def _complete_service(monkeypatch, public_read, line_items):
    svc = object.__new__(BillCreditCompleteService)

    class _BCS(_NoByIdRead):
        def read_by_public_id(self, public_id):
            return public_read

        def update_by_public_id(self, **kwargs):
            return public_read

    class _Vendors:
        def read_by_id(self, id):
            return SimpleNamespace(id=3, public_id="v-3", name="Acme")

    class _Lines(_NoByIdRead):
        def read_by_bill_credit_id(self, bill_credit_id):
            return line_items

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


def test_completion_with_qbo_id_on_public_read_is_not_enqueued(monkeypatch):
    enqueue = MagicMock(return_value={"success": True, "qbo_sync_queued": True, "errors": []})
    monkeypatch.setattr("shared.completion.qbo_enqueue.enqueue_qbo_push", enqueue)
    svc = _complete_service(monkeypatch, public_read=_credit(qbo_id="880"), line_items=[])
    result = svc.complete_bill_credit("bc-7")
    enqueue.assert_not_called()
    assert result["qbo_sync"]["qbo_sync_queued"] is False
    assert result["status_code"] == 200


def test_completion_without_qbo_id_on_public_read_is_enqueued(monkeypatch):
    enqueue = MagicMock(return_value={"success": True, "qbo_sync_queued": True, "errors": []})
    monkeypatch.setattr("shared.completion.qbo_enqueue.enqueue_qbo_push", enqueue)
    svc = _complete_service(monkeypatch, public_read=_credit(), line_items=[])
    result = svc.complete_bill_credit("bc-7")
    enqueue.assert_called_once()
    assert result["qbo_sync"]["qbo_sync_queued"] is True


def test_drift_check_receives_the_passed_line_items_without_rereads(monkeypatch):
    line_items = [SimpleNamespace(id=1, qbo_id="5", sub_cost_code_id=10, project_id=20, description="x",
                                  is_draft=False, public_id="bcli-1")]
    record = MagicMock()
    monkeypatch.setattr("shared.completion.qbo_coding_drift.record_coding_drift_for", record)
    monkeypatch.setattr("integrations.intuit.qbo.vendorcredit.persistence.repo.QboVendorCreditRepository",
                        MagicMock())
    svc = _complete_service(monkeypatch, public_read=_credit(qbo_id="880"), line_items=line_items)
    svc.complete_bill_credit("bc-7")
    record.assert_called_once()
    kwargs = record.call_args.kwargs
    assert kwargs["entity_type"] == "BillCredit"
    assert kwargs["doc"].qbo_id == "880"
    assert kwargs["local_lines"] is line_items


# ------------------------------------------------------------------ worker: public-id read only

def _worker():
    return QboOutboxWorker(repo=object(), api_budget=SimpleNamespace())


class _Client:
    def __init__(self, *, realm_id, **kw):
        self.realm_id = realm_id

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_vendor_credit(self, vendor_credit_id):
        return SimpleNamespace(id=vendor_credit_id)


def test_refresh_uses_only_the_public_id_read_and_refreshes(monkeypatch):
    import entities.bill_credit.business.service as bc_service_mod

    class _BCS(_NoByIdRead):
        def read_by_public_id(self, public_id):
            return _credit(qbo_id="880")

        def read_by_qbo_identity(self, qbo_id, realm_id=None):
            return _credit(qbo_id="880") if qbo_id == "880" else None

    projected = []

    class _Conn:
        def __init__(self, *a, **kw):
            pass

        def sync_from_qbo_vendor_credit(self, qbo_vc, qbo_lines):
            projected.append(qbo_vc)

    monkeypatch.setattr(bc_service_mod, "BillCreditService", _BCS)
    monkeypatch.setattr(ext_client_mod, "QboVendorCreditClient", _Client)
    monkeypatch.setattr(QboVendorCreditService, "upsert_from_external",
                        lambda self, qbo_vc, realm_id: (SimpleNamespace(id=500), []))
    monkeypatch.setattr(conn_mod, "VendorCreditBillCreditConnector", _Conn)
    _worker()._refresh_bill_credit(SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1"))
    assert len(projected) == 1


def test_refresh_without_qbo_id_on_public_read_returns_without_refreshing(monkeypatch):
    import entities.bill_credit.business.service as bc_service_mod

    class _BCS(_NoByIdRead):
        def read_by_public_id(self, public_id):
            return _credit(qbo_id=None)

    class _NoClient:
        def __init__(self, *a, **kw):
            raise AssertionError("no QboId means nothing to refresh")

    monkeypatch.setattr(bc_service_mod, "BillCreditService", _BCS)
    monkeypatch.setattr(ext_client_mod, "QboVendorCreditClient", _NoClient)
    _worker()._refresh_bill_credit(SimpleNamespace(entity_public_id="bc-7", realm_id="realm-1"))
