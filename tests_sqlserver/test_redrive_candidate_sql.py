"""U-652a re-drive script: every generated candidate query must be VALID SQL and select the right
Expenses on a real SQL Server (the pure-logic tests mock the database and cannot see SQL validity).

Runs only with BUILDONE_TEST_SQLSERVER (localhost guard + schema from test_u652_coding_suggestion_sql).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from tests_sqlserver import test_u652_coding_suggestion_sql as u652_sql
from tests_sqlserver.test_u652_coding_suggestion_sql import _reset, conn  # noqa: F401  (fixtures)

pytestmark = u652_sql.pytestmark

UNCAT = "Cost of construction : NEED TO CATEGORIZE"


def _ensure_review_and_outbox(cur) -> None:
    cur.execute(
        """
        IF OBJECT_ID('dbo.Review', 'U') IS NULL
            CREATE TABLE dbo.Review(Id BIGINT IDENTITY(1,1) PRIMARY KEY, ExpenseId BIGINT NULL);
        IF OBJECT_ID('qbo.Outbox', 'U') IS NULL
            CREATE TABLE qbo.Outbox(
                Id BIGINT IDENTITY(1,1) PRIMARY KEY,
                Kind NVARCHAR(64) NOT NULL,
                EntityPublicId UNIQUEIDENTIFIER NOT NULL
            );
        DELETE FROM dbo.Review;
        DELETE FROM qbo.Outbox;
        """
    )


def _expense(cur, *, qbo_id: str, status: str, reviewed: bool = True, item_ref=None) -> int:
    pid = cur.execute(
        "INSERT INTO qbo.Purchase (QboId, RealmId) OUTPUT INSERTED.Id VALUES (?, N'realm-1');", (qbo_id,)
    ).fetchval()
    cur.execute(
        "INSERT INTO qbo.PurchaseLine (QboPurchaseId, QboLineId, LineNum, AccountRefName, ItemRefValue)"
        " VALUES (?, N'1', 1, ?, ?);",
        (pid, UNCAT, item_ref),
    )
    eid = cur.execute(
        "INSERT INTO dbo.Expense (QboId, RealmId, Status) OUTPUT INSERTED.Id VALUES (?, N'realm-1', ?);",
        (qbo_id, status),
    ).fetchval()
    if reviewed:
        cur.execute("INSERT INTO dbo.Review (ExpenseId) VALUES (?);", (eid,))
    return eid


def _suggestion(cur, expense_id: int, qbo_id: str, *, claimed: bool = False, enqueued: bool = False) -> None:
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (PublicId, TargetKind, ExpenseId, QboPurchaseQboId, RealmId, Status,
                                          ClaimedByUserId, ClaimedAt)
        VALUES (?, N'expense', ?, ?, N'realm-1', N'pending', ?, CASE WHEN ? = 1 THEN SYSUTCDATETIME() END);
        """,
        (pub, expense_id, qbo_id, 17 if claimed else None, 1 if claimed else 0),
    )
    if enqueued:
        cur.execute("INSERT INTO qbo.Outbox (Kind, EntityPublicId) VALUES (N'recode_purchase_line', ?);", (pub,))


@pytest.fixture
def seeded(conn):  # noqa: F811
    cur = conn.cursor()
    _ensure_review_and_outbox(cur)
    ids = {
        "submitted_missing": _expense(cur, qbo_id="R-1", status="submitted"),
        "approved_missing": _expense(cur, qbo_id="R-2", status="approved"),
        "declined_missing": _expense(cur, qbo_id="R-3", status="declined"),
        "draft_missing": _expense(cur, qbo_id="R-4", status="draft"),
        "not_reviewed": _expense(cur, qbo_id="R-5", status="completed", reviewed=False),
        "item_recoded": _expense(cur, qbo_id="R-6", status="completed", item_ref="ITEM-1"),
        "seeded_free": _expense(cur, qbo_id="R-7", status="completed"),
        "seeded_claimed": _expense(cur, qbo_id="R-8", status="completed"),
        "seeded_enqueued": _expense(cur, qbo_id="R-9", status="in_review"),
    }
    _suggestion(cur, ids["seeded_free"], "R-7")
    _suggestion(cur, ids["seeded_claimed"], "R-8", claimed=True)
    _suggestion(cur, ids["seeded_enqueued"], "R-9", enqueued=True)
    conn.commit()
    yield ids
    cur.execute("DELETE FROM dbo.Review; DELETE FROM qbo.Outbox;")
    conn.commit()


@contextmanager
def _use(conn):  # noqa: F811
    @contextmanager
    def _get_connection():
        yield conn

    with patch("scripts.redrive_skipped_expense_recodes.get_connection", _get_connection):
        yield


def _ids(rows) -> list[int]:
    return [int(r.Id) for r in rows]


def test_default_candidates_are_the_missing_arm_in_eligible_statuses(conn, seeded):  # noqa: F811
    from scripts.redrive_skipped_expense_recodes import fetch_candidates

    with _use(conn):
        got = _ids(fetch_candidates())
    assert got == sorted([seeded["submitted_missing"], seeded["approved_missing"]])


def test_expense_id_filter_is_valid_sql_and_still_requires_candidacy(conn, seeded):  # noqa: F811
    from scripts.redrive_skipped_expense_recodes import fetch_candidates

    with _use(conn):
        assert _ids(fetch_candidates(expense_ids=[seeded["approved_missing"]])) == [seeded["approved_missing"]]
        assert _ids(fetch_candidates(expense_ids=[seeded["declined_missing"]])) == []
        assert _ids(fetch_candidates(expense_ids=[seeded["submitted_missing"], seeded["item_recoded"]])) == [
            seeded["submitted_missing"]
        ]


def test_resume_filter_is_valid_sql_and_skips_claimed_or_enqueued(conn, seeded):  # noqa: F811
    from scripts.redrive_skipped_expense_recodes import fetch_candidates

    resume = [seeded["seeded_free"], seeded["seeded_claimed"], seeded["seeded_enqueued"]]
    with _use(conn):
        got = _ids(fetch_candidates(resume_expense_ids=resume))
    assert seeded["seeded_free"] in got
    assert seeded["seeded_claimed"] not in got
    assert seeded["seeded_enqueued"] not in got


def test_single_id_recheck_used_by_apply_is_valid_sql(conn, seeded):  # noqa: F811
    """--apply re-checks one id right before enqueueing; that query must run, for both arms."""
    from scripts.redrive_skipped_expense_recodes import fetch_candidates

    with _use(conn):
        assert _ids(fetch_candidates(expense_ids=[seeded["submitted_missing"]])) == [seeded["submitted_missing"]]
        resumed = fetch_candidates(expense_ids=[seeded["seeded_free"]], resume_expense_ids=[seeded["seeded_free"]])
    assert seeded["seeded_free"] in _ids(resumed)
