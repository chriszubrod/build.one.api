"""U-701d: dbo.LlmDailySpend keeps anthropic and foundry independent (local SQL Server)."""
from __future__ import annotations

import os
import re
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyodbc
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

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
    raise RuntimeError("tests_sqlserver refuses non-local SERVER")

REPO_ROOT = Path(__file__).resolve().parents[1]
LLM_SPEND_SQL = REPO_ROOT / "shared/sql/dbo.llm_daily_spend.sql"


def _sqlserver_connect(conn_str: str, *, autocommit: bool = False):
    return pyodbc.connect(conn_str, autocommit=autocommit)


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


def _exec_file(cur, path: Path) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        cur.execute(batch)


def _drain(cur) -> None:
    while True:
        try:
            if not cur.nextset():
                break
        except pyodbc.Error:
            break


def _drop(cur) -> None:
    _drain(cur)
    for proc in (
        "dbo.AddLlmDailySpend",
        "dbo.ReadLlmDailySpend",
        "dbo.ReadLlmSpendForRange",
    ):
        cur.execute(f"IF OBJECT_ID('{proc}', 'P') IS NOT NULL DROP PROCEDURE {proc}")
        _drain(cur)
    cur.execute("IF OBJECT_ID('dbo.LlmDailySpend', 'U') IS NOT NULL DROP TABLE dbo.LlmDailySpend")
    _drain(cur)


@pytest.fixture
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    _drop(cur)
    _exec_file(cur, LLM_SPEND_SQL)
    yield c
    _drop(cur)
    c.close()


def _add(cur, spend_date: date, provider: str, cost: Decimal):
    cur.execute(
        "EXEC dbo.AddLlmDailySpend @SpendDate=?, @Provider=?, @CostUsd=?",
        spend_date,
        provider,
        cost,
    )
    row = cur.fetchone()
    _drain(cur)
    return row


def _read(cur, d: date, provider: str):
    cur.execute("EXEC dbo.ReadLlmDailySpend @SpendDate=?, @Provider=?", d, provider)
    row = cur.fetchone()
    _drain(cur)
    return row


def test_same_date_two_providers_independent_rows(conn):
    cur = conn.cursor()
    d = date(2026, 10, 28)
    a1 = _add(cur, d, "anthropic", Decimal("1.500000"))
    f1 = _add(cur, d, "foundry", Decimal("0.005250"))
    a2 = _add(cur, d, "anthropic", Decimal("0.250000"))
    f2 = _add(cur, d, "foundry", Decimal("0.001450"))
    assert Decimal(str(a1.CostUsd)) == Decimal("1.5")
    assert Decimal(str(f1.CostUsd)) == Decimal("0.00525")
    assert Decimal(str(a2.CostUsd)) == Decimal("1.75")
    assert Decimal(str(f2.CostUsd)) == Decimal("0.0067")
    a = _read(cur, d, "anthropic")
    f = _read(cur, d, "foundry")
    assert (Decimal(str(a.CostUsd)), a.CallCount) == (Decimal("1.75"), 2)
    assert (Decimal(str(f.CostUsd)), f.CallCount) == (Decimal("0.0067"), 2)
    cur.execute("SELECT COUNT(*) FROM dbo.LlmDailySpend WHERE SpendDate = ?", d)
    assert cur.fetchone()[0] == 2


def test_concurrent_foundry_adds_sum_exactly(conn):
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    d = date(2026, 10, 29)
    n = 24
    delta = Decimal("0.000750")
    errors: list[Exception] = []
    barrier = threading.Barrier(n)

    def worker():
        try:
            c = _sqlserver_connect(conn_str, autocommit=True)
            cur = c.cursor()
            barrier.wait()
            _add(cur, d, "foundry", delta)
            c.close()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    row = _read(conn.cursor(), d, "foundry")
    assert row.CallCount == n
    assert Decimal(str(row.CostUsd)) == delta * n
    assert _read(conn.cursor(), d, "anthropic") is None


def test_read_range_filters_by_provider(conn):
    cur = conn.cursor()
    d1, d2, d3 = date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)
    _add(cur, d1, "foundry", Decimal("0.1"))
    _add(cur, d2, "anthropic", Decimal("2"))
    _add(cur, d3, "foundry", Decimal("0.3"))
    _add(cur, d3, "anthropic", Decimal("3"))
    cur.execute("EXEC dbo.ReadLlmSpendForRange @FromDate=?, @ToDate=?, @Provider=?", d1, d3, "foundry")
    rows = cur.fetchall()
    _drain(cur)
    assert [(r.SpendDate, r.Provider, Decimal(str(r.CostUsd))) for r in rows] == [
        (d1, "foundry", Decimal("0.1")), (d3, "foundry", Decimal("0.3")),
    ]
    cur.execute("EXEC dbo.ReadLlmSpendForRange @FromDate=?, @ToDate=?, @Provider=?", d2, d3, "anthropic")
    rows = cur.fetchall()
    _drain(cur)
    assert [(r.SpendDate, Decimal(str(r.CostUsd))) for r in rows] == [(d2, Decimal("2")), (d3, Decimal("3"))]
