"""U-655c: ReadBudgetVarianceByProjectId @SubCostCodeId filter on real SQL Server."""
from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path

import pytest

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}


def _server_host(conn_str: str) -> str:
    for part in conn_str.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() == "SERVER":
            host = re.sub(r"^tcp:", "", value.strip().strip("{}"), flags=re.IGNORECASE)
            return host.split(",")[0].strip().lower()
    return ""


if os.environ.get("BUILDONE_TEST_SQLSERVER") and _server_host(
    os.environ["BUILDONE_TEST_SQLSERVER"]
) not in _LOCAL_SERVERS:
    raise RuntimeError(
        "tests_sqlserver refuses to run: SERVER in BUILDONE_TEST_SQLSERVER must be 127.0.0.1 or localhost"
    )

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VARIANCE_SQL = REPO_ROOT / "entities/budget/sql/dbo.budget_variance.sql"
ACCESS_UDFS_SQL = REPO_ROOT / "shared/sql/dbo.access_udfs.sql"

PROJECT_ID = 9001
BUDGET_ID = 1
COST_CODE_ID = 50
SCC_A, SCC_B, SCC_C = 101, 102, 103
SCC_ACTUAL_ONLY = 104
SCC_DRAW_ONLY = 105
MISSING_SCC = 99999

ACTOR_ALLOWED = 2001
ACTOR_DENIED = 2002
PROJECT_CREATOR = 2000

_DRAWN_WHERE_FILTER = (
    "          AND (@SubCostCodeId IS NULL\n"
    "               OR ISNULL(COALESCE(ili.[SubCostCodeId], bli.[SubCostCodeId], eli.[SubCostCodeId],\n"
    "                          bcli.[SubCostCodeId], elli.[SubCostCodeId]), -1) = @SubCostCodeId)"
)
_DRAWN_WHERE_FILTER_BROKEN = (
    "          AND (@SubCostCodeId IS NULL\n"
    "               OR ISNULL(ili.[SubCostCodeId], -1) = @SubCostCodeId)"
)


def _batches(sql: str) -> list[str]:
    out, cur = [], []
    for line in sql.splitlines():
        if line.strip() == "GO":
            out.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    out.append("\n".join(cur))
    return [b for b in out if any(l.strip() and not l.strip().startswith("--") for l in b.splitlines())]


def _exec_batches(cur, path: Path) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        cur.execute(batch)


def _install_user_can_access_project(cur) -> None:
    for batch in _batches(ACCESS_UDFS_SQL.read_text(encoding="utf-8")):
        if "CREATE OR ALTER FUNCTION dbo.UserCanAccessProject" in batch:
            cur.execute(batch)
            return
    raise RuntimeError("UserCanAccessProject batch not found in access_udfs.sql")


def _install_variance_proc(cur, *, sql_text: str | None = None) -> None:
    text = sql_text if sql_text is not None else VARIANCE_SQL.read_text(encoding="utf-8")
    for batch in _batches(text):
        if "ReadBudgetVarianceByProjectId" in batch or "ReadBudgetListRollups" in batch:
            cur.execute(batch)


def _install_broken_draw_coalesce_filter(cur) -> None:
    text = VARIANCE_SQL.read_text(encoding="utf-8")
    if _DRAWN_WHERE_FILTER not in text:
        raise RuntimeError("expected DrawnAgg WHERE filter not found in variance SQL")
    broken = text.replace(_DRAWN_WHERE_FILTER, _DRAWN_WHERE_FILTER_BROKEN)
    cur.execute(
        "IF OBJECT_ID('dbo.ReadBudgetVarianceByProjectId', 'P') IS NOT NULL "
        "DROP PROCEDURE dbo.ReadBudgetVarianceByProjectId"
    )
    _install_variance_proc(cur, sql_text=broken)


def _install_broken_access_gate(cur) -> None:
    text = VARIANCE_SQL.read_text(encoding="utf-8")
    if "WHERE @CanAccess = 1" not in text:
        raise RuntimeError("expected access gate WHERE not found in variance SQL")
    broken = text.replace("WHERE @CanAccess = 1", "WHERE 1 = 1")
    cur.execute(
        "IF OBJECT_ID('dbo.ReadBudgetVarianceByProjectId', 'P') IS NOT NULL "
        "DROP PROCEDURE dbo.ReadBudgetVarianceByProjectId"
    )
    _install_variance_proc(cur, sql_text=broken)


