"""U-797c2b item 3: the per-kind coverage map replaces the Contract Labor branch.

Bill, Expense and Contract Labor outcomes are unchanged. Unknown kinds refuse.
"""
from __future__ import annotations

from types import SimpleNamespace

from shared.authz import provenance as prov


def _n(nid, *, kind="bill", project_id=None, work_date=None, subject=None):
    return SimpleNamespace(Id=nid, Kind=kind, ProjectId=project_id, WorkDate=work_date, Subject=subject)


def test_coverage_map_names_each_kind_rule():
    assert prov.COVERAGE_BY_KIND == {
        "bill": "target",
        "expense": "target",
        "bill_credit": "target",
        "contract_labor": "project_date",
    }


def test_coverage_map_matches_the_review_notification_kind_check_constraint():
    # U-797c2b pass 2 (F): the kinds the binder accepts must be the kinds the table accepts.
    import re
    from pathlib import Path

    sql = (Path(prov.__file__).resolve().parents[2] / "entities" / "review" / "sql"
           / "dbo.review_notification.sql").read_text(encoding="utf-8")
    match = re.search(r"CK_ReviewNotification_Kind\] CHECK \(\[Kind\] IN \(([^)]*)\)", sql)
    assert match, "CK_ReviewNotification_Kind not found"
    kinds = set(re.findall(r"N'(\w+)'", match.group(1)))
    assert kinds == set(prov.COVERAGE_BY_KIND)


def test_bill_target_outcomes_unchanged():
    assert prov.select_notification([], ("bill", 5)) == ("NO_ROW", "no_row", None)
    two = [(_n(1), {("bill", 5)}), (_n(2), {("bill", 5)})]
    assert prov.select_notification(two, ("bill", 5))[:2] == ("REFUSED", "ambiguous")
    one = [(_n(1), {("bill", 5)})]
    assert prov.select_notification(one, ("bill", 5))[0] == "BOUND"
    assert prov.select_notification(one, ("bill", 6))[:2] == ("REFUSED", "wrong_document")


def test_expense_target_outcomes_unchanged():
    one = [(_n(1, kind="expense"), {("expense", 3)})]
    assert prov.select_notification(one, ("expense", 3))[0] == "BOUND"
    assert prov.select_notification(one, ("expense", 4))[:2] == ("REFUSED", "wrong_document")


def test_bill_credit_target_binds_through_the_same_target_rule():
    one = [(_n(1, kind="bill_credit"), {("bill_credit", 9)})]
    status, reason, notification = prov.select_notification(one, ("bill_credit", 9))
    assert (status, reason) == ("BOUND", "bound")
    assert notification is one[0][0]
    assert prov.select_notification(one, ("bill_credit", 8))[:2] == ("REFUSED", "wrong_document")


def test_unknown_kind_refuses_as_wrong_document_even_when_targets_name_it():
    # U-797c2b fix round 1 (F2): an unknown kind refuses with trunk's reason, wrong_document.
    one = [(_n(1, kind="invoice"), {("invoice", 2)})]
    assert prov.select_notification(one, ("invoice", 2)) == ("REFUSED", "wrong_document", None)
    assert prov.select_notification(one, ("bogus", 2)) == ("REFUSED", "wrong_document", None)
    assert prov.select_notification([], ("bogus", 2)) == ("NO_ROW", "no_row", None)


def test_contract_labor_project_and_date_rule_unchanged():
    cl = [(_n(1, kind="contract_labor", project_id=1, work_date="2026-11-19"), {("contract_labor", 7)})]
    target = ("contract_labor", 7)
    assert prov.select_notification(cl, target, project_id=1, work_date="2026-11-19")[0] == "BOUND"
    assert prov.select_notification(cl, target, project_id=2, work_date="2026-11-19")[1] == (
        "wrong_project_or_date")
    assert prov.select_notification(cl, target, project_id=1, work_date="2026-11-18")[1] == (
        "wrong_project_or_date")
    assert prov.select_notification(cl, target)[1] == "wrong_project_or_date"  # no scope: refuses


def test_contract_labor_wrong_notification_kind_still_refuses():
    bill = [(_n(1, kind="bill", project_id=1, work_date="2026-11-19"), {("contract_labor", 7)})]
    assert prov.select_notification(bill, ("contract_labor", 7), project_id=1,
                                    work_date="2026-11-19")[1] == "wrong_kind"


def test_contract_labor_late_added_member_still_binds_on_project_and_date():
    cl = [(_n(1, kind="contract_labor", project_id=1, work_date="2026-11-19"), set())]
    assert prov.select_notification(cl, ("contract_labor", 99), project_id=1,
                                    work_date="2026-11-19")[0] == "BOUND"
