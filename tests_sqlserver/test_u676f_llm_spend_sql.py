"""U-676f: dbo.LlmDailySpend sprocs against local SQL Server."""
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


def test_read_missing_day_returns_nothing(conn):
    cur = conn.cursor()
    cur.execute(
        "EXEC dbo.ReadLlmDailySpend @SpendDate=?, @Provider=?",
        date(2099, 1, 1),
        "anthropic",
    )
    assert cur.fetchone() is None


def test_add_and_read_round_trip(conn):
    cur = conn.cursor()
    d = date(2026, 10, 18)
    row = _add(cur, d, "anthropic", Decimal("0.123456"))
    assert float(row.CostUsd) == pytest.approx(0.123456)
    assert row.CallCount == 1
    cur.execute("EXEC dbo.ReadLlmDailySpend @SpendDate=?, @Provider=?", d, "anthropic")
    row2 = cur.fetchone()
    assert float(row2.CostUsd) == pytest.approx(0.123456)


def test_concurrent_add_sums_exactly(conn):
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    d = date(2026, 10, 19)
    n = 20
    delta = Decimal("0.001")
    errors: list[Exception] = []

    def worker():
        try:
            c = _sqlserver_connect(conn_str, autocommit=True)
            cur = c.cursor()
            _add(cur, d, "anthropic", delta)
            c.close()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors

    cur = conn.cursor()
    cur.execute("EXEC dbo.ReadLlmDailySpend @SpendDate=?, @Provider=?", d, "anthropic")
    row = cur.fetchone()
    assert row.CallCount == n
    assert Decimal(str(row.CostUsd)) == delta * n


def test_decimal_binding_small_cost(conn):
    cur = conn.cursor()
    d = date(2026, 10, 21)
    tiny = Decimal("0.000050")
    row = _add(cur, d, "anthropic", tiny)
    assert Decimal(str(row.CostUsd)) == tiny


def test_reapply_sql_idempotent(conn):
    cur = conn.cursor()
    _exec_file(cur, LLM_SPEND_SQL)
    d = date(2026, 10, 20)
    _add(cur, d, "foundry", Decimal("1"))
    cur.execute("EXEC dbo.ReadLlmSpendForRange @FromDate=?, @ToDate=?, @Provider=?", d, d, "foundry")
    assert len(cur.fetchall()) == 1