def _drop_all(cur) -> None:
    cur.execute(
        "IF OBJECT_ID('dbo.ReadBudgetVarianceByProjectId', 'P') IS NOT NULL "
        "DROP PROCEDURE dbo.ReadBudgetVarianceByProjectId"
    )
    cur.execute(
        "IF OBJECT_ID('dbo.ReadBudgetListRollups', 'P') IS NOT NULL "
        "DROP PROCEDURE dbo.ReadBudgetListRollups"
    )
    cur.execute(
        "IF OBJECT_ID('dbo.UserCanAccessProject', 'FN') IS NOT NULL "
        "DROP FUNCTION dbo.UserCanAccessProject"
    )
    for tbl in (
        "dbo.UserProject",
        "dbo.Project",
        "dbo.InvoiceLineItem",
        "dbo.Invoice",
        "dbo.BillLineItem",
        "dbo.BillCreditLineItem",
        "dbo.ContractLaborLineItem",
        "dbo.EmployeeLaborLineItem",
        "dbo.ExpenseLineItem",
        "dbo.Expense",
        "dbo.BudgetLineItem",
        "dbo.BudgetRevision",
        "dbo.Budget",
        "dbo.SubCostCode",
        "dbo.CostCode",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.CostCode (
            Id BIGINT NOT NULL PRIMARY KEY,
            Number NVARCHAR(20) NOT NULL,
            Name NVARCHAR(255) NOT NULL
        );
        CREATE TABLE dbo.SubCostCode (
            Id BIGINT NOT NULL PRIMARY KEY,
            Number NVARCHAR(20) NOT NULL,
            Name NVARCHAR(255) NOT NULL,
            CostCodeId BIGINT NOT NULL
        );
        CREATE TABLE dbo.Project (
            Id BIGINT NOT NULL PRIMARY KEY,
            CreatedByUserId BIGINT NOT NULL
        );
        CREATE TABLE dbo.UserProject (
            UserId BIGINT NOT NULL,
            ProjectId BIGINT NOT NULL,
            PRIMARY KEY (UserId, ProjectId)
        );
        CREATE TABLE dbo.Budget (
            Id BIGINT NOT NULL PRIMARY KEY,
            ProjectId BIGINT NOT NULL,
            Status NVARCHAR(20) NOT NULL
        );
        CREATE TABLE dbo.BudgetRevision (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BudgetId BIGINT NOT NULL,
            Status NVARCHAR(20) NOT NULL
        );
        CREATE TABLE dbo.BudgetLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BudgetRevisionId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            Amount DECIMAL(18,2) NULL,
            Price DECIMAL(18,2) NULL
        );
        CREATE TABLE dbo.Expense (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            IsCredit BIT NOT NULL DEFAULT 0
        );
        CREATE TABLE dbo.ExpenseLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ExpenseId BIGINT NOT NULL,
            ProjectId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            Amount DECIMAL(18,2) NULL
        );
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ProjectId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            Amount DECIMAL(18,2) NULL
        );
        CREATE TABLE dbo.BillCreditLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ProjectId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            Amount DECIMAL(18,2) NULL
        );
        CREATE TABLE dbo.ContractLaborLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ProjectId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            Price DECIMAL(18,2) NULL,
            Markup DECIMAL(18,4) NULL,
            Hours DECIMAL(18,2) NULL,
            IsOverhead BIT NOT NULL DEFAULT 0,
            BillLineItemId BIGINT NULL,
            IsBillable BIT NOT NULL DEFAULT 1
        );
        CREATE TABLE dbo.EmployeeLaborLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ProjectId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            Hours DECIMAL(18,2) NULL,
            Rate DECIMAL(18,2) NULL,
            IsOverhead BIT NOT NULL DEFAULT 0
        );
        CREATE TABLE dbo.Invoice (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ProjectId BIGINT NOT NULL,
            IsDraft BIT NOT NULL DEFAULT 0
        );
        CREATE TABLE dbo.InvoiceLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            InvoiceId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            BillLineItemId BIGINT NULL,
            ExpenseLineItemId BIGINT NULL,
            BillCreditLineItemId BIGINT NULL,
            EmployeeLaborLineItemId BIGINT NULL,
            Amount DECIMAL(18,2) NULL,
            Price DECIMAL(18,2) NULL
        );
        """
    )
    cur.execute(
        "INSERT INTO dbo.Project (Id, CreatedByUserId) VALUES (?, ?)",
        PROJECT_ID,
        PROJECT_CREATOR,
    )
    cur.execute(
        "INSERT INTO dbo.UserProject (UserId, ProjectId) VALUES (?, ?)",
        ACTOR_ALLOWED,
        PROJECT_ID,
    )
    _install_user_can_access_project(cur)
    _exec_batches(cur, VARIANCE_SQL)


def _seed_fixtures(cur) -> None:
    cur.execute(
        "INSERT INTO dbo.CostCode (Id, Number, Name) VALUES (?, '01', 'General')",
        COST_CODE_ID,
    )
    scc_rows = (
        (SCC_A, "A"),
        (SCC_B, "B"),
        (SCC_C, "C"),
        (SCC_ACTUAL_ONLY, "D"),
        (SCC_DRAW_ONLY, "E"),
    )
    for scc_id, num in scc_rows:
        cur.execute(
            """
            INSERT INTO dbo.SubCostCode (Id, Number, Name, CostCodeId)
            VALUES (?, ?, ?, ?)
            """,
            scc_id,
            num,
            f"SCC {num}",
            COST_CODE_ID,
        )

    cur.execute(
        "INSERT INTO dbo.Budget (Id, ProjectId, Status) VALUES (?, ?, 'active')",
        BUDGET_ID,
        PROJECT_ID,
    )
    cur.execute(
        """
        INSERT INTO dbo.BudgetRevision (BudgetId, Status)
        OUTPUT INSERTED.Id VALUES (?, 'approved')
        """,
        BUDGET_ID,
    )
    rev_id = int(cur.fetchone()[0])
    for bl in (
        (rev_id, SCC_A, Decimal("1000.00"), Decimal("1200.00")),
        (rev_id, SCC_B, Decimal("2000.00"), Decimal("2400.00")),
        (rev_id, SCC_C, Decimal("3000.00"), Decimal("3600.00")),
        (rev_id, None, Decimal("50.00"), Decimal("60.00")),
    ):
        cur.execute(
            """
            INSERT INTO dbo.BudgetLineItem (BudgetRevisionId, SubCostCodeId, Amount, Price)
            VALUES (?, ?, ?, ?)
            """,
            *bl,
        )

    cur.execute(
        """
        INSERT INTO dbo.BillLineItem (ProjectId, SubCostCodeId, Amount)
        OUTPUT INSERTED.Id
        VALUES (?, ?, 100.00)
        """,
        PROJECT_ID,
        SCC_A,
    )
    bli_a_id = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO dbo.BillLineItem (ProjectId, SubCostCodeId, Amount)
        VALUES (?, ?, 200.00), (?, NULL, 15.00)
        """,
        PROJECT_ID,
        SCC_B,
        PROJECT_ID,
    )

    cur.execute("INSERT INTO dbo.Expense (IsCredit) OUTPUT INSERTED.Id VALUES (0)")
    exp_id = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO dbo.ExpenseLineItem (ExpenseId, ProjectId, SubCostCodeId, Amount)
        OUTPUT INSERTED.Id
        VALUES (?, ?, ?, 80.00)
        """,
        exp_id,
        PROJECT_ID,
        SCC_B,
    )
    eli_b_id = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO dbo.ExpenseLineItem (ExpenseId, ProjectId, SubCostCodeId, Amount)
        VALUES (?, ?, ?, 90.00), (?, ?, NULL, 5.00)
        """,
        exp_id,
        PROJECT_ID,
        SCC_C,
        exp_id,
        PROJECT_ID,
    )

    cur.execute(
        """
        INSERT INTO dbo.BillCreditLineItem (ProjectId, SubCostCodeId, Amount)
        OUTPUT INSERTED.Id
        VALUES (?, ?, 40.00)
        """,
        PROJECT_ID,
        SCC_ACTUAL_ONLY,
    )
    bcli_d_id = int(cur.fetchone()[0])

    cur.execute(
        """
        INSERT INTO dbo.ContractLaborLineItem
            (ProjectId, SubCostCodeId, Price, Markup, Hours, IsOverhead, BillLineItemId, IsBillable)
        VALUES (?, ?, 220.00, 0.25, 1.00, 0, NULL, 1)
        """,
        PROJECT_ID,
        SCC_ACTUAL_ONLY,
    )

    cur.execute(
        """
        INSERT INTO dbo.EmployeeLaborLineItem (ProjectId, SubCostCodeId, Hours, Rate, IsOverhead)
        VALUES (?, ?, 2.00, 25.00, 0)
        """,
        PROJECT_ID,
        SCC_ACTUAL_ONLY,
    )
    cur.execute(
        """
        INSERT INTO dbo.EmployeeLaborLineItem (ProjectId, SubCostCodeId, Hours, Rate, IsOverhead)
        OUTPUT INSERTED.Id
        VALUES (?, ?, 3.00, 30.00, 0)
        """,
        PROJECT_ID,
        SCC_C,
    )
    elli_c_id = int(cur.fetchone()[0])

    cur.execute(
        """
        INSERT INTO dbo.Invoice (ProjectId, IsDraft)
        OUTPUT INSERTED.Id VALUES (?, 0)
        """,
        PROJECT_ID,
    )
    inv_id = int(cur.fetchone()[0])

    cur.execute(
        """
        INSERT INTO dbo.InvoiceLineItem (InvoiceId, SubCostCodeId, Price)
        VALUES (?, ?, 300.00)
        """,
        inv_id,
        SCC_A,
    )
    cur.execute(
        """
        INSERT INTO dbo.InvoiceLineItem
            (InvoiceId, SubCostCodeId, BillLineItemId, Price)
        VALUES (?, NULL, ?, 50.00)
        """,
        inv_id,
        bli_a_id,
    )
    cur.execute(
        """
        INSERT INTO dbo.InvoiceLineItem
            (InvoiceId, SubCostCodeId, BillLineItemId, Price)
        VALUES (?, ?, ?, 75.00)
        """,
        inv_id,
        SCC_B,
        bli_a_id,
    )
    cur.execute(
        """
        INSERT INTO dbo.InvoiceLineItem
            (InvoiceId, SubCostCodeId, ExpenseLineItemId, Price)
        VALUES (?, NULL, ?, 60.00)
        """,
        inv_id,
        eli_b_id,
    )
    cur.execute(
        """
        INSERT INTO dbo.InvoiceLineItem
            (InvoiceId, SubCostCodeId, BillCreditLineItemId, Price)
        VALUES (?, NULL, ?, 35.00)
        """,
        inv_id,
        bcli_d_id,
    )
    cur.execute(
        """
        INSERT INTO dbo.InvoiceLineItem
            (InvoiceId, SubCostCodeId, EmployeeLaborLineItemId, Price)
        VALUES (?, NULL, ?, 45.00)
        """,
        inv_id,
        elli_c_id,
    )
    cur.execute(
        """
        INSERT INTO dbo.InvoiceLineItem (InvoiceId, SubCostCodeId, Price)
        VALUES (?, ?, 400.00), (?, NULL, 25.00), (?, ?, 500.00)
        """,
        inv_id,
        SCC_C,
        inv_id,
        inv_id,
        SCC_DRAW_ONLY,
    )


