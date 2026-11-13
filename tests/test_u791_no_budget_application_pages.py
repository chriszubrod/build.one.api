"""U-791: the Application for Payment / Schedule of Values render for a project with no
budget, and PERIOD TO is the latest source date on the draw. (U-792 renamed the pages
from the AIA G702/G703 reproductions.)

Pure-logic / no-live-DB. With no schedule of values the contract-dependent fields
(schedule: scheduled value / % / balance; application: contract sum, contract sum to
date, balance to finish) are None and render blank instead of $0 and a negative
balance to finish; the payment figures are unchanged. The packet route keeps "no
budget" ([] -> render) apart from "lookup failed" (None -> skip the pages).
"""

import io
import re
from decimal import Decimal
from types import SimpleNamespace

from pypdf import PdfReader

from entities.invoice.api import router
from entities.invoice.business.pay_application import build_application_lines, build_application_pdf
from entities.invoice.business.schedule_of_values import build_schedule_of_values_pdf, build_sov_rows
from entities.project_draw_packet_setting.business.model import DrawPacketBlocks

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

HEADER = {"project": "WR", "application_no": "WR-01", "application_date": "10/30/2026",
          "period_to": "10/15/2026",
          "from_lines": ["Rogers Build, Inc."], "to_name": "Owner", "to_lines": []}


def _pdf_text(pdf_bytes: bytes) -> str:
    return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


# ---- schedule rows -------------------------------------------------------------

def test_no_schedule_blanks_scheduled_pct_balance_and_keeps_payment_columns():
    rows, grand = build_sov_rows([], DRAWS, "WR-01")
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


def test_a_schedule_still_fills_scheduled_pct_balance():
    sov = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("40150")}]
    rows, grand = build_sov_rows(sov, DRAWS, "WR-01")
    by = {r["item_no"]: r for r in rows}

    assert by["06"]["scheduled"] == Decimal("40150")
    assert by["06"]["pct"] == "50.00%"
    assert by["06"]["balance"] == Decimal("20075.00")
    # A draw code missing from the budget is still a $0-scheduled row, not blank.
    assert by["01"]["scheduled"] == Decimal("0") and by["01"]["balance"] == Decimal("-257.48")
    assert grand["scheduled"] == Decimal("40150")
    assert grand["balance"] == Decimal("40150") - Decimal("23179.03")


# ---- application figures -------------------------------------------------------

def test_no_schedule_blanks_the_contract_figures_only():
    _, grand = build_sov_rows([], DRAWS, "WR-01")
    lines = build_application_lines(grand)

    assert lines["contract_sum"] is None
    assert lines["contract_sum_to_date"] is None
    assert lines["balance_to_finish"] is None
    assert lines["net_change_orders"] == Decimal("0")
    assert lines["completed_to_date"] == Decimal("23179.03")
    assert lines["earned_less_retainage"] == Decimal("23179.03")
    assert lines["previous_applications"] == Decimal("0")
    assert lines["amount_due"] == Decimal("23179.03")


def test_explicit_contract_sum_wins_over_a_missing_schedule():
    _, grand = build_sov_rows([], DRAWS, "WR-01")
    lines = build_application_lines(grand, original_contract_sum=Decimal("100000"))

    assert lines["contract_sum"] == Decimal("100000")
    assert lines["contract_sum_to_date"] == Decimal("100000")
    assert lines["balance_to_finish"] == Decimal("100000") - Decimal("23179.03")


def test_zero_schedule_is_not_a_missing_schedule():
    """A budget whose lines are all $0 is still a budget: $0, not blank."""
    sov = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("0")}]
    _, grand = build_sov_rows(sov, DRAWS, "WR-01")
    lines = build_application_lines(grand)

    assert grand["scheduled"] == Decimal("0")
    assert lines["contract_sum"] == Decimal("0")
    assert lines["balance_to_finish"] == Decimal("-23179.03")


# ---- renderers -----------------------------------------------------------------

def test_sov_pdf_renders_blanks_not_negative_balances():
    rows, grand = build_sov_rows([], DRAWS, "WR-01")
    text = _pdf_text(build_schedule_of_values_pdf(HEADER, rows, grand))

    assert "$23,179.03" in text and "$20,075.00" in text
    assert "($" not in text                          # no negative balance anywhere
    assert not re.search(r"\d%", text)                # no % values (the "PERCENT COMPLETE" header aside)
    assert "10/15/2026" in text


def test_application_pdf_renders_blank_contract_figures():
    _, grand = build_sov_rows([], DRAWS, "WR-01")
    text = _pdf_text(build_application_pdf(HEADER, build_application_lines(grand),
                                           DrawPacketBlocks()))

    assert "23,179.03" in text
    assert "(23,179.03)" not in text  # balance to finish is blank, not a negative balance
    assert "10/15/2026" in text


