"""U-791: the G702/G703 render for a project with no budget, and PERIOD TO is the
latest source date on the draw.

Pure-logic / no-live-DB. With no schedule of values the contract-dependent fields
(G703 C / % / H, G702 lines 1 / 3 / 9) are None and render blank instead of $0 and a
negative balance to finish; the payment fields are unchanged. The packet route keeps
"no budget" ([] -> render) apart from "lookup failed" (None -> skip the pages).
"""

import io
from decimal import Decimal
from types import SimpleNamespace

from pypdf import PdfReader

from entities.invoice.api import router
from entities.invoice.business.g702 import build_g702_lines, build_g702_pdf
from entities.invoice.business.g703 import build_g703_pdf, build_g703_rows

# Shaped like WR-01: one coded draw, a fee, no budget.
DRAWS = [
    {"label": "WR-01", "date": "2026-10-30",
     "categories": [
         {"cost_code_number": "01", "cost_code_name": "Permits", "amount": Decimal("257.48")},
         {"cost_code_number": "06", "cost_code_name": "Grading/Sitework/Demolition",
          "amount": Decimal("20075.00")},
     ],
     "subtotal": Decimal("20332.48"), "builders_fee": Decimal("2846.55"),
     "total": Decimal("23179.03")},
]


def _pdf_text(pdf_bytes: bytes) -> str:
    return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


# ---- G703 rows -----------------------------------------------------------------

def test_no_schedule_blanks_c_pct_h_and_keeps_payment_columns():
    rows, grand = build_g703_rows([], DRAWS, "WR-01")
    by = {r["item_no"]: r for r in rows}

    assert set(by) == {"01", "06", "90"}  # the draws' codes + the synthetic fee line
    for r in rows:
        assert r["scheduled"] is None and r["pct"] is None and r["balance"] is None
    assert by["06"]["prev"] == Decimal("0")
    assert by["06"]["this_period"] == Decimal("20075.00")
    assert by["06"]["total_to_date"] == Decimal("20075.00")
    assert by["90"]["this_period"] == Decimal("2846.55")

    assert grand["scheduled"] is None and grand["pct"] is None and grand["balance"] is None
    assert grand["this_period"] == Decimal("23179.03") == DRAWS[0]["total"]
    assert grand["total_to_date"] == Decimal("23179.03")


def test_a_schedule_still_fills_c_pct_h():
    sov = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("40150")}]
    rows, grand = build_g703_rows(sov, DRAWS, "WR-01")
    by = {r["item_no"]: r for r in rows}

    assert by["06"]["scheduled"] == Decimal("40150")
    assert by["06"]["pct"] == "50.00%"
    assert by["06"]["balance"] == Decimal("20075.00")
    # A draw code missing from the budget is still a $0-scheduled row, not blank.
    assert by["01"]["scheduled"] == Decimal("0") and by["01"]["balance"] == Decimal("-257.48")
    assert grand["scheduled"] == Decimal("40150")
    assert grand["balance"] == Decimal("40150") - Decimal("23179.03")


# ---- G702 lines ----------------------------------------------------------------

def test_no_schedule_blanks_lines_1_3_9_only():
    _, grand = build_g703_rows([], DRAWS, "WR-01")
    lines = build_g702_lines(grand)

    assert lines["l1_original_contract_sum"] is None
    assert lines["l3_contract_sum_to_date"] is None
    assert lines["l9_balance_to_finish"] is None
    assert lines["l2_net_change_orders"] == Decimal("0")
    assert lines["l4_total_completed_stored"] == Decimal("23179.03")
    assert lines["l6_total_earned_less_retainage"] == Decimal("23179.03")
    assert lines["l7_less_previous_certificates"] == Decimal("0")
    assert lines["l8_current_payment_due"] == Decimal("23179.03")


