"""U-778a bite 2: ReadQbo{Bill,Purchase,VendorCredit}LinesMissingReimburseChargeByRealmId against real SQL Server."""
from __future__ import annotations

import os
import re
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

QBO = Path(__file__).resolve().parents[1] / "integrations/intuit/qbo"

ENTITIES = {
    "bill": dict(line="BillLine", header="Bill", fk="QboBillId", file=QBO / "bill/sql/qbo.bill.sql",
                 proc="ReadQboBillLinesMissingReimburseChargeByRealmId"),
    "purchase": dict(line="PurchaseLine", header="Purchase", fk="QboPurchaseId", file=QBO / "purchase/sql/qbo.purchase.sql",
                     proc="ReadQboPurchaseLinesMissingReimburseChargeByRealmId"),
    "vendorcredit": dict(line="VendorCreditLine", header="VendorCredit", fk="QboVendorCreditId",
                         file=QBO / "vendorcredit/sql/qbo.vendorcredit.sql",
                         proc="ReadQboVendorCreditLinesMissingReimburseChargeByRealmId"),
}


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


def _batch(path: Path, pattern: str) -> str:
    for b in _batches(path.read_text(encoding="utf-8")):
        if re.search(pattern, b):
            return b
    raise RuntimeError(f"{pattern} missing from {path}")


@pytest.fixture(scope="module")
def cur():
    conn = pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=True)
    c = conn.cursor()
    c.execute("IF SCHEMA_ID('qbo') IS NULL EXEC('CREATE SCHEMA qbo')")

    def teardown():
        for e in ENTITIES.values():
            c.execute(f"IF OBJECT_ID('dbo.{e['proc']}','P') IS NOT NULL DROP PROCEDURE dbo.{e['proc']}")
            c.execute(f"IF OBJECT_ID('qbo.{e['line']}','U') IS NOT NULL DROP TABLE qbo.{e['line']}")
            c.execute(f"IF OBJECT_ID('qbo.{e['header']}','U') IS NOT NULL DROP TABLE qbo.{e['header']}")

    teardown()
    for e in ENTITIES.values():
        # real base-file CREATE TABLEs (fresh-build shape: header, then line incl. ReimburseChargeQboId)
        c.execute(_batch(e["file"], r"CREATE TABLE \[qbo\]\.\[" + e["header"] + r"\]"))
        c.execute(_batch(e["file"], r"CREATE TABLE \[qbo\]\.\[" + e["line"] + r"\]"))
        c.execute(_batch(e["file"], r"CREATE OR ALTER PROCEDURE\s+(dbo\.)?" + e["proc"] + r"\b"))
    yield c
    teardown()
    conn.close()


def _header(cur, e, qbo_id, realm):
    cur.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='qbo' AND TABLE_NAME=? "
                f"AND IS_NULLABLE='NO' AND COLUMN_DEFAULT IS NULL AND COLUMNPROPERTY(OBJECT_ID('qbo.'+TABLE_NAME), COLUMN_NAME,'IsIdentity')=0 "
                f"AND DATA_TYPE NOT IN ('timestamp')", e["header"])
    required = [r[0] for r in cur.fetchall()]
    values = {"RealmId": realm, "QboId": qbo_id, "CreatedDatetime": "2026-01-01"}
    names, vals = ["RealmId", "QboId"], [realm, qbo_id]
    for col in required:
        if col in values and col not in names:
            names.append(col); vals.append(values[col])
        elif col not in names:
            raise RuntimeError(f"unhandled required header column {col}")
    cur.execute(
        f"INSERT INTO qbo.{e['header']} ({','.join(names)}) VALUES ({','.join('?' * len(names))}); "
        "SELECT CAST(SCOPE_IDENTITY() AS BIGINT)", *vals)
    while cur.description is None and cur.nextset():
        pass
    return int(cur.fetchone()[0])


def _line(cur, e, header_id, line_id, status, charge=None):
    cur.execute(
        f"INSERT INTO qbo.{e['line']} ({e['fk']}, QboLineId, BillableStatus, ReimburseChargeQboId, CreatedDatetime) "
        "VALUES (?,?,?,?,SYSUTCDATETIME()); SELECT CAST(SCOPE_IDENTITY() AS BIGINT)",
        header_id, line_id, status, charge)
    while cur.description is None and cur.nextset():
        pass
    return int(cur.fetchone()[0])


@pytest.mark.parametrize("key", list(ENTITIES))
def test_missing_link_read(cur, key):
    e = ENTITIES[key]
    h10 = _header(cur, e, "10", "R1")
    h9 = _header(cur, e, "9", "R1")
    h_other = _header(cur, e, "5", "R2")
    a = _line(cur, e, h10, "a", "Billable")
    b = _line(cur, e, h10, "b", "HasBeenBilled")
    _line(cur, e, h10, "c", "NotBillable")
    _line(cur, e, h10, "d", "Billable", "68056")      # already linked
    _line(cur, e, h_other, "e", "Billable")           # other realm
    z = _line(cur, e, h9, "z", "Billable")
    cur.execute(f"SET NOCOUNT ON; EXEC dbo.{e['proc']} @RealmId=?", "R1")
    rows = [(r.Id, r.QboLineId, r.HeaderQboId) for r in cur.fetchall()]
    assert rows == [(z, "z", "9"), (a, "a", "10"), (b, "b", "10")]  # numeric header order: '9' before '10'