def test_application_pdf_blank_figures_print_no_value():
    _, grand = build_sov_rows([], DRAWS, "WR-01")
    text = _pdf_text(build_application_pdf(HEADER, build_application_lines(grand),
                                           DrawPacketBlocks()))

    # Each blank figure's "$" is followed directly by the next row's label, no amount.
    assert re.search(r"Contract Sum\s*\$\s*Net Change by Change Orders", text)
    assert re.search(r"Contract Sum to Date\s*\$\s*Work Completed to Date", text)
    assert re.search(r"Balance to Finish\s*\$\s*The attached Schedule", text)
    # ...while a valued figure keeps its amount (the regexes above are not vacuous).
    assert re.search(r"Net Change by Change Orders\s*\$\s*0\.00", text)


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

        # the real revision rollup, over the fake revision/line services below
        from entities.budget.business.service import BudgetService as _Real
        revision_breakdown_by_cost_code = _Real.revision_breakdown_by_cost_code

        def variance_by_public_id(self, public_id):
            return {"cost_codes": [{"cost_code_id": 6, "cost_code_number": "06",
                                    "cost_code_name": "Grading", "budget_price": "40150"}],
                    "rows": [{"sub_cost_code_id": 601, "cost_code_id": 6, "cost_code_number": "06"}]}

    class _Revisions:
        def read_by_budget_id(self, budget_id):
            return [SimpleNamespace(id=1, status="approved", type="original",
                                    effective_date=None, approved_datetime="2026-01-01")]

    class _Lines:
        def read_by_budget_revision_id(self, revision_id):
            return [SimpleNamespace(sub_cost_code_id=601, price=Decimal("40150"))]

    monkeypatch.setattr("entities.project.business.service.ProjectService", _Projects)
    monkeypatch.setattr("entities.budget.business.service.BudgetService", _Budgets)
    monkeypatch.setattr("entities.budget_revision.business.service.BudgetRevisionService", _Revisions)
    monkeypatch.setattr("entities.budget_line_item.business.service.BudgetLineItemService", _Lines)


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
    _patch(monkeypatch, budget=SimpleNamespace(id=9, public_id="B1"))
    assert router._budget_sov_for_project(203) == [
        {"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("40150"),
         "original_value": Decimal("40150"), "change_orders": []}]


# ---- the packet route's helper (the wiring) ------------------------------------

_INVOICE = SimpleNamespace(project_id=203, invoice_number="WR-01", invoice_date="2026-10-30")
_TOC = [{"source_date": "08-28-2026"}, {"source_date": "10-15-2026"}]


def _patch_pages(monkeypatch, sov, blocks=None):
    monkeypatch.setattr(router, "_budget_sov_for_project", lambda project_id: sov)
    monkeypatch.setattr(router, "_draw_packet_blocks",
                        lambda project_id, public_id: blocks or DrawPacketBlocks())

    class _Projects:
        def read_by_id(self, project_id):
            return SimpleNamespace(name="WR - 5224 Williamsburg Road")

    monkeypatch.setattr("entities.project.business.service.ProjectService", _Projects)


def test_pages_render_without_a_budget(monkeypatch):
    _patch_pages(monkeypatch, [])
    app, sov = router._build_application_pages(_INVOICE, DRAWS, _TOC, "Owner", [], "pid")

    assert app and sov
    t_app, t_sov = _pdf_text(app), _pdf_text(sov)
    assert "Application for Payment" in t_app and "Schedule of Values" in t_sov
    assert "23,179.03" in t_app and "(23,179.03)" not in t_app
    assert "$23,179.03" in t_sov and "($" not in t_sov
    assert "WR - 5224 Williamsburg Road" in t_app and "WR - 5224 Williamsburg Road" in t_sov
    # PERIOD TO = latest source date; the application date stays the invoice date.
    assert "10/15/2026" in t_app and "10/15/2026" in t_sov
    assert "10/30/2026" in t_app and "10/30/2026" in t_sov


def test_pages_skip_when_the_budget_lookup_fails(monkeypatch):
    _patch_pages(monkeypatch, None)
    assert router._build_application_pages(_INVOICE, DRAWS, _TOC, "Owner", [], "pid") == (None, None)


def test_pages_with_a_budget_fill_the_contract_fields(monkeypatch):
    _patch_pages(monkeypatch, [{"cost_code_number": "06", "cost_code_name": "Grading",
                                "scheduled_value": Decimal("40150")}])
    app, sov = router._build_application_pages(_INVOICE, DRAWS, _TOC, "Owner", [], "pid")

    assert "$40,150.00" in _pdf_text(sov)
    assert "40,150.00" in _pdf_text(app)


def test_period_to_falls_back_to_the_invoice_date(monkeypatch):
    _patch_pages(monkeypatch, [])
    app, _ = router._build_application_pages(_INVOICE, DRAWS, [{"source_date": ""}], "Owner", [], "pid")

    assert "PERIOD TO 10/30/2026" in _pdf_text(app).replace("\n", " ")


def test_the_project_settings_reach_the_application(monkeypatch):
    _patch_pages(monkeypatch, [], DrawPacketBlocks(contractor_statement=False, owner_approval=False,
                                                   architect_certificate=True, notary=False))
    app, _ = router._build_application_pages(_INVOICE, DRAWS, _TOC, "Owner", [], "pid")
    text = _pdf_text(app)
    assert "Architect Review" in text
    assert "Contractor's Statement" not in text and "Notary" not in text
    assert "Owner Approval" not in text


def test_a_failed_settings_read_falls_back_to_the_defaults(monkeypatch):
    class _Boom:
        def blocks_for_project(self, project_id):
            raise RuntimeError("db down")

    monkeypatch.setattr(
        "entities.project_draw_packet_setting.business.service.ProjectDrawPacketSettingService",
        _Boom)
    assert router._draw_packet_blocks(203, "pid") == DrawPacketBlocks()
    assert router._draw_packet_blocks(None, "pid") == DrawPacketBlocks()
