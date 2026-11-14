"""U-652d8: the expense-coding queue / metrics reads use the same access rule as
CodingSuggestionService.assert_accessible (U-652d7): admin; else the suggestion's Expense; else its project; else
admin only. Only a line with no suggestion row yet (nothing to write to) falls back to its purchase's Expense.

The predicate is lifted VERBATIM from each of the three scans in qbo.expense_coding_queue.sql and evaluated in
SQLite, with the two access UDFs as Python stand-ins. So a mutant in any one scan's predicate fails here, not only
in the text pins. Every case names the mutant it kills. Prod proof (2026-11-14, read-only): 25 callers, 0
mismatches between the new proc and a per-row UDF check."""
import re
import sqlite3
from pathlib import Path

import pytest

SQL_FILE = Path(__file__).resolve().parents[1] / "integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql"

# Actor 1 may see Expense 10 and Project 100 only.
ACCESSIBLE_EXPENSES = {10}
ACCESSIBLE_PROJECTS = {100}


def _predicates() -> list[str]:
    """The three access predicates, as SQLite expressions (one per scan)."""
    text = SQL_FILE.read_text()
    blocks = re.findall(r"AND \(\n\s*-- U-652d8:.*?\n(.*?)\n\s*\)(?=;?\n)", text, re.S)
    out = []
    for block in blocks:
        body = "\n".join(line for line in block.splitlines() if not line.strip().startswith("--"))
        body = body.replace("[", "").replace("]", "")
        body = body.replace("dbo.UserCanAccessExpense(", "can_exp(").replace("dbo.UserCanAccessProject(", "can_proj(")
        body = body.replace("@ActorUserId", ":uid").replace("@ActorIsSystemAdmin", ":isa")
        out.append(body)
    return out


def test_all_three_scans_carry_the_predicate():
    # kills: a scan whose predicate was not migrated (it would not carry the U-652d8 marker)
    assert len(_predicates()) == 3


def _db():
    con = sqlite3.connect(":memory:")
    con.create_function("can_exp", 3, lambda uid, isa, eid: 1 if (isa == 1 or (uid == 1 and eid in ACCESSIBLE_EXPENSES)) else 0)
    con.create_function("can_proj", 3, lambda uid, isa, pid: 1 if (isa == 1 or (uid == 1 and pid in ACCESSIBLE_PROJECTS)) else 0)
    con.execute("CREATE TABLE cs (Id INT, ExpenseId INT, ConfirmedProjectId INT, SuggestedProjectId INT)")
    con.execute("CREATE TABLE e (Id INT)")
    return con


def _visible(pred: str, *, uid, isa, cs_expense=None, e_expense=None, confirmed=None, suggested=None,
             no_suggestion=False) -> bool:
    con = _db()
    if no_suggestion:
        # an unseeded line: LEFT JOIN to CodingSuggestion yields all-NULL cs columns (cs.Id NULL too)
        con.execute("INSERT INTO cs VALUES (NULL, NULL, NULL, NULL)")
    else:
        con.execute("INSERT INTO cs VALUES (1, ?, ?, ?)", (cs_expense, confirmed, suggested))
    con.execute("INSERT INTO e VALUES (?)", (e_expense,))
    row = con.execute(f"SELECT 1 FROM cs, e WHERE {pred}", {"uid": uid, "isa": isa}).fetchone()
    return row is not None


