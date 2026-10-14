"""U-653b: in-memory backtest engine matches history procs on local SQL Server fixture."""
from __future__ import annotations

import contextlib
import os
from decimal import Decimal

import pytest

import entities.coding_suggestion.persistence.repo as coding_repo_mod
from scripts.u653b_backtest import BillRow, score_bills_rolling, verify_sample_against_procs
from tests_sqlserver.test_u653a_bill_history_sql import (
    _drop_all,
    _insert_bill,
    _insert_line,
    _install_equivalence_fixture,
    _install_minimal_schema,
    _sqlserver_connect,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)


def _load_bill_rows_from_fixture(cur) -> list[BillRow]:
    cur.execute(
        """
        WITH LineAgg AS (
            SELECT
                bli.[BillId],
                bli.[SubCostCodeId],
                SUM(ABS(bli.[Amount])) AS [AbsTotal],
                MIN(bli.[Id]) AS [MinLineId]
            FROM dbo.[BillLineItem] bli
            INNER JOIN dbo.[Bill] b ON b.[Id] = bli.[BillId]
            WHERE b.[IsDraft] = 0
              AND bli.[SubCostCodeId] IS NOT NULL
            GROUP BY bli.[BillId], bli.[SubCostCodeId]
        ),
        Dominant AS (
            SELECT
                la.[BillId],
                la.[SubCostCodeId],
                la.[AbsTotal],
                ROW_NUMBER() OVER (
                    PARTITION BY la.[BillId]
                    ORDER BY la.[AbsTotal] DESC, la.[MinLineId] ASC
                ) AS [Rn],
                la.[MinLineId]
            FROM LineAgg la
        ),
        Label AS (
            SELECT d.[BillId], d.[SubCostCodeId], bli.[ProjectId]
            FROM Dominant d
            INNER JOIN dbo.[BillLineItem] bli ON bli.[Id] = d.[MinLineId]
            WHERE d.[Rn] = 1
        ),
        Mixed AS (
            SELECT [BillId], CASE WHEN COUNT(DISTINCT [SubCostCodeId]) > 1 THEN 1 ELSE 0 END AS [MixedCode]
            FROM LineAgg
            GROUP BY [BillId]
        ),
        Totals AS (
            SELECT bli.[BillId], SUM(ABS(COALESCE(bli.[Amount], 0))) AS [TotalAbsAmount]
            FROM dbo.[BillLineItem] bli
            GROUP BY bli.[BillId]
        )
        SELECT
            b.[Id],
            b.[VendorId],
            b.[CreatedDatetime],
            l.[SubCostCodeId],
            l.[ProjectId],
            t.[TotalAbsAmount],
            m.[MixedCode]
        FROM dbo.[Bill] b
        INNER JOIN Label l ON l.[BillId] = b.[Id]
        INNER JOIN Totals t ON t.[BillId] = b.[Id]
        INNER JOIN Mixed m ON m.[BillId] = b.[Id]
        WHERE b.[IsDraft] = 0
        ORDER BY b.[CreatedDatetime], b.[Id]
        """
    )
    rows = []
    for r in cur.fetchall():
        created = r.CreatedDatetime
        if hasattr(created, "replace"):
            created = created.replace(tzinfo=None) if getattr(created, "tzinfo", None) else created
        rows.append(
            BillRow(
                bill_id=int(r.Id),
                vendor_id=int(r.VendorId),
                created=created,
                label_scc_id=int(r.SubCostCodeId),
                label_project_id=int(r.ProjectId) if r.ProjectId is not None else None,
                total_abs_amount=Decimal(str(r.TotalAbsAmount)),
                mixed_code=bool(r.MixedCode),
            )
        )
    return rows


@contextlib.contextmanager
def _as_repo_connection(conn):
    yield conn


@pytest.fixture()
def equivalence_conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    _drop_all(cur)
    _install_minimal_schema(cur)
    _install_equivalence_fixture(cur)
    yield c
    _drop_all(cur)
    c.close()


def test_in_memory_matches_procs_on_equivalence_fixture(equivalence_conn, monkeypatch):
    monkeypatch.setattr(
        coding_repo_mod,
        "get_connection",
        lambda *a, **k: _as_repo_connection(equivalence_conn),
    )
    cur = equivalence_conn.cursor()
    bills = _load_bill_rows_from_fixture(cur)
    assert len(bills) >= 10
    scored = score_bills_rolling(bills)
    verify_sample_against_procs(scored, sample_size=len(scored))
