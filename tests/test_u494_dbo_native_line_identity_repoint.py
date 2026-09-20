"""U-494 — repoint map-table readers onto dbo-native, parent-scoped line identity.

Pure-logic / source pins + SQLite join characterization (no live DB).
"""

import re
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from entities.expense.business.service import ExpenseService
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

EXPENSE_SERVICE_PY = REPO_ROOT / "entities/expense/business/service.py"
EXPENSE_CODING_SQL = REPO_ROOT / "entities/expense_coding_item/sql/dbo.expense_coding_item.sql"
READ_STATE_SPROC = "ReadExpenseCodingStateByExpenseIds"

PRE_U490_EXPENSE_CODING_SPROC_NAMES = frozenset(
    {
        "UpsertExpenseCodingItem",
        "ReadExpenseCodingItemByPublicId",
        "RecordExpenseCodingSuggestion",
        "RecordExpenseCodingFlag",
        "ClaimExpenseCodingItem",
        "ReleaseExpenseCodingItem",
        "RecordExpenseCodingConfirmation",
        "MarkExpenseCodingEnqueued",
        "MarkExpenseCodingWritten",
        "MarkExpenseCodingChangedInQbo",
        "MarkExpenseCodingError",
        "ReadExpenseCodingStateByExpenseIds",
        "ReadExternallyResolvedCodingItemCandidates",
        "MarkExpenseCodingResolvedExternally",
    }
)

READ_STATE_SELECT_COLUMNS = [
    "eli.[ExpenseId]",
    "eci.[PublicId] AS [CodingItemPublicId]",
    "eci.[Status]",
    "eci.[SuggestionConfidence]",
    "eci.[SuggestedProjectId]",
    "eci.[SuggestedSubCostCodeId]",
    "eci.[ConfirmedProjectId]",
    "eci.[ConfirmedSubCostCodeId]",
    "eci.[FlagReason]",
]

ENQUEUE_LINE_SELECT_COLUMNS = [
    "eci.[PublicId]",
    "eli.[ProjectId]",
    "eli.[SubCostCodeId]",
    "eli.[Description]",
]


def _sproc_names_in_file(path: Path) -> set[str]:
    text = path.read_text()
    return set(
        re.findall(
            r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?(\w+)",
            text,
            re.IGNORECASE,
        )
    )


def _execute_sql_in_function(source: str, func_name: str) -> str:
    match = re.search(
        rf"def {func_name}\b.*?cursor\.execute\(\s*\"\"\"\s*\n(.*?)\"\"\"",
        source,
        re.DOTALL,
    )
    assert match is not None, f"no cursor.execute SQL in {func_name}"
    return match.group(1)


def _enqueue_recode_line_sql() -> str:
    return _execute_sql_in_function(
        EXPENSE_SERVICE_PY.read_text(), "enqueue_coding_recode_on_submit"
    )


def _payment_type_line_sql() -> str:
    return _execute_sql_in_function(
        EXPENSE_SERVICE_PY.read_text(), "_read_qbo_purchase_payment_type"
    )


def _payment_type_line_sql_for_sqlite() -> str:
    return re.sub(
        r"SELECT\s+TOP\s+1\s+",
        "SELECT ",
        _payment_type_line_sql(),
        flags=re.IGNORECASE,
    )


