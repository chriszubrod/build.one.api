"""U-778a bite 1: the three QBO line repos persist ReimburseChargeQboId.

Fakes replace get_connection / call_procedure in each repo module; the assertions are on the exact
named params handed to the sproc, so a default call that would CLEAR the stored link (NULL + flag 1)
differs from one that preserves it (NULL + flag 0).
"""
from __future__ import annotations

import base64
from decimal import Decimal
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.bill.persistence import repo as bill_repo
from integrations.intuit.qbo.purchase.persistence import repo as purchase_repo
from integrations.intuit.qbo.vendorcredit.persistence import repo as vc_repo
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCreditLine


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cursor


@pytest.fixture
def calls(monkeypatch):
    """Install fakes into all three repo modules; returns (calls list, set_rows fn)."""
    state = {"rows": [], "calls": []}

    def fake_call(*args, **kwargs):
        if args:
            cursor, name, params = (list(args) + [None, None])[:3]
            params = params if params is not None else kwargs.get("params")
        else:
            name, params = kwargs["name"], kwargs["params"]
        state["calls"].append((name, params))

    for mod in (bill_repo, purchase_repo, vc_repo):
        monkeypatch.setattr(mod, "call_procedure", fake_call)
        monkeypatch.setattr(mod, "get_connection", lambda: FakeConn(FakeCursor(state["rows"])))
    return state


def _row(parent_col, charge, line_id="L1"):
    return SimpleNamespace(
        Id=1, PublicId="11111111-1111-1111-1111-111111111111", RowVersion=b"\x00" * 8,
        CreatedDatetime=None, ModifiedDatetime=None, QboLineId=line_id, LineNum=1,
        Description="d", Amount=Decimal("10.00"), DetailType="X", ItemRefValue=None,
        ItemRefName=None, AccountRefValue=None, AccountRefName=None, CustomerRefValue=None,
        CustomerRefName=None, ClassRefValue=None, ClassRefName=None, BillableStatus="Billable",
        Qty=None, UnitPrice=None, MarkupPercent=None, ReimburseChargeQboId=charge,
        **{parent_col: 9},
    )


_COMMON = dict(
    line_num=1, description="d", amount=Decimal("10.00"), detail_type="X",
    item_ref_value=None, item_ref_name=None, account_ref_value=None, account_ref_name=None,
    customer_ref_value=None, customer_ref_name=None, class_ref_value=None, class_ref_name=None,
    billable_status="Billable", qty=None, unit_price=None, markup_percent=None,
)

# (repo class, parent kwarg for create, parent column, create-proc, update-proc, narrow-proc)
HEADER_LINE_REPOS = [
    pytest.param(bill_repo.QboBillLineRepository, "qbo_bill_id", "QboBillId",
                 "CreateQboBillLine", "UpdateQboBillLineById",
                 "UpdateQboBillLineReimburseChargeById", id="bill"),
    pytest.param(purchase_repo.QboPurchaseLineRepository, "qbo_purchase_id", "QboPurchaseId",
                 "CreateQboPurchaseLine", "UpdateQboPurchaseLineById",
                 "UpdateQboPurchaseLineReimburseChargeById", id="purchase"),
]


