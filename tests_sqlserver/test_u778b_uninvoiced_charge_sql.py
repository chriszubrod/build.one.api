"""U-778b: Read{Bill,Purchase,VendorCredit}LinesWithUninvoicedChargeByRealmId and
ReadInvoiceLineItemIdsBySourceLineItem against a real (LOCAL) SQL Server. Not runnable by the builder."""
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

ROOT = Path(__file__).resolve().parents[1]
QBO = ROOT / "integrations/intuit/qbo"
RC_FILE = QBO / "reimburse_charge/sql/qbo.reimburse_charge.sql"
ILI_FILE = ROOT / "entities/invoice_line_item/sql/dbo.invoice_line_item.sql"
ILI_PROC = "ReadInvoiceLineItemIdsBySourceLineItem"

ENTITIES = {
    "bill": dict(line="BillLine", header="Bill", fk="QboBillId", file=QBO / "bill/sql/qbo.bill.sql",
                 proc="ReadQboBillLinesWithUninvoicedChargeByRealmId"),
    "purchase": dict(line="PurchaseLine", header="Purchase", fk="QboPurchaseId", file=QBO / "purchase/sql/qbo.purchase.sql",
                     proc="ReadQboPurchaseLinesWithUninvoicedChargeByRealmId"),
    "vendorcredit": dict(line="VendorCreditLine", header="VendorCredit", fk="QboVendorCreditId",
                         file=QBO / "vendorcredit/sql/qbo.vendorcredit.sql",
                         proc="ReadQboVendorCreditLinesWithUninvoicedChargeByRealmId"),
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
        c.execute(f"IF OBJECT_ID('dbo.{ILI_PROC}','P') IS NOT NULL DROP PROCEDURE dbo.{ILI_PROC}")
        c.execute("IF OBJECT_ID('dbo.InvoiceLineItem','U') IS NOT NULL DROP TABLE dbo.InvoiceLineItem")
        for e in ENTITIES.values():
            c.execute(f"IF OBJECT_ID('dbo.{e['proc']}','P') IS NOT NULL DROP PROCEDURE dbo.{e['proc']}")
            c.execute(f"IF OBJECT_ID('qbo.{e['line']}','U') IS NOT NULL DROP TABLE qbo.{e['line']}")
            c.execute(f"IF OBJECT_ID('qbo.{e['header']}','U') IS NOT NULL DROP TABLE qbo.{e['header']}")
        c.execute("IF OBJECT_ID('qbo.ReimburseCharge','U') IS NOT NULL DROP TABLE qbo.ReimburseCharge")

    teardown()
    c.execute(_batch(RC_FILE, r"CREATE TABLE \[qbo\]\.\[ReimburseCharge\]"))
    for e in ENTITIES.values():
        c.execute(_batch(e["file"], r"CREATE TABLE \[qbo\]\.\[" + e["header"] + r"\]"))
        c.execute(_batch(e["file"], r"CREATE TABLE \[qbo\]\.\[" + e["line"] + r"\]"))
        c.execute(_batch(e["file"], r"CREATE OR ALTER PROCEDURE\s+(dbo\.)?" + e["proc"] + r"\b"))
    # Minimal InvoiceLineItem stand-in (only the columns the sproc reads; no FKs to the wider schema).
    c.execute(
        "CREATE TABLE dbo.InvoiceLineItem (Id BIGINT IDENTITY(1,1) PRIMARY KEY, InvoiceId BIGINT NOT NULL, "
        "BillLineItemId BIGINT NULL, ExpenseLineItemId BIGINT NULL, BillCreditLineItemId BIGINT NULL)"
    )
    c.execute(_batch(ILI_FILE, r"CREATE OR ALTER PROCEDURE\s+(dbo\.)?" + ILI_PROC + r"\b"))
    yield c
    teardown()
    conn.close()


def _scalar_id(cur):
    while cur.description is None and cur.nextset():
        pass
    return int(cur.fetchone()[0])


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
        f"INSERT INTO qbo.{e['line']} ({e['fk']}, QboLineId, BillableStatus, ReimburseChargeQboId, Amount, CreatedDatetime) "
        "VALUES (?,?,?,?,21.27,SYSUTCDATETIME()); SELECT CAST(SCOPE_IDENTITY() AS BIGINT)",
        header_id, line_id, status, charge)
    return _scalar_id(cur)


