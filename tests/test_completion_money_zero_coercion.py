"""Completion/finalize money coercion guards (U-199).

``Decimal(0)`` is falsy in Python. Finalize paths that used truthy guards
(``if invoice.total_amount`` / ``if line_item.markup``) coerced a genuine
$0.00 total or 0% markup to ``None``, and invoice finalize additionally used
``float()`` — violating the CLAUDE.md exact-decimal rule.

**No value was ever lost.** Gate-1 traced the full path and the symptom is
PRESERVE-on-``None``, not clear-on-``None``: ``finalize`` re-passes what it
just read, and the services skip the field entirely when ``None`` is passed
(``invoice/business/service.py:258``, ``invoice_line_item:153-157``,
``bill_credit:190``), so the stored ``Decimal`` is written back unchanged.
Note this preserve lives in **Python**, not SQL — the UPDATE sprocs set these
columns unconditionally (``dbo.invoice.sql:275``,
``dbo.invoice_line_item.sql:282-284``, ``dbo.bill_credit.sql:236``, no
``CASE WHEN`` guard).

So these specs pin **hygiene and insurance**, not a live money bug (U-199 was
right-sized from 🔴 to P3 on that finding). What they defend against is the
landmine: the day ``finalize`` passes a *computed* total instead of the one it
read — recompute-header-on-complete is the plausible near-term change — a
truthy guard would silently drop a computed $0.00 and the stale total would
stand; and with the sprocs clear-on-``None``, a ``None`` that ever reaches the
repo directly nulls the column.

Pure-logic tests patch downstream side effects and assert on the production
handoff kwargs to ``update_by_public_id``. All money fields route through
``shared.api.money.to_decimal_or_none``.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from entities.bill_credit.business.complete_service import BillCreditCompleteService
from entities.bill_credit.business.model import BillCredit
from entities.invoice.business.model import Invoice
from entities.invoice.business.service import InvoiceService
from entities.invoice_line_item.business.model import InvoiceLineItem

_BC_MODULE = "entities.bill_credit.business.complete_service"

# BillCreditCompleteService.__init__ eagerly constructs these; stub them at module
# scope so the service can be built without touching SharePoint/Graph.
_BC_MS_STUBS = {
    "DriveItemProjectExcelConnector": MagicMock,
    "DriveItemProjectModuleConnector": MagicMock,
    "MsDriveItemService": MagicMock,
    "MsDriveRepository": MagicMock,
}


def _assert_money(value, expected: str):
    """Exact Decimal handoff: never dropped to None, never coerced to float."""
    assert value is not None
    assert isinstance(value, Decimal)
    assert not isinstance(value, float)
    assert value == Decimal(expected)


def _invoice(**overrides) -> Invoice:
    base = dict(
        id=1,
        public_id="inv-1",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        project_id=None,
        payment_term_id=None,
        invoice_date="2026-08-02",
        due_date="2026-08-02",
        invoice_number="INV-1",
        total_amount=Decimal("0.00"),
        memo=None,
        is_draft=True,
    )
    base.update(overrides)
    return Invoice(**base)


def _invoice_line_item(**overrides) -> InvoiceLineItem:
    base = dict(
        id=1,
        public_id="ili-1",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        invoice_id=1,
        source_type="Manual",
        bill_line_item_id=None,
        expense_line_item_id=None,
        bill_credit_line_item_id=None,
        description="Line",
        amount=Decimal("0.00"),
        markup=Decimal("0"),
        price=Decimal("0.00"),
        is_draft=True,
    )
    base.update(overrides)
    return InvoiceLineItem(**base)


def _bill_credit(**overrides) -> BillCredit:
    base = dict(
        id=1,
        public_id="bc-1",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=1,
        credit_date="2026-08-02",
        credit_number="VC-1",
        total_amount=Decimal("0.00"),
        memo=None,
        is_draft=True,
    )
    base.update(overrides)
    return BillCredit(**base)


def _run_complete_invoice(invoice: Invoice, line_items=()) -> tuple[dict, dict]:
    """Drive ``complete_invoice`` with every side effect stubbed.

    Returns ``(header_kwargs, line_kwargs)`` — the real kwargs the production code
    hands to ``InvoiceService.update_by_public_id`` and to
    ``InvoiceLineItemService.update_by_public_id``, not a mock artifact.
    """
    header_captured: dict = {}
    line_captured: dict = {}

    def _capture_header(**kwargs):
        header_captured.update(kwargs)
        return _invoice(**{**invoice.__dict__, "is_draft": False})

    def _capture_line(**kwargs):
        line_captured.update(kwargs)
        return line_items[0] if line_items else None

    service = InvoiceService()
    service.read_by_public_id = MagicMock(return_value=invoice)
    service.update_by_public_id = MagicMock(side_effect=_capture_header)
    service.invoice_line_item_service.read_by_invoice_id = MagicMock(
        return_value=list(line_items)
    )
    service.invoice_line_item_service.update_by_public_id = MagicMock(
        side_effect=_capture_line
    )
    # Downstream of the captured handoff: packet render, SharePoint, Excel, Box.
    service._mark_source_as_billed = MagicMock()
    service._upload_to_sharepoint = MagicMock(return_value={"success": True, "errors": []})
    service.sync_to_excel_workbook = MagicMock(return_value={"success": True})
    service._enqueue_box_excel = MagicMock()
    service._enqueue_box_line_pdfs = MagicMock()

    with patch("entities.invoice.business.service.PaymentTermService"), patch(
        "entities.invoice.api.router._generate_invoice_packet"
    ):
        service.complete_invoice(public_id="inv-1")

    return header_captured, line_captured


def _run_complete_bill_credit(bill_credit: BillCredit) -> dict:
    """Drive ``complete_bill_credit``; return the real ``update_by_public_id`` kwargs."""
    captured: dict = {}

    def _capture_update(**kwargs):
        captured.update(kwargs)
        return bill_credit

    with patch.multiple(_BC_MODULE, **_BC_MS_STUBS):
        service = BillCreditCompleteService()
        service.bill_credit_service.read_by_public_id = MagicMock(return_value=bill_credit)
        service.bill_credit_service.update_by_public_id = MagicMock(side_effect=_capture_update)
        service.vendor_service.read_by_id = MagicMock(return_value=MagicMock(public_id="v-1"))
        service.bill_credit_line_item_service.read_by_bill_credit_id = MagicMock(return_value=[])
        service.complete_bill_credit(public_id="bc-1")

    return captured


@pytest.mark.parametrize("amount", ["0.00", "1234.56"])
def test_complete_invoice_header_total_amount_is_exact_decimal(amount):
    """THE guard: reverting header finalize to ``float(x) if x else None`` makes this RED."""
    header_captured, _ = _run_complete_invoice(_invoice(total_amount=Decimal(amount)))

    _assert_money(header_captured["total_amount"], amount)


@pytest.mark.parametrize("amount", ["0.00", "1234.56"])
def test_complete_invoice_line_item_money_is_exact_decimal(amount):
    """Pins amount, markup (0%), and price on the line-item finalize handoff.

    Deliberately does NOT assert the header total: that is
    ``test_complete_invoice_header_total_amount_is_exact_decimal``'s job, and
    duplicating it here only made a header regression light up a test named
    ``line_item``. Measured at Pass 2: removing it costs zero mutation coverage.
    """
    stored = Decimal(amount)
    _, line_captured = _run_complete_invoice(
        _invoice(total_amount=stored),
        [_invoice_line_item(amount=stored, markup=Decimal("0"), price=stored)],
    )

    _assert_money(line_captured["amount"], amount)
    _assert_money(line_captured["markup"], "0")
    _assert_money(line_captured["price"], amount)


@pytest.mark.parametrize("amount", ["0.00", "1234.56"])
def test_complete_bill_credit_total_amount_is_exact_decimal(amount):
    """THE guard: reverting bill-credit finalize to a truthy check makes this RED."""
    captured = _run_complete_bill_credit(_bill_credit(total_amount=Decimal(amount)))

    _assert_money(captured["total_amount"], amount)


def test_completion_money_survives_high_precision_decimal():
    """Pins the never-routed-through-float property, which the cases above cannot.

    ``1234.56`` survives ``Decimal -> float -> Decimal(str())`` intact, so the specs
    above stay GREEN against a re-laundering regression such as
    ``to_decimal_or_none(float(x))``. These values are column-legal but corrupt
    through float64: ``9999999999999999.99`` (DECIMAL(18,2)) becomes ``1e+16``, and
    ``99999999999999.9999`` (DECIMAL(18,4), the Markup column type) becomes
    ``100000000000000.0``. This is the only test that catches that regression —
    including at the invoice header, which is why it asserts the header here.
    """
    header_total = Decimal("9999999999999999.99")
    line_markup = Decimal("99999999999999.9999")

    header_captured, line_captured = _run_complete_invoice(
        _invoice(total_amount=header_total),
        [_invoice_line_item(amount=header_total, markup=line_markup, price=header_total)],
    )

    _assert_money(header_captured["total_amount"], "9999999999999999.99")
    _assert_money(line_captured["amount"], "9999999999999999.99")
    _assert_money(line_captured["price"], "9999999999999999.99")
    _assert_money(line_captured["markup"], "99999999999999.9999")

    bc_captured = _run_complete_bill_credit(_bill_credit(total_amount=header_total))

    _assert_money(bc_captured["total_amount"], "9999999999999999.99")