@pytest.mark.parametrize("cls,parent_kw,parent_col,create_proc,update_proc,narrow_proc", HEADER_LINE_REPOS)
class TestBillAndPurchaseLineRepos:
    def test_create_passes_charge_id(self, calls, cls, parent_kw, parent_col, create_proc, update_proc, narrow_proc):
        calls["rows"].append(_row(parent_col, "68056"))
        line = cls().create(**{parent_kw: 9}, qbo_line_id="L1", reimburse_charge_qbo_id="68056", **_COMMON)
        name, params = calls["calls"][-1]
        assert name == create_proc
        assert params["ReimburseChargeQboId"] == "68056"
        assert line.reimburse_charge_qbo_id == "68056"

    def test_create_default_is_null(self, calls, cls, parent_kw, parent_col, create_proc, update_proc, narrow_proc):
        calls["rows"].append(_row(parent_col, None))
        cls().create(**{parent_kw: 9}, qbo_line_id="L1", **_COMMON)
        _, params = calls["calls"][-1]
        assert "ReimburseChargeQboId" in params and params["ReimburseChargeQboId"] is None

    def test_update_default_preserves(self, calls, cls, parent_kw, parent_col, create_proc, update_proc, narrow_proc):
        calls["rows"].append(_row(parent_col, "68056"))
        cls().update_by_id(id=1, row_version=b"\x00" * 8, **_COMMON)
        name, params = calls["calls"][-1]
        assert name == update_proc
        assert params["ReimburseChargeQboId"] is None
        assert params["SetReimburseChargeQboId"] == 0

    def test_update_set_true_with_value(self, calls, cls, parent_kw, parent_col, create_proc, update_proc, narrow_proc):
        calls["rows"].append(_row(parent_col, "777"))
        out = cls().update_by_id(
            id=1, row_version=b"\x00" * 8, reimburse_charge_qbo_id="777",
            set_reimburse_charge_qbo_id=True, **_COMMON,
        )
        _, params = calls["calls"][-1]
        assert params["ReimburseChargeQboId"] == "777"
        assert params["SetReimburseChargeQboId"] == 1
        assert out.reimburse_charge_qbo_id == "777"

    def test_update_set_true_with_none_clears(self, calls, cls, parent_kw, parent_col, create_proc, update_proc, narrow_proc):
        calls["rows"].append(_row(parent_col, None))
        cls().update_by_id(
            id=1, row_version=b"\x00" * 8, reimburse_charge_qbo_id=None,
            set_reimburse_charge_qbo_id=True, **_COMMON,
        )
        _, params = calls["calls"][-1]
        assert params["ReimburseChargeQboId"] is None
        assert params["SetReimburseChargeQboId"] == 1

    def test_from_db_maps_distinct_ids(self, calls, cls, parent_kw, parent_col, create_proc, update_proc, narrow_proc):
        calls["rows"].extend([_row(parent_col, "A1", "L1"), _row(parent_col, "B2", "L2")])
        r = cls()
        first, second = r._from_db(calls["rows"][0]), r._from_db(calls["rows"][1])
        assert (first.reimburse_charge_qbo_id, second.reimburse_charge_qbo_id) == ("A1", "B2")

    def test_read_returns_charge_id(self, calls, cls, parent_kw, parent_col, create_proc, update_proc, narrow_proc):
        calls["rows"].extend([_row(parent_col, "A1", "L1"), _row(parent_col, None, "L2")])
        name = "read_by_qbo_bill_id" if "Bill" in parent_col else "read_by_qbo_purchase_id"
        lines = getattr(cls(), name)(9)
        assert [l.reimburse_charge_qbo_id for l in lines] == ["A1", None]

    def test_narrow_update_true_on_row(self, calls, cls, parent_kw, parent_col, create_proc, update_proc, narrow_proc):
        calls["rows"].append(SimpleNamespace(Id=1, ReimburseChargeQboId="68056"))
        assert cls().update_reimburse_charge_by_id(1, "68056") is True
        name, params = calls["calls"][-1]
        assert name == narrow_proc
        assert params == {"Id": 1, "ReimburseChargeQboId": "68056"}

    def test_narrow_update_false_on_no_row(self, calls, cls, parent_kw, parent_col, create_proc, update_proc, narrow_proc):
        assert cls().update_reimburse_charge_by_id(1, "68056") is False


# ---- VendorCredit (object-shaped line API) ----