def _fetch_variance(
    cur,
    *,
    sub_cost_code_id=None,
    explicit_null=False,
    actor_user_id=ACTOR_ALLOWED,
    actor_is_system_admin=0,
    omit_sub_cost_code_param=False,
):
    params = [PROJECT_ID, BUDGET_ID, actor_user_id, actor_is_system_admin]
    scc_clause = ""
    if explicit_null:
        scc_clause = ",\n                @SubCostCodeId = NULL"
    elif not omit_sub_cost_code_param and sub_cost_code_id is not None:
        scc_clause = ",\n                @SubCostCodeId = ?"
        params.append(sub_cost_code_id)

    cur.execute(
        f"""
        EXEC dbo.ReadBudgetVarianceByProjectId
            @ProjectId = ?,
            @BudgetId = ?,
            @ActorUserId = ?,
            @ActorIsSystemAdmin = ?{scc_clause}
        """,
        params,
    )
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    while cur.nextset():
        pass
    return rows, cols


def _filter_param_for_row(row: dict) -> int:
    return -1 if row["SubCostCodeId"] is None else int(row["SubCostCodeId"])


def _assert_every_scc_filter_matches_full(cur) -> None:
    full, _ = _fetch_variance(cur, omit_sub_cost_code_param=True)
    assert full, "expected non-empty variance for allowed actor"
    full_map = {r["SubCostCodeId"]: r for r in full}

    for row in full:
        scc_key = row["SubCostCodeId"]
        filt_param = _filter_param_for_row(row)
        filtered, _ = _fetch_variance(cur, sub_cost_code_id=filt_param)
        assert len(filtered) == 1, f"filter {filt_param!r} for key {scc_key!r}"
        assert filtered[0] == row
        if scc_key is not None:
            assert filtered[0]["SubCostCodeId"] == scc_key

    assert full_map[SCC_ACTUAL_ONLY]["BudgetAmount"] == Decimal("0.00")
    assert full_map[SCC_DRAW_ONLY]["BudgetAmount"] == Decimal("0.00")
    assert full_map[SCC_DRAW_ONLY]["DrawnPrice"] == Decimal("500.00")
    assert full_map[SCC_B]["DrawnPrice"] == Decimal("135.00")
    assert full_map[SCC_A]["DrawnPrice"] == Decimal("350.00")

    empty, _ = _fetch_variance(cur, sub_cost_code_id=MISSING_SCC)
    assert empty == []


