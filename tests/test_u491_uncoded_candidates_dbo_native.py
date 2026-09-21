"""U-491 — ReadUncodedCompletedExpenseCandidates uses dbo-native, parent-scoped joins.

Pure-logic / source pins + SQLite join characterization (no live DB).
"""

import re
import sqlite3

from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

EXPENSE_SQL = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"
DETECTION_SPROC = "ReadUncodedCompletedExpenseCandidates"
DELETE_CASCADE_SPROC = "DeleteExpenseCascadeById"

DETECTION_OUTER_SELECT_COLUMNS = [
    "[Id]",
    "[PublicId]",
    "[Status]",
    "[ExpenseDate]",
    "[TotalAmount]",
    "[ReferenceNumber]",
    "[QboPurchaseLineId]",
    "[CodingItemPublicId]",
]

PARENT_SCOPING_EQUALITIES = (
    ("p.[QboId] = e.[QboId]", "purchase header must bind to this expense's QboId"),
    ("p.[RealmId] = e.[RealmId]", "realm scopes expense to purchase header"),
    ("pl.[QboPurchaseId] = p.[Id]", "line must reach its parent purchase"),
    ("eli.[RealmId] = p.[RealmId]", "realm scopes line match to purchase"),
    ("eli.[ExpenseId] = e.[Id]", "line must belong to THIS expense"),
    ("pl.[QboLineId] = eli.[QboId]", "line identity from staging QboLineId"),
)


def _detection_sproc_body_stripped() -> str:
    return strip_sql_comments(sproc_body(EXPENSE_SQL, DETECTION_SPROC))


def _detection_sproc_flat() -> str:
    return " ".join(_detection_sproc_body_stripped().split())