def _vc_line(charge, line_id="L1"):
    return QboVendorCreditLine(
        id=5, public_id=None, row_version=None, created_datetime=None, modified_datetime=None,
        qbo_vendor_credit_id=9, qbo_line_id=line_id, line_num=1, description="d",
        amount=Decimal("10.00"), detail_type="X", item_ref_value=None, item_ref_name=None,
        class_ref_value=None, class_ref_name=None, unit_price=None, qty=None,
        billable_status="Billable", customer_ref_value=None, customer_ref_name=None,
        account_ref_value=None, account_ref_name=None, reimburse_charge_qbo_id=charge,
    )


class TestVendorCreditLineRepo:
    def test_create_line_passes_charge_id(self, calls):
        calls["rows"].append(_row("QboVendorCreditId", "68056"))
        out = vc_repo.QboVendorCreditRepository().create_line(_vc_line("68056"))
        name, params = calls["calls"][-1]
        assert name == "CreateQboVendorCreditLine"
        assert params["ReimburseChargeQboId"] == "68056"
        assert out.reimburse_charge_qbo_id == "68056"

    def test_create_line_none_is_null(self, calls):
        calls["rows"].append(_row("QboVendorCreditId", None))
        vc_repo.QboVendorCreditRepository().create_line(_vc_line(None))
        assert calls["calls"][-1][1]["ReimburseChargeQboId"] is None

    def test_update_line_default_preserves(self, calls):
        calls["rows"].append(_row("QboVendorCreditId", "68056"))
        # the line carries a value, but without the flag the sproc must be told to preserve
        vc_repo.QboVendorCreditRepository().update_line_by_id(_vc_line("68056"))
        _, params = calls["calls"][-1]
        assert params["SetReimburseChargeQboId"] == 0

    def test_update_line_set_true_writes_value(self, calls):
        calls["rows"].append(_row("QboVendorCreditId", "777"))
        vc_repo.QboVendorCreditRepository().update_line_by_id(
            _vc_line("777"), set_reimburse_charge_qbo_id=True)
        name, params = calls["calls"][-1]
        assert name == "UpdateQboVendorCreditLineById"
        assert params["ReimburseChargeQboId"] == "777"
        assert params["SetReimburseChargeQboId"] == 1

    def test_update_line_set_true_none_clears(self, calls):
        calls["rows"].append(_row("QboVendorCreditId", None))
        vc_repo.QboVendorCreditRepository().update_line_by_id(
            _vc_line(None), set_reimburse_charge_qbo_id=True)
        _, params = calls["calls"][-1]
        assert params["ReimburseChargeQboId"] is None
        assert params["SetReimburseChargeQboId"] == 1

    def test_line_from_db_maps_distinct_ids(self, calls):
        r = vc_repo.QboVendorCreditRepository()
        a = r._line_from_db(_row("QboVendorCreditId", "A1", "L1"))
        b = r._line_from_db(_row("QboVendorCreditId", "B2", "L2"))
        assert (a.reimburse_charge_qbo_id, b.reimburse_charge_qbo_id) == ("A1", "B2")

    def test_read_lines_returns_charge_id(self, calls):
        calls["rows"].extend([_row("QboVendorCreditId", "A1", "L1"), _row("QboVendorCreditId", None, "L2")])
        lines = vc_repo.QboVendorCreditRepository().read_lines_by_vendor_credit_id(9)
        assert [l.reimburse_charge_qbo_id for l in lines] == ["A1", None]

    def test_narrow_update_true_on_row(self, calls):
        calls["rows"].append(SimpleNamespace(Id=1, ReimburseChargeQboId="68056"))
        assert vc_repo.QboVendorCreditRepository().update_line_reimburse_charge_by_id(1, "68056") is True
        name, params = calls["calls"][-1]
        assert name == "UpdateQboVendorCreditLineReimburseChargeById"
        assert params == {"Id": 1, "ReimburseChargeQboId": "68056"}

    def test_narrow_update_false_on_no_row(self, calls):
        assert vc_repo.QboVendorCreditRepository().update_line_reimburse_charge_by_id(1, "68056") is False