def _read_state_join_sql() -> str:
    """Sproc SELECT with STRING_SPLIT replaced by a single expense-id filter."""
    body = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC))
    select_match = re.search(
        r"(SELECT\s+.*?FROM\s+dbo\.\[ExpenseCodingItem\].*?)(?:\s+WHERE\s+)",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert select_match is not None
    from_clause = select_match.group(1)
    from_clause = re.sub(
        r"\s+INNER JOIN STRING_SPLIT\(.*?\)\s+s\s+ON\s+s\.value\s*<>\s*''\s+AND\s+eli\.\[ExpenseId\]\s*=\s*TRY_CAST\(LTRIM\(RTRIM\(s\.value\)\)\s+AS\s+BIGINT\)",
        "",
        from_clause,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return from_clause + "\n    WHERE eli.[ExpenseId] = ?"


def _sqlite_fixture_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS dbo")
    conn.execute("ATTACH DATABASE ':memory:' AS qbo")
    conn.executescript(
        """
        CREATE TABLE dbo.Expense(
            Id INTEGER PRIMARY KEY,
            QboId TEXT,
            RealmId TEXT
        );
        CREATE TABLE dbo.ExpenseLineItem(
            Id INTEGER PRIMARY KEY,
            ExpenseId INTEGER,
            QboId TEXT,
            RealmId TEXT,
            ProjectId INTEGER,
            SubCostCodeId INTEGER,
            Description TEXT
        );
        CREATE TABLE dbo.ExpenseCodingItem(
            Id INTEGER PRIMARY KEY,
            PublicId TEXT,
            QboPurchaseLineId INTEGER,
            Status TEXT,
            SuggestionConfidence REAL,
            SuggestedProjectId INTEGER,
            SuggestedSubCostCodeId INTEGER,
            ConfirmedProjectId INTEGER,
            ConfirmedSubCostCodeId INTEGER,
            FlagReason TEXT
        );
        CREATE TABLE qbo.Purchase(
            Id INTEGER PRIMARY KEY,
            QboId TEXT,
            RealmId TEXT,
            PaymentType TEXT
        );
        CREATE TABLE qbo.PurchaseLine(
            Id INTEGER PRIMARY KEY,
            QboPurchaseId INTEGER,
            QboLineId TEXT
        );
        CREATE TABLE qbo.PurchaseLineExpenseLineItem(
            Id INTEGER PRIMARY KEY,
            ExpenseLineItemId INTEGER,
            QboPurchaseLineId INTEGER
        );
        """
    )
    return conn


def _run_sql(conn: sqlite3.Connection, sql: str, params=()):
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# 1 — no map row still resolves (all three consumers)
# ---------------------------------------------------------------------------


def test_enqueue_recode_resolves_line_without_map_row():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.Expense VALUES (1, 'PUR-A', 'realm-1');
        INSERT INTO dbo.ExpenseLineItem VALUES (101, 1, '1', 'realm-1', 77, 481, 'Fuel');
        INSERT INTO qbo.Purchase VALUES (201, 'PUR-A', 'realm-1', 'CreditCard');
        INSERT INTO qbo.PurchaseLine VALUES (301, 201, '1');
        INSERT INTO dbo.ExpenseCodingItem VALUES (
            1, 'ci-pub-1', 301, 'pending', NULL, NULL, NULL, NULL, NULL, NULL
        );
        """
    )
    rows = _run_sql(conn, _enqueue_recode_line_sql(), (1,))
    assert len(rows) == 1
    assert rows[0]["PublicId"] == "ci-pub-1"
    assert rows[0]["ProjectId"] == 77


def test_payment_type_resolves_without_map_row():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.Expense VALUES (1, 'PUR-A', 'realm-1');
        INSERT INTO dbo.ExpenseLineItem VALUES (101, 1, '1', 'realm-1', NULL, NULL, NULL);
        INSERT INTO qbo.Purchase VALUES (201, 'PUR-A', 'realm-1', 'Check');
        INSERT INTO qbo.PurchaseLine VALUES (301, 201, '1');
        """
    )
    rows = _run_sql(conn, _payment_type_line_sql_for_sqlite(), (1,))
    assert len(rows) == 1
    assert rows[0][0] == "Check"


def test_read_state_sproc_resolves_without_map_row():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.Expense VALUES (1, 'PUR-A', 'realm-1');
        INSERT INTO dbo.ExpenseLineItem VALUES (101, 1, '1', 'realm-1', NULL, NULL, NULL);
        INSERT INTO qbo.Purchase VALUES (201, 'PUR-A', 'realm-1', 'CreditCard');
        INSERT INTO qbo.PurchaseLine VALUES (301, 201, '1');
        INSERT INTO dbo.ExpenseCodingItem VALUES (
            1, 'ci-pub-1', 301, 'pending', NULL, 10, 20, NULL, NULL, NULL
        );
        """
    )
    rows = _run_sql(conn, _read_state_join_sql(), (1,))
    assert len(rows) == 1
    assert rows[0]["CodingItemPublicId"] == "ci-pub-1"


# ---------------------------------------------------------------------------
# 2 — cross-purchase QboLineId collision must not match wrong expense
# ---------------------------------------------------------------------------


def test_enqueue_recode_parent_scoped_not_cross_purchase():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.Expense VALUES (1, 'PUR-A', 'realm-1'), (2, 'PUR-B', 'realm-1');
        INSERT INTO dbo.ExpenseLineItem VALUES
          (101, 1, '1', 'realm-1', 77, 481, 'A line'),
          (102, 2, '1', 'realm-1', 99, 999, 'B line');
        INSERT INTO qbo.Purchase VALUES (201, 'PUR-A', 'realm-1', 'CreditCard'),
                                        (202, 'PUR-B', 'realm-1', 'CreditCard');
        INSERT INTO qbo.PurchaseLine VALUES (301, 201, '1'), (302, 202, '1');
        INSERT INTO dbo.ExpenseCodingItem VALUES (
            1, 'ci-pub-a', 301, 'pending', NULL, NULL, NULL, NULL, NULL, NULL
        );
        """
    )
    rows = _run_sql(conn, _enqueue_recode_line_sql(), (1,))
    assert len(rows) == 1
    assert rows[0]["ProjectId"] == 77
    assert rows[0]["SubCostCodeId"] == 481