CASES = [
    # (id, kwargs, expected, kills)
    ("admin sees a no-scope row", dict(uid=9, isa=1), True, "dropping the admin arm"),
    ("non-admin: no expense, no project -> hidden", dict(uid=1, isa=0), False,
     "restoring the old 'no project = everyone' arm"),
    ("anonymous: no expense, no project -> hidden", dict(uid=None, isa=0), False, "same, missing actor"),
    ("unseeded line, no Expense -> hidden", dict(uid=1, isa=0, no_suggestion=True), False,
     "an unseeded row slipping through on NULL cs columns"),
    ("unseeded line, accessible purchase Expense -> visible", dict(uid=1, isa=0, no_suggestion=True, e_expense=10),
     True, "dropping the e.Id fallback (unseeded lines would vanish for expense holders)"),
    ("unseeded line, inaccessible purchase Expense -> hidden",
     dict(uid=1, isa=0, no_suggestion=True, e_expense=11), False, "an expense arm that ignores the UDF"),
    ("suggestion Expense accessible -> visible", dict(uid=1, isa=0, cs_expense=10), True, "dropping the expense arm"),
    ("suggestion Expense inaccessible -> hidden", dict(uid=1, isa=0, cs_expense=11), False,
     "an expense arm that ignores the UDF"),
    ("suggestion Expense wins over purchase Expense", dict(uid=1, isa=0, cs_expense=11, e_expense=10), False,
     "COALESCE(e.Id, cs.ExpenseId) (purchase first)"),
    ("expense inaccessible, project accessible -> hidden (expense decides)",
     dict(uid=1, isa=0, cs_expense=11, suggested=100), False,
     "falling through to the project arm when an expense exists"),
    ("no expense, project accessible -> visible", dict(uid=1, isa=0, suggested=100), True, "dropping the project arm"),
    ("no expense, project inaccessible -> hidden", dict(uid=1, isa=0, suggested=101), False,
     "a project arm that ignores the UDF"),
    ("confirmed project wins over suggested", dict(uid=1, isa=0, confirmed=101, suggested=100), False,
     "COALESCE(Suggested, Confirmed)"),
    # round 2 (both Pass-1 legs): a SEEDED row never falls back to the purchase's Expense, so it is scoped
    # exactly as assert_accessible scopes it (cs.ExpenseId, else project, else admin only).
    ("seeded, NULL ExpenseId, accessible purchase Expense, no project -> hidden",
     dict(uid=1, isa=0, e_expense=10), False,
     "applying the e.Id fallback to seeded rows (visible in the queue, refused on write)"),
    ("seeded, NULL ExpenseId, inaccessible purchase Expense, accessible project -> visible",
     dict(uid=1, isa=0, e_expense=11, suggested=100), True,
     "applying the e.Id fallback to seeded rows (writable via the project, hidden in the queue)"),
    ("NULL admin flag fails closed", dict(uid=1, isa=None), False, "treating a NULL flag as admin"),
    ("NULL admin flag still scopes by expense", dict(uid=1, isa=None, cs_expense=10), True,
     "a NULL flag that short-circuits every arm"),
    ("dangling ExpenseId (deleted Expense) -> hidden", dict(uid=1, isa=0, cs_expense=999, suggested=100), False,
     "falling through to the project arm when the expense row is gone"),
]


def test_each_expense_join_keys_on_the_purchase_qbo_identity():
    # kills: a join on the wrong column (e.g. the purchase's dbo-side Id) or one missing RealmId; the SQLite
    # harness above cannot see join keys, so they are pinned as text, one per scan.
    sql = SQL_FILE.read_text()
    for on in (
        "LEFT JOIN [dbo].[Expense] e  -- at most one: UQ_Expense_QboId_RealmId\n        ON e.[QboId] = r.[QboPurchaseQboId]\n       AND e.[RealmId] = r.[RealmId]",
        "LEFT JOIN [dbo].[Expense] e\n                ON e.[QboId] = p.[QboId]\n               AND e.[RealmId] = p.[RealmId]",
        "LEFT JOIN [dbo].[Expense] e\n        ON e.[QboId] = cs.[QboPurchaseQboId]\n       AND e.[RealmId] = cs.[RealmId]",
    ):
        assert sql.count(on) == 1, on


@pytest.mark.parametrize("scan", [0, 1, 2], ids=["queue", "metrics_total", "metrics_status"])
@pytest.mark.parametrize("case_id,kwargs,expected,kills", CASES, ids=[c[0] for c in CASES])
def test_access_rule(scan, case_id, kwargs, expected, kills):
    pred = _predicates()[scan]
    assert _visible(pred, **kwargs) is expected, f"{case_id} (kills: {kills})"