@pytest.fixture
def conn():
    import pyodbc

    c = pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=True)
    cur = c.cursor()
    _drop_all(cur)
    _install_schema(cur)
    _seed_fixtures(cur)
    yield c
    _drop_all(cur)
    c.close()


def test_filtered_rows_match_full_per_scc(conn):
    cur = conn.cursor()
    _assert_every_scc_filter_matches_full(cur)


def test_null_parameter_matches_omitted(conn):
    cur = conn.cursor()
    omitted, cols_omitted = _fetch_variance(cur, omit_sub_cost_code_param=True)
    explicit, cols_explicit = _fetch_variance(cur, explicit_null=True)
    assert cols_explicit == cols_omitted
    assert explicit == omitted


def test_access_denied_without_project_membership(conn):
    cur = conn.cursor()
    reference, ref_cols = _fetch_variance(cur, omit_sub_cost_code_param=True)
    assert reference

    for sub_filter in (None, SCC_A):
        kwargs = {"omit_sub_cost_code_param": sub_filter is None}
        if sub_filter is not None:
            kwargs["sub_cost_code_id"] = sub_filter
        denied, cols = _fetch_variance(
            cur,
            actor_user_id=ACTOR_DENIED,
            actor_is_system_admin=0,
            **kwargs,
        )
        assert denied == []
        assert cols == ref_cols