def test_payment_type_parent_scoped_not_cross_purchase():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.Expense VALUES (1, 'PUR-A', 'realm-1'), (2, 'PUR-B', 'realm-1');
        INSERT INTO dbo.ExpenseLineItem VALUES
          (101, 1, '1', 'realm-1', NULL, NULL, NULL),
          (102, 2, '1', 'realm-1', NULL, NULL, NULL);
        INSERT INTO qbo.Purchase VALUES (201, 'PUR-A', 'realm-1', 'Check'),
                                        (202, 'PUR-B', 'realm-1', 'Cash');
        INSERT INTO qbo.PurchaseLine VALUES (301, 201, '1'), (302, 202, '1');
        """
    )
    rows = _run_sql(conn, _payment_type_line_sql_for_sqlite(), (1,))
    assert len(rows) == 1
    assert rows[0][0] == "Check"


def test_read_state_parent_scoped_not_cross_purchase():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.Expense VALUES (1, 'PUR-A', 'realm-1'), (2, 'PUR-B', 'realm-1');
        INSERT INTO dbo.ExpenseLineItem VALUES
          (101, 1, '1', 'realm-1', NULL, NULL, NULL),
          (102, 2, '1', 'realm-1', NULL, NULL, NULL);
        INSERT INTO qbo.Purchase VALUES (201, 'PUR-A', 'realm-1', 'CreditCard'),
                                        (202, 'PUR-B', 'realm-1', 'CreditCard');
        INSERT INTO qbo.PurchaseLine VALUES (301, 201, '1'), (302, 202, '1');
        INSERT INTO dbo.ExpenseCodingItem VALUES (
            1, 'ci-pub-a', 301, 'pending', NULL, 10, 20, NULL, NULL, NULL
        );
        """
    )
    rows = _run_sql(conn, _read_state_join_sql(), (1,))
    assert len(rows) == 1
    assert rows[0]["ExpenseId"] == 1
    assert rows[0]["SuggestedProjectId"] == 10


# ---------------------------------------------------------------------------
# 3 — parent scoping pins (expense↔purchase QboId+RealmId, eli.ExpenseId = e.Id)
# ---------------------------------------------------------------------------


