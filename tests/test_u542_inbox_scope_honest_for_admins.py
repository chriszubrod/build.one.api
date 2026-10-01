"""U-542 — an admin's `mine` is honest; the admin bypass widens only `all`.

Decided 2026-10-01: `mine` means projects where the caller holds Project
Manager / Owner, for every actor; a system admin reaches the tenant through
`all`. Before this, `@IsSystemAdmin = 1` sat as a standalone OR-arm above the
scope arms in every ReadInboxTasks block, so for an admin EVERY scope returned
everything — while ReadInboxTaskCounts computed `Mine` without the bypass, so
the badge said 0 and the list showed the tenant. Measured on prod before the
change: uid 17 list mine=83, badge Mine=0.

Text pins in the shape of test_u454. Both `--` tails and `/* */` blocks are
stripped first, so a commented-out arm cannot keep a check green. Arms are
extracted POSITIONALLY per UNION ALL block — only the first block of the counts
proc carries `AS [Mine]`; the others inherit the names — and each arm is then
checked for its OWN entity's marker, so blocks cannot pair up wrongly.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = REPO_ROOT / "entities" / "review" / "sql" / "dbo.inbox_tasks.sql"
ENTITIES = ["Bill", "Expense", "BillCredit", "Invoice"]
# Parent alias in the list proc, and the Pending CTE's FK in the counts proc. The
# mask replaces ONLY these two spellings of "this entity's parent row", so a wrong
# parent column (P.[ExpenseId] inside the Bill block) stays visible and fails.
PARENT = {"Bill": ("B", "BillId"), "Expense": ("E", "ExpenseId"),
          "BillCredit": ("BC", "BillCreditId"), "Invoice": ("I", None)}
# Something present in this entity's arms and in no other entity's.
MARKER = {"Bill": "dbo.[BillLineItem]", "Expense": "dbo.[ExpenseLineItem]",
          "BillCredit": "dbo.[BillCreditLineItem]", "Invoice": "= I.[ProjectId]"}


def _executable(path: Path) -> str:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    return "\n".join(l.split("--")[0] for l in text.splitlines())


def _proc(sql: str, name: str) -> str:
    m = re.search(rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+dbo\.{name}\b.*?(?=\nGO\b|\Z)", sql, re.S)
    assert m, name
    return m.group(0)


def _norm(body: str, entity: str) -> str:
    s = re.sub(r"\s+", " ", body).strip()
    alias, fk = PARENT[entity]
    s = s.replace(f"= {alias}.[Id]", "= <PARENT>")
    if fk:
        s = s.replace(f"= P.[{fk}]", "= <PARENT>")
    return s


def _own_entity_only(arm: str, entity: str) -> None:
    assert MARKER[entity] in arm, f"{entity}: arm does not reference its own entity"
    for other, marker in MARKER.items():
        if other != entity:
            assert marker not in arm, f"{entity}: arm references {other}"


SQL = _executable(SQL_PATH)
LIST = _proc(SQL, "ReadInboxTasks")
COUNTS = _proc(SQL, "ReadInboxTaskCounts")

LIST_MINE = re.findall(r"@Scope = N'mine' AND EXISTS \((.*?)\n\s*\)\)", LIST, re.S)
LIST_ALL = re.findall(r"@Scope = N'all' AND \(\s*@IsSystemAdmin = 1\s*OR EXISTS \((.*?)\n\s*\)\n\s*\)\)", LIST, re.S)
COUNT_MINE = re.findall(r"CASE WHEN EXISTS \((.*?)\) THEN 1 ELSE 0 END", COUNTS, re.S)
COUNT_TOTAL = re.findall(r"CASE WHEN @IsSystemAdmin = 1 OR EXISTS \((.*?)\) THEN 1 ELSE 0 END", COUNTS, re.S)


def test_every_arm_was_found_once_per_entity():
    # Guards every pin below against passing vacuously on a regex that matched nothing.
    assert [len(LIST_MINE), len(LIST_ALL), len(COUNT_MINE), len(COUNT_TOTAL)] == [4, 4, 4, 4]


def test_the_admin_bypass_lives_only_inside_the_all_arm():
    assert LIST.count("@IsSystemAdmin = 1") == 4, "exactly one bypass per block, and only in `all`"
    assert not re.search(r"AND \(\s*@IsSystemAdmin = 1\s*OR \(@Scope", LIST), (
        "the old standalone bypass arm is back — an admin's `mine` would be the whole tenant again"
    )
    assert LIST_MINE and all("@IsSystemAdmin" not in arm for arm in LIST_MINE)
    assert LIST.count("(@Scope = N'mine_submitted' AND P.[SubmitterId] = @CurrentUserId)") == 4


def test_scope_is_compared_only_against_the_three_literals():
    # One declaration plus three arms per block. Any other reading of @Scope —
    # `OR @Scope IS NULL`, a fourth literal, a LIKE — changes this count.
    assert LIST.count("@Scope") == 13
    assert len(re.findall(r"@Scope = N'(mine|all|mine_submitted)'", LIST)) == 12


@pytest.mark.parametrize("i,entity", list(enumerate(ENTITIES)))
def test_badge_mine_matches_list_mine_arm_for_arm(i, entity):
    _own_entity_only(LIST_MINE[i], entity)
    _own_entity_only(COUNT_MINE[i], entity)
    assert _norm(LIST_MINE[i], entity) == _norm(COUNT_MINE[i], entity), f"{entity}: badge and list would disagree"


@pytest.mark.parametrize("i,entity", list(enumerate(ENTITIES)))
def test_badge_total_matches_list_all_arm_for_arm(i, entity):
    _own_entity_only(LIST_ALL[i], entity)
    _own_entity_only(COUNT_TOTAL[i], entity)
    assert _norm(LIST_ALL[i], entity) == _norm(COUNT_TOTAL[i], entity), f"{entity}: Total and `all` would disagree"


def test_counts_mine_carries_no_bypass_and_total_is_the_only_one():
    assert COUNT_MINE and all("@IsSystemAdmin" not in arm for arm in COUNT_MINE)
    assert COUNTS.count("@IsSystemAdmin = 1") == 4


def test_counts_mine_submitted_is_the_submitter_test_in_every_block():
    assert COUNTS.count("CASE WHEN P.[SubmitterId] = @CurrentUserId THEN 1 ELSE 0 END") == 4