def test_explicit_contract_sum_wins_over_a_missing_schedule():
    _, grand = build_g703_rows([], DRAWS, "WR-01")
    lines = build_g702_lines(grand, original_contract_sum=Decimal("100000"))

    assert lines["l1_original_contract_sum"] == Decimal("100000")
    assert lines["l3_contract_sum_to_date"] == Decimal("100000")
    assert lines["l9_balance_to_finish"] == Decimal("100000") - Decimal("23179.03")


def test_zero_schedule_is_not_a_missing_schedule():
    """A budget whose lines are all $0 is still a budget: $0, not blank."""
    sov = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("0")}]
    _, grand = build_g703_rows(sov, DRAWS, "WR-01")
    lines = build_g702_lines(grand)

    assert grand["scheduled"] == Decimal("0")
    assert lines["l1_original_contract_sum"] == Decimal("0")
    assert lines["l9_balance_to_finish"] == Decimal("-23179.03")


# ---- renderers -----------------------------------------------------------------

def test_g703_pdf_renders_blanks_not_negative_balances():
    rows, grand = build_g703_rows([], DRAWS, "WR-01")
    text = _pdf_text(build_g703_pdf(
        {"application_no": "WR-01", "application_date": "10/30/2026",
         "period_to": "10/15/2026", "architect_project_no": "N/A"}, rows, grand))

    assert "$23,179.03" in text and "$20,075.00" in text
    assert "($" not in text          # no negative balance anywhere
    assert "%" not in text.replace("% (G", "")  # no % values (the header's '% (G÷C)' aside)
    assert "10/15/2026" in text


def test_g702_pdf_renders_blank_contract_lines():
    _, grand = build_g703_rows([], DRAWS, "WR-01")
    text = _pdf_text(build_g702_pdf(
        {"owner_lines": ["Owner"], "project": "WR", "application_no": "WR-01",
         "period_to": "10/15/2026", "contractor_lines": ["Rogers Build, Inc."],
         "architect_lines": [], "contract_for": "", "contract_date": ""},
        build_g702_lines(grand)))

    assert "23,179.03" in text
    assert "(23,179.03)" not in text  # line 9 is blank, not a negative balance
    assert "10/15/2026" in text


# ---- PERIOD TO -----------------------------------------------------------------

def test_period_to_is_the_latest_source_date():
    from datetime import date
    rows = [{"source_date": "08-28-2026"}, {"source_date": "10-15-2026"},
            {"source_date": "09-03-2026"}]
    assert router._period_to_date(rows) == date(2026, 10, 15)


def test_period_to_skips_blank_and_unparseable_dates():
    from datetime import date
    rows = [{"source_date": ""}, {"source_date": "not a date"}, {},
            {"source_date": "10-02-2026"}]
    assert router._period_to_date(rows) == date(2026, 10, 2)


def test_period_to_is_none_without_any_date():
    assert router._period_to_date([]) is None
    assert router._period_to_date([{"source_date": ""}]) is None


# ---- budget lookup: no budget vs failed ----------------------------------------

_PROJECT = SimpleNamespace(id=203, public_id="20BF258B-1C5A-4AC7-AF8B-8198A32D3E95")


def _patch(monkeypatch, *, project=_PROJECT, budget=None, raises=False):
    class _Projects:
        def read_by_id(self, project_id):
            return project

    class _Budgets:
        def read_by_project_public_id(self, public_id):
            if raises:
                raise RuntimeError("db down")
            return budget

        def variance_by_public_id(self, public_id):
            return {"cost_codes": [{"cost_code_number": "06", "cost_code_name": "Grading",
                                    "budget_price": "40150"}]}

    monkeypatch.setattr("entities.project.business.service.ProjectService", _Projects)
    monkeypatch.setattr("entities.budget.business.service.BudgetService", _Budgets)


def test_no_budget_is_an_empty_schedule(monkeypatch):
    _patch(monkeypatch, budget=None)
    assert router._budget_sov_for_project(203) == []


def test_a_failed_lookup_is_none_not_empty(monkeypatch):
    _patch(monkeypatch, raises=True)
    assert router._budget_sov_for_project(203) is None


