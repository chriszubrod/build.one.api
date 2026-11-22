"""U-797c4b (F3/F5/F6, N1) — static checks on the seed, the buildone routing prompt
and the design doc."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = (ROOT / "intelligence/persistence/sql/seed.bill_credit_agent.sql").read_text()
EXP_SEED = (ROOT / "intelligence/persistence/sql/seed.expense_agent.sql").read_text()
PROMPT = (ROOT / "intelligence/agents/buildone/prompt.md").read_text()
DESIGN = (ROOT / "docs/design/u797c-review-reply-binding.md").read_text()

PREDICATE = "AND (IsAgent IS NULL OR IsAgent = 0)"
HEAL = (
    "UPDATE dbo.[User]\n   SET IsAgent = 1,\n       ModifiedDatetime = @Now\n"
    " WHERE Id = @UserId\n   " + PREDICATE + ";\n"
)


def test_seed_heal_has_the_expense_predicate_and_print():
    assert HEAL in SEED
    assert HEAL in EXP_SEED
    assert "PRINT CONCAT('  bill_credit_agent: IsAgent ensured (id=', @UserId, ')');" in SEED


def test_seed_heal_is_unconditional_and_placed_before_the_role_grant():
    assert SEED.index(HEAL) > SEED.index("user exists (id=")
    assert SEED.index(HEAL) < SEED.index("IF NOT EXISTS (SELECT 1 FROM dbo.UserRole")


def test_seed_comment_is_true_about_the_predicate_and_prod():
    low = SEED.lower()
    assert "no-op" in low and "only touches a row" in low
    assert "fresh-install parity" in low or "fresh install parity" in low


def test_buildone_prompt_routes_bill_credit_reviewer_replies_to_delegate_to_bill_credit():
    line = [l for l in PROMPT.splitlines() if "BillCredit" in l and "apply_reviewer_decision" in l]
    assert line, "no BillCredit reviewer-reply routing line"
    assert "delegate_to_bill_credit" in line[0]


def test_design_doc_records_the_shipped_finder_and_rules():
    assert "Deferred: a BillCredit conversation-id finder" not in DESIGN
    assert "find_for_reviewer_reply" in DESIGN
    assert "exactly one" in DESIGN.lower()


def test_design_doc_records_the_late_read_and_the_open_question():
    low = DESIGN.lower()
    assert "late" in low
    assert "no push path" in low
