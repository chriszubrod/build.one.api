"""U-778b part 5: un-billing a BillCreditLineItem must call update_by_public_id with keyword fields.

Before the fix `_reset_source_as_unbilled` passed a `bill_credit_line_item=<schema>` kwarg the service does
not accept -> TypeError, swallowed by the QBO line connector, so a credit was never un-billed.
"""
from types import SimpleNamespace

from entities.invoice.business.service import InvoiceService


class _StrictCreditLineSvc:
    """Mirrors the real BillCreditLineItemService.update_by_public_id signature (keyword fields)."""

    calls: list = []

    def read_by_id(self, id):
        return SimpleNamespace(
            id=id, public_id="CLI-PUB", row_version="RV-1", is_billed=True, bill_credit_id=7
        )

    def update_by_public_id(
        self,
        public_id,
        *,
        tenant_id=None,
        row_version,
        bill_credit_public_id=None,
        is_billed=None,
    ):
        type(self).calls.append(
            {
                "public_id": public_id,
                "row_version": row_version,
                "bill_credit_public_id": bill_credit_public_id,
                "is_billed": is_billed,
            }
        )


class _CreditSvc:
    def read_by_id(self, id):
        return SimpleNamespace(id=id, public_id="CREDIT-PUB")


def test_reset_credit_source_unbills_with_keyword_fields(monkeypatch):
    import entities.bill_credit.business.service as credit_mod
    import entities.bill_credit_line_item.business.service as cli_mod

    _StrictCreditLineSvc.calls = []
    monkeypatch.setattr(cli_mod, "BillCreditLineItemService", _StrictCreditLineSvc)
    monkeypatch.setattr(credit_mod, "BillCreditService", _CreditSvc)

    svc = InvoiceService.__new__(InvoiceService)
    line = SimpleNamespace(source_type="BillCreditLineItem", bill_credit_line_item_id=55)
    svc._reset_source_as_unbilled(line)

    assert _StrictCreditLineSvc.calls == [
        {
            "public_id": "CLI-PUB",
            "row_version": "RV-1",
            "bill_credit_public_id": "CREDIT-PUB",
            "is_billed": False,
        }
    ]


def test_credit_unbill_kwargs_bind_to_the_real_signature(monkeypatch):
    import inspect

    from entities.bill_credit_line_item.business.service import BillCreditLineItemService
    import entities.bill_credit.business.service as credit_mod
    import entities.bill_credit_line_item.business.service as cli_mod

    seen = {}

    class _Recorder(_StrictCreditLineSvc):
        def update_by_public_id(self, public_id, **kw):
            seen["kw"] = {"public_id": public_id, **kw}

    monkeypatch.setattr(cli_mod, "BillCreditLineItemService", _Recorder)
    monkeypatch.setattr(credit_mod, "BillCreditService", _CreditSvc)
    svc = InvoiceService.__new__(InvoiceService)
    svc._reset_source_as_unbilled(SimpleNamespace(source_type="BillCreditLineItem", bill_credit_line_item_id=55))
    inspect.signature(BillCreditLineItemService.update_by_public_id).bind(None, **seen["kw"])
    assert "_via_internal_pipeline" not in seen["kw"]
