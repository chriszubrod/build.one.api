"""Pure tests for AIA G702 Application and Certification for Payment PDF renderer."""

import io
from decimal import Decimal

from pypdf import PdfReader

from entities.invoice.business.cover import _format_money
from entities.invoice.business.g702 import build_g702_pdf


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "".join(page.extract_text() or "" for page in reader.pages)


def _header(**overrides):
    base = {
        "owner_lines": ["Acme Development LLC", "123 Main Street", "Austin, TX 78701"],
        "project": "Harbor View Residence",
        "application_no": "APP-2026-04",
        "period_to": "March 31, 2026",
        "contractor_lines": ["Build One Construction", "456 Commerce Blvd"],
        "architect_lines": ["Design Studio Architects", "789 Design Way"],
        "contract_for": "Construction of single-family residence",
        "contract_date": "January 15, 2025",
    }
    base.update(overrides)
    return base


def _lines(**overrides):
    base = {
        "l1_original_contract_sum": Decimal("850000.00"),
        "l2_net_change_orders": Decimal("12500.00"),
        "l3_contract_sum_to_date": Decimal("862500.00"),
        "l4_total_completed_stored": Decimal("420000.00"),
        "l5_retainage": Decimal("42000.00"),
        "l6_total_earned_less_retainage": Decimal("378000.00"),
        "l7_less_previous_certificates": Decimal("300000.00"),
        "l8_current_payment_due": Decimal("78000.00"),
        "l9_balance_to_finish": Decimal("484500.00"),
        "co_additions": Decimal("15000.00"),
        "co_deductions": Decimal("2500.00"),
    }
    base.update(overrides)
    return base


def test_g702_pdf_core_content():
    pdf = build_g702_pdf(_header(), _lines())
    assert pdf[:4] == b"%PDF"

    text = _pdf_text(pdf)
    assert "APPLICATION AND CERTIFICATION FOR PAYMENT" in text
    assert "ORIGINAL CONTRACT SUM" in text
    assert "CURRENT PAYMENT DUE" in text
    assert "Harbor View Residence" in text
    assert _format_money(Decimal("850000.00")) in text
    assert _format_money(Decimal("78000.00")) in text


def test_g702_lines_reconcile_from_g703_grand():
    from decimal import Decimal
    from entities.invoice.business.g702 import build_g702_lines

    grand = {"scheduled": Decimal("4783414.60"), "prev": Decimal("1142564.98"),
             "this_period": Decimal("506388.46"), "total_to_date": Decimal("1648953.44")}
    L = build_g702_lines(grand)
    assert L["l1_original_contract_sum"] == Decimal("4783414.60")
    assert L["l2_net_change_orders"] == Decimal("0")
    assert L["l3_contract_sum_to_date"] == Decimal("4783414.60")
    assert L["l4_total_completed_stored"] == Decimal("1648953.44")
    assert L["l5_retainage"] == Decimal("0")
    assert L["l6_total_earned_less_retainage"] == Decimal("1648953.44")
    assert L["l7_less_previous_certificates"] == Decimal("1142564.98")
    assert L["l8_current_payment_due"] == Decimal("506388.46")   # == grand this_period
    assert L["l9_balance_to_finish"] == Decimal("3134461.16")


def test_g702_lines_with_retainage():
    from decimal import Decimal
    from entities.invoice.business.g702 import build_g702_lines

    grand = {"scheduled": Decimal("1000"), "prev": Decimal("200"),
             "this_period": Decimal("100"), "total_to_date": Decimal("300")}
    L = build_g702_lines(grand, retainage_rate=Decimal("0.10"))
    assert L["l5_retainage"] == Decimal("30.00")          # 10% of cumulative 300
    assert L["l6_total_earned_less_retainage"] == Decimal("270.00")
    # L7 = previous NET of retainage = 200 - 20 = 180; L8 = 270 - 180 = 90
    # (= this_period 100 x (1 - 0.10)), the AIA-correct current payment due.
    assert L["l7_less_previous_certificates"] == Decimal("180.00")
    assert L["l8_current_payment_due"] == Decimal("90.00")