def _assert_parent_scoped_join(sql: str, *, label: str) -> None:
    normalized = strip_sql_comments(sql)
    qbo_pair = (
        r"e\.\[QboId\]\s*=\s*p\.\[QboId\]|p\.\[QboId\]\s*=\s*e\.\[QboId\]"
    )
    realm_pair = (
        r"e\.\[RealmId\]\s*=\s*p\.\[RealmId\]|p\.\[RealmId\]\s*=\s*e\.\[RealmId\]"
    )
    assert re.search(qbo_pair, normalized, re.IGNORECASE), (
        f"{label}: missing expense↔purchase QboId pair"
    )
    assert re.search(realm_pair, normalized, re.IGNORECASE), (
        f"{label}: missing expense↔purchase RealmId pair"
    )
    assert re.search(
        r"eli\.\[ExpenseId\]\s*=\s*e\.\[Id\]",
        normalized,
        re.IGNORECASE,
    ), f"{label}: missing eli.ExpenseId = e.Id"
    line_pair = (
        r"eli\.\[QboId\]\s*=\s*pl\.\[QboLineId\]|pl\.\[QboLineId\]\s*=\s*eli\.\[QboId\]"
    )
    assert re.search(line_pair, normalized, re.IGNORECASE), (
        f"{label}: missing eli.QboId ↔ pl.QboLineId pair"
    )


def test_parent_scoping_present_in_enqueue_recode_sql():
    _assert_parent_scoped_join(_enqueue_recode_line_sql(), label="enqueue_coding_recode")


def test_parent_scoping_present_in_payment_type_sql():
    _assert_parent_scoped_join(_payment_type_line_sql(), label="_read_qbo_purchase_payment_type")


def test_parent_scoping_present_in_read_state_sproc():
    body = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC))
    _assert_parent_scoped_join(body, label=READ_STATE_SPROC)


# ---------------------------------------------------------------------------
# 4 — map table no longer in resolution paths
# ---------------------------------------------------------------------------


def test_enqueue_recode_sql_does_not_reference_map_table():
    assert "PurchaseLineExpenseLineItem" not in _enqueue_recode_line_sql()


def test_payment_type_sql_does_not_reference_map_table():
    assert "PurchaseLineExpenseLineItem" not in _payment_type_line_sql()


def test_read_state_sproc_does_not_reference_map_table():
    body = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC))
    assert "PurchaseLineExpenseLineItem" not in body


# ---------------------------------------------------------------------------
# 5 — result shape unchanged
# ---------------------------------------------------------------------------


def test_read_state_sproc_select_list_unchanged():
    body = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, READ_STATE_SPROC))
    select_match = re.search(r"SELECT\s+(.*?)\s+FROM", body, re.DOTALL | re.IGNORECASE)
    assert select_match is not None
    select_list = re.sub(r"\s+", " ", select_match.group(1).strip())
    expected = ", ".join(READ_STATE_SELECT_COLUMNS)
    assert select_list.replace(" ", "") == expected.replace(" ", "")


def test_enqueue_recode_select_list_unchanged():
    sql = _enqueue_recode_line_sql()
    select_match = re.search(r"SELECT\s+(.*?)\s+FROM", sql, re.DOTALL | re.IGNORECASE)
    assert select_match is not None
    select_list = re.sub(r"\s+", " ", select_match.group(1).strip())
    expected = ", ".join(ENQUEUE_LINE_SELECT_COLUMNS)
    assert select_list.replace(" ", "") == expected.replace(" ", "")


def test_payment_type_still_returns_top_payment_type():
    assert re.search(r"SELECT\s+TOP\s+1\s+p\.\[PaymentType\]", _payment_type_line_sql(), re.I)


def test_enqueue_recode_on_submit_still_returns_summary_dict():
    svc = ExpenseService()
    with patch(
        "entities.expense_coding_item.persistence.repo.ExpenseCodingItemRepository.read_state_by_expense_ids",
        return_value={},
    ):
        summary = svc.enqueue_coding_recode_on_submit(expense_id=1, user_id=17)
    assert set(summary.keys()) == {"enqueued", "skipped", "invalid", "writes_disabled", "items"}


# ---------------------------------------------------------------------------
# 6 — no other sproc in expense_coding_item.sql added or removed
# ---------------------------------------------------------------------------


def test_expense_coding_sql_sproc_inventory_unchanged():
    assert _sproc_names_in_file(EXPENSE_CODING_SQL) == PRE_U490_EXPENSE_CODING_SPROC_NAMES