def test_access_denied_for_null_actor(conn):
    """Unset actor params → UDF returns 0; @CanAccess = 1 excludes all rows."""
    cur = conn.cursor()
    _, ref_cols = _fetch_variance(cur, omit_sub_cost_code_param=True)

    for sub_filter in (None, SCC_B):
        kwargs = {"omit_sub_cost_code_param": sub_filter is None}
        if sub_filter is not None:
            kwargs["sub_cost_code_id"] = sub_filter
        denied, cols = _fetch_variance(
            cur,
            actor_user_id=None,
            actor_is_system_admin=None,
            **kwargs,
        )
        assert denied == []
        assert cols == ref_cols


def test_red_proof_broken_draw_filter_fails(conn):
    cur = conn.cursor()
    _install_broken_draw_coalesce_filter(cur)
    try:
        with pytest.raises(AssertionError):
            _assert_every_scc_filter_matches_full(cur)
    finally:
        _exec_batches(cur, VARIANCE_SQL)


def test_red_proof_broken_access_gate_fails(conn):
    cur = conn.cursor()
    _install_broken_access_gate(cur)
    try:
        denied, _ = _fetch_variance(
            cur,
            actor_user_id=ACTOR_DENIED,
            actor_is_system_admin=0,
            omit_sub_cost_code_param=True,
        )
        assert denied != [], "broken access gate should leak rows to denied actor"
    finally:
        _exec_batches(cur, VARIANCE_SQL)
