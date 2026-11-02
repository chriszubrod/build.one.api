"""U-749: ReadContractLaborSubCostCodeCounts honours every U-673 visibility filter."""
from __future__ import annotations

import os
import re
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
BASE_SQL = REPO_ROOT / "entities/contract_labor/sql/dbo.contract_labor.sql"
PROC = "dbo.ReadContractLaborSubCostCodeCounts"

VENDOR, OTHER_VENDOR = 5, 6
PROJECT, OTHER_PROJECT = 77, 78
SCC_A, SCC_B, SCC_HOLD = 1, 2, 3
AS_OF = "2026-10-20"  # visible through 2026-10-18


def _proc_batch() -> str:
    text = BASE_SQL.read_text(encoding="utf-8")
    m = re.search(
        r"CREATE OR ALTER PROCEDURE dbo\.ReadContractLaborSubCostCodeCounts\b.*?(?=^GO\s*$)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert m, "sproc batch not found in base file"
    return m.group(0)


def _drop_all(cur) -> None:
    cur.execute(f"IF OBJECT_ID('{PROC}', 'P') IS NOT NULL DROP PROCEDURE {PROC}")
    for tbl in ("dbo.ContractLaborLineItem", "dbo.ContractLabor", "dbo.SubCostCode"):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_minimal_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.SubCostCode (
            Id BIGINT NOT NULL PRIMARY KEY,
            IsHoldingCode BIT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.ContractLabor (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            VendorId BIGINT NULL,
            BillVendorId BIGINT NULL,
            Status NVARCHAR(20) NOT NULL,
            WorkDate DATE NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.ContractLaborLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ContractLaborId BIGINT NOT NULL,
            ProjectId BIGINT NULL,
            SubCostCodeId BIGINT NULL,
            LineDate DATE NULL
        );
        """
    )
    cur.execute(
        "INSERT INTO dbo.SubCostCode (Id, IsHoldingCode) VALUES (?, NULL), (?, 0), (?, 1)",
        SCC_A, SCC_B, SCC_HOLD,
    )
    cur.execute(_proc_batch())


@pytest.fixture
def conn():
    import pyodbc

    c = pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=True)
    cur = c.cursor()
    _drop_all(cur)
    _install_minimal_schema(cur)
    yield c
    _drop_all(cur)
    c.close()


def _cl(cur, *, vendor=VENDOR, bill_vendor=None, status="approved", work_date="2026-10-01") -> int:
    cur.execute(
        "INSERT INTO dbo.ContractLabor (VendorId, BillVendorId, Status, WorkDate) "
        "OUTPUT INSERTED.Id VALUES (?, ?, ?, ?)",
        vendor, bill_vendor, status, work_date,
    )
    return int(cur.fetchone()[0])


def _li(cur, cl_id, *, project=PROJECT, scc=SCC_A, line_date=None) -> None:
    cur.execute(
        "INSERT INTO dbo.ContractLaborLineItem (ContractLaborId, ProjectId, SubCostCodeId, LineDate) "
        "VALUES (?, ?, ?, ?)",
        cl_id, project, scc, line_date,
    )


def _counts(cur, *, vendor=VENDOR, project=PROJECT, as_of=AS_OF, exclude=None):
    cur.execute(
        f"EXEC {PROC} @VendorId = ?, @ProjectId = ?, @AsOfDate = ?, @ExcludeContractLaborId = ?",
        vendor, project, as_of, exclude,
    )
    out: dict[str, dict[int, int]] = {"project": {}, "vendor": {}}
    for row in cur.fetchall():
        out[row.Scope][int(row.SubCostCodeId)] = int(row.LineCount)
    return out


def test_counts_split_by_scope(conn):
    cur = conn.cursor()
    cl = _cl(cur)
    _li(cur, cl, project=PROJECT, scc=SCC_A)
    _li(cur, cl, project=PROJECT, scc=SCC_A)
    _li(cur, cl, project=OTHER_PROJECT, scc=SCC_B)
    assert _counts(cur) == {"project": {SCC_A: 2}, "vendor": {SCC_A: 2, SCC_B: 1}}


@pytest.mark.parametrize("status", ["approved", "completed", "ready", "billed"])
def test_labelled_statuses_count(conn, status):
    cur = conn.cursor()
    _li(cur, _cl(cur, status=status))
    assert _counts(cur)["vendor"] == {SCC_A: 1}


@pytest.mark.parametrize("status", ["draft", "pending_review", "submitted", "declined"])
def test_unlabelled_statuses_excluded(conn, status):
    cur = conn.cursor()
    _li(cur, _cl(cur, status=status))
    assert _counts(cur) == {"project": {}, "vendor": {}}


def test_null_and_holding_codes_excluded(conn):
    cur = conn.cursor()
    cl = _cl(cur)
    _li(cur, cl, scc=None)
    _li(cur, cl, scc=SCC_HOLD)
    _li(cur, cl, scc=SCC_B)  # IsHoldingCode = 0 counts
    _li(cur, cl, scc=SCC_A)  # IsHoldingCode NULL counts
    assert _counts(cur)["vendor"] == {SCC_A: 1, SCC_B: 1}


def test_two_day_lag_boundary(conn):
    cur = conn.cursor()
    _li(cur, _cl(cur, work_date="2026-10-18"), scc=SCC_A)  # as_of - 2: visible
    _li(cur, _cl(cur, work_date="2026-10-19"), scc=SCC_B)  # as_of - 1: hidden
    _li(cur, _cl(cur, work_date="2026-10-20"), scc=SCC_B)  # as_of: hidden
    assert _counts(cur)["vendor"] == {SCC_A: 1}


def test_line_date_overrides_work_date(conn):
    cur = conn.cursor()
    cl = _cl(cur, work_date="2026-10-01")
    _li(cur, cl, scc=SCC_A, line_date="2026-10-19")  # hidden despite early WorkDate
    _li(cur, cl, scc=SCC_B, line_date=None)          # falls back to WorkDate: visible
    late = _cl(cur, work_date="2026-10-20")
    _li(cur, late, scc=SCC_A, line_date="2026-10-10")  # early LineDate: visible
    assert _counts(cur)["vendor"] == {SCC_A: 1, SCC_B: 1}


def test_excluded_entry_not_counted(conn):
    cur = conn.cursor()
    own = _cl(cur)
    _li(cur, own, scc=SCC_A)
    _li(cur, _cl(cur), scc=SCC_B)
    assert _counts(cur, exclude=own)["vendor"] == {SCC_B: 1}
    assert _counts(cur, exclude=None)["vendor"] == {SCC_A: 1, SCC_B: 1}


def test_vendor_match_is_coalesce_vendor_then_bill_vendor(conn):
    cur = conn.cursor()
    _li(cur, _cl(cur, vendor=VENDOR), scc=SCC_A)
    _li(cur, _cl(cur, vendor=None, bill_vendor=VENDOR), scc=SCC_B)        # COALESCE -> VENDOR
    _li(cur, _cl(cur, vendor=OTHER_VENDOR, bill_vendor=VENDOR), scc=SCC_B)  # COALESCE -> OTHER
    _li(cur, _cl(cur, vendor=OTHER_VENDOR), scc=SCC_A)
    assert _counts(cur)["vendor"] == {SCC_A: 1, SCC_B: 1}


def test_null_project_gives_vendor_scope_only(conn):
    cur = conn.cursor()
    _li(cur, _cl(cur), project=PROJECT, scc=SCC_A)
    assert _counts(cur, project=None) == {"project": {}, "vendor": {SCC_A: 1}}