def _charge(cur, qbo_id, realm, invoiced):
    cur.execute(
        "INSERT INTO qbo.ReimburseCharge (QboId, RealmId, HasBeenInvoiced, CreatedDatetime) "
        "VALUES (?,?,?,SYSUTCDATETIME())", qbo_id, realm, invoiced)


@pytest.mark.parametrize("key", list(ENTITIES))
def test_candidates_only_billed_line_with_uninvoiced_same_realm_charge(cur, key):
    e = ENTITIES[key]
    cur.execute(f"DELETE FROM qbo.{e['line']}; DELETE FROM qbo.{e['header']}; DELETE FROM qbo.ReimburseCharge")
    _charge(cur, "C-UNINV", "R1", 0)
    _charge(cur, "C-INV", "R1", 1)
    _charge(cur, "C-OTHER", "R2", 0)      # uninvoiced but only in the OTHER realm
    cur.execute("INSERT INTO qbo.ReimburseCharge (QboId, RealmId, HasBeenInvoiced, CreatedDatetime) "
                "VALUES ('C-NULL','R1',NULL,SYSUTCDATETIME())")   # HasBeenInvoiced NULL: excluded
    h = _header(cur, e, "100", "R1")
    h_r2 = _header(cur, e, "200", "R2")
    hit = _line(cur, e, h, "1", "HasBeenBilled", "C-UNINV")
    _line(cur, e, h, "2", "Billable", "C-UNINV")          # not HasBeenBilled
    _line(cur, e, h, "3", "HasBeenBilled", "C-INV")       # invoiced charge
    _line(cur, e, h, "4", "HasBeenBilled", "C-OTHER")     # charge exists only in other realm
    _line(cur, e, h, "5", "HasBeenBilled", None)          # NULL link
    _line(cur, e, h, "6", "HasBeenBilled", "C-MISSING")   # no staged charge row
    _line(cur, e, h_r2, "7", "HasBeenBilled", "C-OTHER")  # header in other realm
    _line(cur, e, h_r2, "8", "HasBeenBilled", "C-UNINV")  # header in R2 but charge matches an R1 uninvoiced one
    _line(cur, e, h, "9", "HasBeenBilled", "C-NULL")      # charge HasBeenInvoiced IS NULL
    cur.execute(f"SET NOCOUNT ON; EXEC dbo.{e['proc']} @RealmId=?", "R1")
    rows = cur.fetchall()
    assert [(r.Id, r.QboLineId, r.HeaderQboId, r.RealmId, r.BillableStatus, r.ReimburseChargeQboId) for r in rows] == [
        (hit, "1", "100", "R1", "HasBeenBilled", "C-UNINV")
    ]
    assert [c[0] for c in cur.description] == [
        "Id", "QboLineId", "Amount", "BillableStatus", "HeaderQboId", "RealmId", "ReimburseChargeQboId"
    ]


def _ili(cur, invoice_id, bill=None, expense=None, credit=None):
    cur.execute(
        "INSERT INTO dbo.InvoiceLineItem (InvoiceId, BillLineItemId, ExpenseLineItemId, BillCreditLineItemId) "
        "VALUES (?,?,?,?); SELECT CAST(SCOPE_IDENTITY() AS BIGINT)", invoice_id, bill, expense, credit)
    return _scalar_id(cur)


def test_ili_sproc_returns_rows_per_fk_kind_and_nothing_for_zero_or_two_args(cur):
    cur.execute("DELETE FROM dbo.InvoiceLineItem")
    b = _ili(cur, 11, bill=501)
    x = _ili(cur, 12, expense=502)
    x2 = _ili(cur, 13, expense=502)
    c = _ili(cur, 14, credit=503)

    def call(**kw):
        sets = ", ".join(f"@{k}=?" for k in kw)
        cur.execute(f"SET NOCOUNT ON; EXEC dbo.{ILI_PROC} {sets}", *kw.values())
        return [(r.Id, r.InvoiceId) for r in cur.fetchall()]

    assert call(BillLineItemId=501) == [(b, 11)]
    assert call(ExpenseLineItemId=502) == [(x, 12), (x2, 13)]
    assert call(BillCreditLineItemId=503) == [(c, 14)]
    assert call(BillLineItemId=999) == []
    cur.execute(f"SET NOCOUNT ON; EXEC dbo.{ILI_PROC}")
    assert cur.fetchall() == []
    assert call(BillLineItemId=501, ExpenseLineItemId=502) == []
    assert call(BillLineItemId=501, ExpenseLineItemId=502, BillCreditLineItemId=503) == []
