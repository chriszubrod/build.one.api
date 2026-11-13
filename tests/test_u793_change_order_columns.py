"""U-793: the Schedule of Values splits the scheduled value into Original, Previous
Change Orders, This Period Change Orders and Revised; the Application for Payment's
Contract Sum / Net Change by Change Orders / Contract Sum to Date follow the same split.

Pure-logic / no-live-DB."""

import io
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from entities.invoice.api import router
from entities.invoice.business.pay_application import build_application_lines
from entities.invoice.business.schedule_of_values import build_schedule_of_values_pdf, build_sov_rows

D = Decimal


def _draw(label, date, amount, fee="0"):
    return {"label": label, "date": date,
            "categories": [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": D(amount)}],
            "subtotal": D(amount), "builders_fee": D(fee), "total": D(amount) + D(fee)}


DRAWS = [_draw("HA-01", "2026-08-31", "1000"), _draw("HA-02", "2026-09-30", "2000")]
SOV = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": D("10600"),
        "original_value": D("10000"),
        "change_orders": [
            {"date": "2026-08-15", "amount": D("100")},    # before HA-01 → previous
            {"date": "2026-08-31", "amount": D("200")},    # on the previous application's date → previous
            {"date": "2026-09-10", "amount": D("400")},    # after HA-01, by HA-02 → this period
            {"date": "2026-10-05", "amount": D("-150")},   # after HA-02 → not yet in effect
            {"date": None, "amount": D("50")},             # undated → previous
        ]}]


# ---- row split ---------------------------------------------------------------------

def test_change_orders_split_by_the_previous_and_this_application_dates():
    rows, grand = build_sov_rows(SOV, DRAWS, "HA-02", current_date="2026-09-30")
    r = next(x for x in rows if x["item_no"] == "06")
    assert r["original"] == D("10000")
    assert r["co_prev"] == D("350")      # 100 + 200 + undated 50
    assert r["co_this"] == D("400")
    assert r["scheduled"] == D("10750")  # revised AS OF THIS application (future -150 left out)
    assert r["balance"] == D("10750") - D("3000")
    assert (grand["original"], grand["co_prev"], grand["co_this"], grand["scheduled"]) == (
        D("10000"), D("350"), D("400"), D("10750"))


def test_the_first_application_has_no_previous_period():
    rows, _ = build_sov_rows(SOV, DRAWS[:1], "HA-01", current_date="2026-08-31")
    r = next(x for x in rows if x["item_no"] == "06")
    assert r["co_prev"] == D("50")                  # only the undated one
    assert r["co_this"] == D("300")                 # everything through HA-01's date
    assert r["scheduled"] == D("10350")


def test_a_bare_schedule_entry_is_all_original():
    rows, grand = build_sov_rows([{"cost_code_number": "06", "cost_code_name": "Grading",
                                   "scheduled_value": D("5000")}], DRAWS, "HA-02")
    r = next(x for x in rows if x["item_no"] == "06")
    assert (r["original"], r["co_prev"], r["co_this"], r["scheduled"]) == (D("5000"), 0, 0, D("5000"))


def test_no_budget_leaves_every_scheduled_value_column_blank():
    rows, grand = build_sov_rows([], DRAWS, "HA-02")
    for r in rows + [grand]:
        assert r["original"] is None and r["co_prev"] is None and r["co_this"] is None
        assert r["scheduled"] is None


# ---- application figures --------------------------------------------------------------

def test_application_contract_lines_follow_the_split():
    _, grand = build_sov_rows(SOV, DRAWS, "HA-02", current_date="2026-09-30")
    L = build_application_lines(grand)
    assert L["contract_sum"] == D("10000")                 # the original
    assert L["net_change_orders"] == D("750")              # previous + this period
    assert L["contract_sum_to_date"] == D("10750") == grand["scheduled"]
    assert L["balance_to_finish"] == D("10750") - D("3000")
    assert L["amount_due"] == D("2000")


def test_application_without_a_split_keeps_its_old_shape():
    L = build_application_lines({"scheduled": D("1000"), "prev": D("0"), "this_period": D("100"),
                                 "total_to_date": D("100")})
    assert (L["contract_sum"], L["net_change_orders"], L["contract_sum_to_date"]) == (
        D("1000"), D("0"), D("1000"))


# ---- renderer ---------------------------------------------------------------------------

HEADER = {"project": "HA", "application_no": "HA-02", "application_date": "09/30/2026",
          "period_to": "09/30/2026", "from_lines": ["Rogers Build, Inc."], "to_name": "Owner",
          "to_lines": []}


def _text(pdf: bytes) -> str:
    return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


def test_the_schedule_prints_the_industry_column_names_unsplit():
    rows, grand = build_sov_rows(SOV, DRAWS, "HA-02", current_date="2026-09-30")
    text = _text(build_schedule_of_values_pdf(HEADER, rows, grand))
    for name in ("SCHEDULED VALUE", "ORIGINAL", "REVISED", "WORK COMPLETED", "TO DATE"):
        assert name in text, name
    # Multi-word headers wrap between words, never inside one.
    for word in ("PREVIOUS", "CHANGE", "ORDERS", "APPLICATIONS", "COMPLETE", "APPLICATION"):
        assert word in text.split(), word
    for amount in ("$10,000.00", "$350.00", "$400.00", "$10,750.00"):
        assert amount in text, amount


def test_the_widest_money_values_stay_on_one_line():
    big = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": D("99999999.99"),
            "original_value": D("99999999.99"), "change_orders": []}]
    rows, grand = build_sov_rows(big, DRAWS, "HA-02")
    text = _text(build_schedule_of_values_pdf(HEADER, rows, grand))
    assert "$99,999,999.99" in text


