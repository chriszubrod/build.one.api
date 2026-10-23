"""U-721 holding rule on the Box DETAILS row builder (Chris, 2026-10-23).

"We never sync a line item that subcostcode=00.02" — the Box row builder held
holding lines back on the expense branch only; bill and bill-credit rows were
built for every line (reachable via the invoice push's Box enqueue).
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

HOLDING_ID = 900
NORMAL_ID = 253
NUMBERS = {HOLDING_ID: "00.02", NORMAL_ID: "32.4"}


def _scc_service(flagged: bool):
    return SimpleNamespace(
        read_by_id=lambda **kw: SimpleNamespace(number=NUMBERS[int(kw["id"])]),
        is_holding_code=lambda _id: flagged and _id == HOLDING_ID,
    )


def _lines():
    return [
        SimpleNamespace(
            id=1, public_id="li-normal", price=Decimal("10.00"), amount=Decimal("10.00"),
            description="d", sub_cost_code_id=NORMAL_ID, project_id=93,
        ),
        SimpleNamespace(
            id=2, public_id="li-held", price=Decimal("20.00"), amount=Decimal("20.00"),
            description="d", sub_cost_code_id=HOLDING_ID, project_id=93,
        ),
    ]


def _stub_common(monkeypatch, flagged):
    import entities.sub_cost_code.business.service as scc_service
    import entities.vendor.business.service as vendor_service

    monkeypatch.setattr(
        vendor_service, "VendorService",
        lambda: SimpleNamespace(read_by_id=lambda **kw: SimpleNamespace(name="V")),
    )
    monkeypatch.setattr(scc_service, "SubCostCodeService", lambda: _scc_service(flagged))


def _build_bill_rows(monkeypatch, flagged):
    import entities.bill.business.service as bill_service
    import entities.bill_line_item.business.service as bli_service
    import integrations.box.excel.business.row_builder as rb

    _stub_common(monkeypatch, flagged)
    bill = SimpleNamespace(
        id=1, public_id="b-pub", vendor_id=7, bill_date="2026-10-01", bill_number="B1",
    )
    monkeypatch.setattr(
        bill_service, "BillService",
        lambda: SimpleNamespace(read_by_public_id=lambda **kw: bill),
    )
    monkeypatch.setattr(
        bli_service, "BillLineItemService",
        lambda: SimpleNamespace(read_by_bill_id=lambda **kw: _lines()),
    )
    return rb.build_details_rows("bill", "b-pub", project_id=93)


def _build_credit_rows(monkeypatch, flagged):
    import entities.bill_credit.business.service as bc_service
    import entities.bill_credit_line_item.business.service as bcli_service
    import integrations.box.excel.business.row_builder as rb

    _stub_common(monkeypatch, flagged)
    credit = SimpleNamespace(
        id=1, public_id="c-pub", vendor_id=7, credit_date="2026-10-01", credit_number="C1",
    )
    monkeypatch.setattr(
        bc_service, "BillCreditService",
        lambda: SimpleNamespace(read_by_public_id=lambda **kw: credit),
    )
    monkeypatch.setattr(
        bcli_service, "BillCreditLineItemService",
        lambda: SimpleNamespace(read_by_bill_credit_id=lambda **kw: _lines()),
    )
    return rb.build_details_rows("bill_credit", "c-pub", project_id=93)


@pytest.mark.parametrize("build", [_build_bill_rows, _build_credit_rows])
@pytest.mark.parametrize("flagged", [True, False])
def test_box_rows_never_include_a_00_02_line(monkeypatch, build, flagged):
    """Held by the holding flag (flagged=True) and by the always-held number even
    when the flag is not configured (flagged=False)."""
    rows = build(monkeypatch, flagged)
    keys = [r[25] for r in rows]
    assert keys == ["li-normal"]
    assert all(r[2] != "00.02" for r in rows)