def test_an_unreadable_project_is_none(monkeypatch):
    _patch(monkeypatch, project=None)
    assert router._budget_sov_for_project(203) is None


def test_a_budget_returns_its_schedule(monkeypatch):
    _patch(monkeypatch, budget=SimpleNamespace(public_id="B1"))
    assert router._budget_sov_for_project(203) == [
        {"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("40150")}]


def test_g702_pdf_lines_1_3_9_print_no_value():
    import re
    _, grand = build_g703_rows([], DRAWS, "WR-01")
    text = _pdf_text(build_g702_pdf(
        {"owner_lines": ["Owner"], "project": "WR", "application_no": "WR-01",
         "period_to": "10/15/2026", "contractor_lines": ["Rogers Build, Inc."],
         "architect_lines": [], "contract_for": "", "contract_date": ""},
        build_g702_lines(grand)))

    # Each blank line's "$" is followed directly by the next line's label, no amount.
    assert re.search(r"ORIGINAL CONTRACT SUM\s*\$\s*2\. Net change", text)
    assert re.search(r"CONTRACT SUM TO DATE \(Line 1 ± 2\)\s*\$\s*4\. TOTAL COMPLETED", text)
    assert re.search(r"\(Line 3 less 6\)\s*\$\s*CHANGE ORDER SUMMARY", text)
    # ...while a valued line keeps its amount (the regexes above are not vacuous).
    assert re.search(r"Net change by Change Orders \(Incl in line 1\)\s*\$\s*0\.00", text)


# ---- the packet route's AIA helper (the wiring) ---------------------------------

_INVOICE = SimpleNamespace(project_id=203, invoice_number="WR-01", invoice_date="2026-10-30")
_TOC = [{"source_date": "08-28-2026"}, {"source_date": "10-15-2026"}]


def _patch_aia(monkeypatch, sov):
    monkeypatch.setattr(router, "_budget_sov_for_project", lambda project_id: sov)

    class _Projects:
        def read_by_id(self, project_id):
            return SimpleNamespace(name="WR - 5224 Williamsburg Road")

    monkeypatch.setattr("entities.project.business.service.ProjectService", _Projects)


def test_aia_pages_render_without_a_budget(monkeypatch):
    _patch_aia(monkeypatch, [])
    g702, g703 = router._build_aia_pages(_INVOICE, DRAWS, _TOC, "Owner", [], "pid")

    assert g702 and g703
    t702, t703 = _pdf_text(g702), _pdf_text(g703)
    assert "23,179.03" in t702 and "(23,179.03)" not in t702
    assert "$23,179.03" in t703 and "($" not in t703
    # PERIOD TO = latest source date; APPLICATION DATE stays the invoice date.
    assert "10/15/2026" in t702 and "10/15/2026" in t703
    assert "10/30/2026" in t703


def test_aia_pages_skip_when_the_budget_lookup_fails(monkeypatch):
    _patch_aia(monkeypatch, None)
    assert router._build_aia_pages(_INVOICE, DRAWS, _TOC, "Owner", [], "pid") == (None, None)


def test_aia_pages_with_a_budget_fill_the_contract_fields(monkeypatch):
    _patch_aia(monkeypatch, [{"cost_code_number": "06", "cost_code_name": "Grading",
                              "scheduled_value": Decimal("40150")}])
    g702, g703 = router._build_aia_pages(_INVOICE, DRAWS, _TOC, "Owner", [], "pid")

    assert "$40,150.00" in _pdf_text(g703)
    assert "40,150.00" in _pdf_text(g702)


def test_period_to_falls_back_to_the_invoice_date(monkeypatch):
    _patch_aia(monkeypatch, [])
    g702, g703 = router._build_aia_pages(_INVOICE, DRAWS, [{"source_date": ""}], "Owner", [], "pid")

    assert "PERIOD TO: 10/30/2026" in _pdf_text(g702).replace("\n", " ")
