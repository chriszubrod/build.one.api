"""U-797c4a: static checks on the BillCredit recipient sproc and the reply-ingest branch.

Text-level only. No DB is touched. The recipient resolver is pinned by meaning: its
normalized body must equal ResolveReviewRecipientsByBillId's body after the documented
renames, with the single documented difference (SET NOCOUNT ON).
"""

import re

from tests.sproc_text import REPO_ROOT, strip_sql_comments

REVIEW_SQL = REPO_ROOT / "entities/review/sql/dbo.review.sql"
INGEST_SQL = REPO_ROOT / "entities/review/sql/dbo.review_reply_ingest.sql"
NEW = "ResolveReviewRecipientsByBillCreditId"
BILL = "ResolveReviewRecipientsByBillId"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", strip_sql_comments(text)).strip()


def _segment(raw: str, name: str) -> str:
    start = raw.index(f"CREATE OR ALTER PROCEDURE dbo.{name}")
    end = raw.index("\nGO", start)
    return raw[start:end]


def _bill_renamed_to_credit(bill_norm: str) -> str:
    """The documented renames from the Bill resolver to the BillCredit resolver."""
    out = bill_norm.replace(BILL, NEW)
    out = re.sub(r"@BillId\b", "@BillCreditId", out)
    out = out.replace("BillLineItem", "BillCreditLineItem")
    out = out.replace("[BillId]", "[BillCreditId]")
    out = out.replace("BillProjects", "BillCreditProjects")
    out = re.sub(r"\bbli\b", "bcli", out)
    out = re.sub(r"\bbp\b", "bcp", out)
    return out


# ---------------------------------------------------------------- recipient sproc


def test_bill_credit_recipient_sproc_defined_once():
    raw = REVIEW_SQL.read_text(encoding="utf-8")
    assert raw.count(f"CREATE OR ALTER PROCEDURE dbo.{NEW}") == 1


def test_bill_credit_recipient_sproc_is_go_terminated():
    raw = REVIEW_SQL.read_text(encoding="utf-8")
    seg = _segment(raw, NEW)
    assert seg.rstrip().endswith("END;")
    end = raw.index("\nGO", raw.index(f"CREATE OR ALTER PROCEDURE dbo.{NEW}"))
    assert raw[end:end + 3] == "\nGO"


def test_bill_credit_recipient_sproc_body_equals_bill_body_after_renames():
    raw = REVIEW_SQL.read_text(encoding="utf-8")
    expected = _bill_renamed_to_credit(_norm(_segment(raw, BILL)))
    actual = _norm(_segment(raw, NEW))
    # The one documented difference: the new resolver sets NOCOUNT (pyodbc needs it).
    assert actual.replace("SET NOCOUNT ON; ", "", 1) == expected
    assert "SET NOCOUNT ON;" in actual


def test_bill_credit_recipient_sproc_keeps_the_exclude_user_predicate():
    code = _norm(_segment(REVIEW_SQL.read_text(encoding="utf-8"), NEW))
    assert "@ExcludeUserId BIGINT = NULL" in code
    assert "(@ExcludeUserId IS NULL OR up.[UserId] <> @ExcludeUserId)" in code


def test_bill_credit_recipient_sproc_keeps_pm_wins_ordering():
    code = _norm(_segment(REVIEW_SQL.read_text(encoding="utf-8"), NEW))
    assert "WHEN 'Project Manager' THEN 1" in code
    assert "WHEN 'Owner' THEN 2" in code
    assert "ROW_NUMBER() OVER ( PARTITION BY [UserId] ORDER BY [RolePrecedence] ASC, [ProjectId] ASC ) AS rn" in code
    assert "WHERE dr.rn = 1" in code


def test_bill_credit_recipient_sproc_keeps_one_email_per_user():
    code = _norm(_segment(REVIEW_SQL.read_text(encoding="utf-8"), NEW))
    assert "ROW_NUMBER() OVER ( PARTITION BY c.[UserId] ORDER BY c.[Id] ASC ) AS rn" in code
    assert "AND ue.rn = 1" in code


def test_bill_credit_recipient_sproc_keeps_the_human_only_predicate():
    code = _norm(_segment(REVIEW_SQL.read_text(encoding="utf-8"), NEW))
    assert "AND dbo.IsHumanReviewUser(up.[UserId]) = 1" in code


def test_bill_credit_recipient_sproc_walks_bill_credit_line_items():
    code = _norm(_segment(REVIEW_SQL.read_text(encoding="utf-8"), NEW))
    assert "FROM dbo.[BillCreditLineItem] bcli" in code
    assert "bcli.[BillCreditId] = @BillCreditId" in code
    assert "bcli.[ProjectId] IS NOT NULL" in code
    assert "dbo.[BillLineItem]" not in code


def test_bill_credit_recipient_sproc_has_no_rollback():
    seg = _segment(REVIEW_SQL.read_text(encoding="utf-8"), NEW)
    assert "ROLLBACK" not in strip_sql_comments(seg).upper()


def test_bill_credit_recipient_sproc_is_in_the_contract_header():
    lines = REVIEW_SQL.read_text(encoding="utf-8").splitlines()[:3]
    assert any(NEW in line for line in lines)


# ---------------------------------------------------------------- ingest branch


def test_ingest_bill_credit_branch_exact_predicate():
    code = _norm(INGEST_SQL.read_text(encoding="utf-8"))
    expected = (
        "OR (n.[Kind] = N'bill_credit' AND EXISTS ( SELECT 1 "
        "FROM dbo.[ReviewNotificationTarget] t "
        "JOIN dbo.[BillCredit] bc ON bc.[Id] = t.[BillCreditId] "
        "WHERE t.[ReviewNotificationId] = n.[Id] "
        "AND bc.[IsDraft] = 1 "
        "AND (SELECT TOP 1 r.[ReviewKind] FROM dbo.[Review] r "
        "WHERE r.[BillCreditId] = bc.[Id] "
        "ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC) IN (N'submitted', N'in_review')))"
    )
    assert expected in code


def test_ingest_existing_branches_are_untouched():
    code = _norm(INGEST_SQL.read_text(encoding="utf-8"))
    assert "n.[Kind] = N'bill' AND EXISTS" in code
    assert "b.[Status] IN (N'submitted', N'in_review')" in code
    assert "e.[Status] IN (N'submitted', N'in_review')" in code
    assert "c.[Status] IN (N'pending_review', N'submitted')" in code


def test_ingest_stays_nocount_go_and_rollback_free():
    raw = INGEST_SQL.read_text(encoding="utf-8")
    code = strip_sql_comments(raw)
    assert "SET NOCOUNT ON" in code
    assert raw.rstrip().endswith("GO")
    assert "ROLLBACK" not in code.upper()