def _uncoded_candidates_sql_for_sqlite() -> str:
    body = _detection_sproc_body_stripped()
    match = re.search(
        r";WITH Ranked AS \(.*?\)\s*SELECT\s+.*?\s+WHERE \[Rn\] = 1",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, "could not extract Ranked CTE query from detection sproc"
    sql = match.group(0)
    sql = re.sub(r"\bN'", "'", sql)
    sql = re.sub(
        r"CONVERT\s*\(\s*VARCHAR\s*\(\s*19\s*\)\s*,\s*e\.\[ExpenseDate\]\s*,\s*120\s*\)",
        "e.[ExpenseDate]",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


def _sqlite_fixture_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS dbo")
    conn.execute("ATTACH DATABASE ':memory:' AS qbo")
    conn.executescript(
        """
        CREATE TABLE dbo.Expense(
            Id INTEGER PRIMARY KEY,
            PublicId TEXT,
            Status TEXT,
            StatusOrigin TEXT,
            ExpenseDate TEXT,
            TotalAmount REAL,
            ReferenceNumber TEXT,
            QboId TEXT,
            RealmId TEXT
        );
        CREATE TABLE dbo.ExpenseLineItem(
            Id INTEGER PRIMARY KEY,
            ExpenseId INTEGER,
            QboId TEXT,
            RealmId TEXT
        );
        CREATE TABLE dbo.ExpenseCodingItem(
            Id INTEGER PRIMARY KEY,
            PublicId TEXT,
            QboPurchaseLineId INTEGER
        );
        CREATE TABLE qbo.Purchase(
            Id INTEGER PRIMARY KEY,
            QboId TEXT,
            RealmId TEXT
        );
        CREATE TABLE qbo.PurchaseLine(
            Id INTEGER PRIMARY KEY,
            QboPurchaseId INTEGER,
            QboLineId TEXT,
            AccountRefName TEXT,
            ItemRefValue TEXT
        );
        """
    )
    return conn


def _insert_uncoded_pair_fixture(conn: sqlite3.Connection) -> None:
    """Two purchases each with QboLineId='1'; only parent scope keeps lines distinct."""
    conn.executescript(
        """
        INSERT INTO dbo.Expense VALUES
          (1, 'exp-pub-1', 'completed', 'qbo_pull', '2026-01-01', 10.0, 'REF-A', 'PUR-A', 'realm-1'),
          (2, 'exp-pub-2', 'completed', 'qbo_pull', '2026-01-02', 20.0, 'REF-B', 'PUR-B', 'realm-1');
        INSERT INTO dbo.ExpenseLineItem VALUES
          (101, 1, '1', 'realm-1'),
          (102, 2, '1', 'realm-1');
        INSERT INTO qbo.Purchase VALUES
          (201, 'PUR-A', 'realm-1'),
          (202, 'PUR-B', 'realm-1');
        INSERT INTO qbo.PurchaseLine VALUES
          (301, 201, '1', 'Cost of construction : NEED TO CATEGORIZE', NULL),
          (302, 202, '1', 'Cost of construction : NEED TO CATEGORIZE', NULL);
        """
    )


def _rows_for_expense(conn: sqlite3.Connection, expense_id: int) -> list[sqlite3.Row]:
    sql = _uncoded_candidates_sql_for_sqlite()
    rows = conn.execute(sql).fetchall()
    return [r for r in rows if r["Id"] == expense_id]


# ---------------------------------------------------------------------------
# 1 — map table absent from the resolution path
# ---------------------------------------------------------------------------


def test_detection_sproc_does_not_reference_map_table():
    body = _detection_sproc_body_stripped()
    # PurchaseLineExpenseLineItem still appears in DeleteExpenseCascadeById (spec 10);
    # pin only the detection sproc body extracted above.
    assert "PurchaseLineExpenseLineItem" not in body


# ---------------------------------------------------------------------------
# 2–5 — parent-scoping equalities (pin strings, not token presence)
# ---------------------------------------------------------------------------


def test_detection_sproc_parent_scoping_equalities():
    flat = _detection_sproc_flat()
    body = _detection_sproc_body_stripped()
    # [RealmId] appears 3x in this sproc — token presence is vacuous for legs.
    assert body.count("[RealmId]") >= 3
    assert body.count("[ExpenseId]") == 1
    for leg, why in PARENT_SCOPING_EQUALITIES:
        assert leg in flat, f"missing `{leg}` ({why})"


# ---------------------------------------------------------------------------
# 6 — cross-purchase QboLineId collision
# ---------------------------------------------------------------------------


def test_detection_parent_scoped_not_cross_purchase():
    conn = _sqlite_fixture_conn()
    _insert_uncoded_pair_fixture(conn)
    sql = _uncoded_candidates_sql_for_sqlite()
    rows = conn.execute(sql).fetchall()
    assert len(rows) == 2
    by_id = {r["Id"]: r for r in rows}
    assert by_id[1]["QboPurchaseLineId"] == 301
    assert by_id[1]["ReferenceNumber"] == "REF-A"
    # Unscoped line-id-only joins bind expense 2 to purchase A's line (301), not 302.
    assert by_id[2]["QboPurchaseLineId"] == 302
    assert by_id[2]["ReferenceNumber"] == "REF-B"


# ---------------------------------------------------------------------------
# 7 — no map row still resolves (prod bug: map retired → 0 rows)
# ---------------------------------------------------------------------------


def test_detection_resolves_without_map_row():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.Expense VALUES
          (1, 'exp-pub-1', 'completed', 'qbo_pull', '2026-01-01', 10.0, 'REF-A', 'PUR-A', 'realm-1');
        INSERT INTO dbo.ExpenseLineItem VALUES (101, 1, '1', 'realm-1');
        INSERT INTO qbo.Purchase VALUES (201, 'PUR-A', 'realm-1');
        INSERT INTO qbo.PurchaseLine VALUES (
          301, 201, '1', 'Cost of construction : NEED TO CATEGORIZE', NULL
        );
        """
    )
    rows = _rows_for_expense(conn, 1)
    assert len(rows) == 1
    assert rows[0]["QboPurchaseLineId"] == 301


# ---------------------------------------------------------------------------
# 8 — 8-column outer SELECT list unchanged
# ---------------------------------------------------------------------------


def test_detection_sproc_outer_select_list_unchanged():
    body = _detection_sproc_body_stripped()
    match = re.search(
        r"\)\s*SELECT\s+(.*?)\s+FROM Ranked\s+WHERE \[Rn\] = 1",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert match is not None
    cols = [c.strip() for c in match.group(1).split(",")]
    assert cols == DETECTION_OUTER_SELECT_COLUMNS


# ---------------------------------------------------------------------------
# 9 — single row per expense via ROW_NUMBER / Rn = 1
# ---------------------------------------------------------------------------


def test_detection_sproc_single_row_per_expense_ranking():
    body = _detection_sproc_body_stripped()
    assert re.search(
        r"ROW_NUMBER\(\)\s+OVER\s*\(\s*PARTITION BY e\.\[Id\]\s+ORDER BY eli\.\[Id\]\s*\)\s+AS \[Rn\]",
        body,
        re.IGNORECASE,
    )
    assert re.search(r"WHERE \[Rn\] = 1", body, re.IGNORECASE)


# ---------------------------------------------------------------------------
# 10 — DeleteExpenseCascadeById map-table bridge survives tidying
# ---------------------------------------------------------------------------


def test_delete_expense_cascade_still_guards_map_table_bridge():
    full_file = EXPENSE_SQL.read_text()
    body = strip_sql_comments(sproc_body(EXPENSE_SQL, DELETE_CASCADE_SPROC))
    assert "PurchaseLineExpenseLineItem" in body
    assert re.search(
        r"IF\s+OBJECT_ID\s*\(\s*'qbo\.PurchaseLineExpenseLineItem'\s*\)\s+IS\s+NOT\s+NULL",
        body,
        re.IGNORECASE,
    )
    assert re.search(
        r"DELETE FROM qbo\.\[PurchaseLineExpenseLineItem\]",
        body,
        re.IGNORECASE,
    )
    # Detection sproc must not be the only remaining mention — cascade still owns FK cleanup.
    assert full_file.count("PurchaseLineExpenseLineItem") >= 2
