"""U-206 — DrawFinancialsService.coded_draws_for_project (canonical-draw select + fee)."""

from decimal import Decimal


class _Inv:
    def __init__(self, id, number, date):
        self.id = id
        self.invoice_number = number
        self.invoice_date = date


class _FakeInvoices:
    def __init__(self, invoices):
        self._invoices = invoices

    def read_paginated(self, **kwargs):
        return list(self._invoices)


class _FakeLineItems:
    def read_by_invoice_id(self, invoice_id):
        # Opaque marker rows; enrich_line_items is monkeypatched to key on the id.
        return [{"id": invoice_id}]


def _patch_enrich(monkeypatch, by_invoice):
    def fake_enrich(line_items):
        iid = line_items[0]["id"]
        return by_invoice.get(iid, [])
    monkeypatch.setattr("entities.invoice.business.enrichment.enrich_line_items", fake_enrich)


def test_coded_draws_selects_coded_only_and_computes_fee(monkeypatch):
    from entities.invoice.business.draw_financials import DrawFinancialsService

    invoices = [
        _Inv(1, "HA-01", "2026-02-27"),   # coded
        _Inv(2, "HA-01-2", "2026-03-11"),  # Manual-only duplicate -> excluded
        _Inv(3, "HA-02", "2026-04-10"),   # coded
        _Inv(4, "HA-05", "2026-08-01"),   # uncoded (all Manual) -> excluded
    ]
    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "06", "cost_code_name": "Grading", "billed_price": 1000.0}],
        2: [{"source_type": "Manual", "cost_code_number": "06", "cost_code_name": "Grading", "billed_price": 500.0}],
        3: [{"source_type": "ExpenseLineItem", "cost_code_number": "13", "cost_code_name": "Framing", "billed_price": 2000.0}],
        4: [{"source_type": "Manual", "cost_code_number": "59", "cost_code_name": "Pool", "billed_price": 9999.0}],
    })

    svc = DrawFinancialsService(invoice_service=_FakeInvoices(invoices), line_item_service=_FakeLineItems())
    draws = svc.coded_draws_for_project(128, Decimal("0.14"))

    # Only the two CODED draws survive; the Manual-only duplicate + uncoded HA-05 are dropped.
    assert [d["label"] for d in draws] == ["HA-01", "HA-02"]
    ha01 = draws[0]
    assert ha01["subtotal"] == Decimal("1000.00")
    assert ha01["builders_fee"] == Decimal("140.00")   # 1000 * 0.14
    assert ha01["total"] == Decimal("1140.00")
    assert ha01["categories"] == [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("1000.00")}]


def test_coded_draws_no_fee_when_rate_none(monkeypatch):
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "06", "cost_code_name": "Grading", "billed_price": 1000.0}],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "HA-01", "2026-02-27")]),
        line_item_service=_FakeLineItems(),
    )
    draws = svc.coded_draws_for_project(128, None)
    assert draws[0]["builders_fee"] == Decimal("0")
    assert draws[0]["total"] == Decimal("1000.00")


def test_coded_draws_empty_project_returns_empty():
    from entities.invoice.business.draw_financials import DrawFinancialsService
    assert DrawFinancialsService().coded_draws_for_project(None, Decimal("0.14")) == []