# ---- the budget lookup ---------------------------------------------------------------------

def _patch_budget(monkeypatch, revisions, lines_by_rev, budget_price, cost_codes=None, rows=None):
    class _Projects:
        def read_by_id(self, project_id):
            return SimpleNamespace(id=128, public_id="P")

    class _Budgets:
        def read_by_project_public_id(self, public_id):
            return SimpleNamespace(id=9, public_id="B")

        # the real revision rollup, over the fake revision/line services below
        from entities.budget.business.service import BudgetService as _Real
        revision_breakdown_by_cost_code = _Real.revision_breakdown_by_cost_code

        def variance_by_public_id(self, public_id):
            return {"cost_codes": cost_codes or [{"cost_code_id": 6, "cost_code_number": "06",
                                                  "cost_code_name": "Grading",
                                                  "budget_price": budget_price}],
                    "rows": rows or [{"sub_cost_code_id": 601, "cost_code_id": 6},
                                     {"sub_cost_code_id": 602, "cost_code_id": 6}]}

    class _Revisions:
        def read_by_budget_id(self, budget_id):
            return revisions

    class _Lines:
        def read_by_budget_revision_id(self, revision_id):
            return lines_by_rev.get(revision_id, [])

    monkeypatch.setattr("entities.project.business.service.ProjectService", _Projects)
    monkeypatch.setattr("entities.budget.business.service.BudgetService", _Budgets)
    monkeypatch.setattr("entities.budget_revision.business.service.BudgetRevisionService", _Revisions)
    monkeypatch.setattr("entities.budget_line_item.business.service.BudgetLineItemService", _Lines)


def _rev(id, type, status="approved", effective=None, approved="2026-07-30 03:08:01"):
    return SimpleNamespace(id=id, type=type, status=status, effective_date=effective,
                           approved_datetime=approved)


def _line(scc, price):
    return SimpleNamespace(sub_cost_code_id=scc, price=D(price))


def test_lookup_splits_original_from_dated_change_orders(monkeypatch):
    _patch_budget(monkeypatch,
                  [_rev(1, "original"), _rev(2, "change_order", effective="2026-03-16"),
                   _rev(3, "change_order", status="draft")],
                  {1: [_line(601, "900"), _line(602, "100")], 2: [_line(601, "250")],
                   3: [_line(601, "9999")]},          # a draft never counts
                  "1250")
    assert router._budget_sov_for_project(128) == [{
        "cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": D("1250"),
        "original_value": D("1000"),
        "change_orders": [{"date": "2026-03-16", "amount": D("250")}]}]


def test_lookup_dates_a_change_order_by_its_local_approval_day(monkeypatch):
    # Approved 7:30pm Central on 11/20 = 01:30 UTC on 11/21: the local day is the 20th.
    _patch_budget(monkeypatch, [_rev(1, "original"),
                                _rev(2, "change_order", approved="2026-11-21 01:30:00")],
                  {1: [_line(601, "1000")], 2: [_line(601, "250")]}, "1250")
    assert router._budget_sov_for_project(128)[0]["change_orders"] == [
        {"date": "2026-11-20", "amount": D("250")}]


def test_two_cost_codes_sharing_a_number_stay_separate(monkeypatch):
    _patch_budget(monkeypatch, [_rev(1, "original")],
                  {1: [_line(601, "10000"), _line(701, "20000")]}, None,
                  cost_codes=[{"cost_code_id": 6, "cost_code_number": "05", "cost_code_name": "A",
                               "budget_price": "10000"},
                              {"cost_code_id": 7, "cost_code_number": "05", "cost_code_name": "B",
                               "budget_price": "20000"}],
                  rows=[{"sub_cost_code_id": 601, "cost_code_id": 6},
                        {"sub_cost_code_id": 701, "cost_code_id": 7}])
    sov = router._budget_sov_for_project(128)
    assert [e["original_value"] for e in sov] == [D("10000"), D("20000")]


def test_uncategorized_budget_money_fails_the_lookup(monkeypatch):
    _patch_budget(monkeypatch, [_rev(1, "original")], {1: [_line(601, "1000")]}, None,
                  cost_codes=[{"cost_code_id": 6, "cost_code_number": "06", "cost_code_name": "G",
                               "budget_price": "1000"},
                              {"cost_code_id": None, "cost_code_number": None,
                               "cost_code_name": "Uncategorized", "budget_price": "500"}])
    assert router._budget_sov_for_project(128) is None


def test_an_out_of_order_prior_draw_never_pulls_a_later_change_order_into_previous():
    sov = [{"cost_code_number": "06", "cost_code_name": "G", "scheduled_value": D("1100"),
            "original_value": D("1000"), "change_orders": [{"date": "2026-10-05", "amount": D("100")}]}]
    draws = [_draw("X-01", "2026-10-15", "10"), _draw("X-02", "2026-09-30", "20")]  # prior is later
    rows, _ = build_sov_rows(sov, draws, "X-02", current_date="2026-09-30")
    r = next(x for x in rows if x["item_no"] == "06")
    # 10/05 is after this application (9/30): not yet in effect — without the cap the
    # later prior draw (10/15) would have counted it as a previous change order.
    assert (r["co_prev"], r["co_this"], r["scheduled"]) == (0, 0, D("1000"))


def test_lookup_that_does_not_reconcile_is_a_failure_not_a_wrong_schedule(monkeypatch):
    _patch_budget(monkeypatch, [_rev(1, "original")], {1: [_line(601, "1000")]}, "1250")
    assert router._budget_sov_for_project(128) is None
