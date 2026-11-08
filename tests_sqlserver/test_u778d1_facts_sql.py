"""U-778d1 b2: the invoice-push read sprocs against real SQL Server (LOCAL Docker only).

Loads the four REAL `CREATE OR ALTER PROCEDURE` batches out of dbo.invoice_push.sql and runs them over MINIMAL
stand-in tables (only the columns the sprocs read; the real CREATE TABLE batches chain FKs through a dozen entities).
Seeds are chosen so the right and the wrong answer differ: an ILI on ANOTHER invoice, a staged invoice line linking
the same charge id in ANOTHER realm (must not count), a same-QboId staged parent in another realm, a missing id,
a draft parent, a holding sub cost code, a dbo identity stamped in another realm.
"""
from __future__ import annotations

import json
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

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = REPO_ROOT / "integrations/intuit/qbo/invoice_push/sql/dbo.invoice_push.sql"
PROCS = ("ReadInvoicePushBillLineFacts", "ReadInvoicePushExpenseLineFacts",
         "ReadInvoicePushBillCreditLineFacts", "ReadInvoicePushProjectFacts",
         "ReadInvoicePushStagedInvoiceLinksByChargeIds")

TABLES_DDL = """
CREATE TABLE dbo.Project (Id BIGINT PRIMARY KEY, QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL);
CREATE TABLE dbo.Invoice (Id BIGINT PRIMARY KEY, ProjectId BIGINT NOT NULL, InvoiceNumber NVARCHAR(50) NOT NULL,
    QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL);
CREATE TABLE dbo.SubCostCode (Id BIGINT PRIMARY KEY, IsHoldingCode BIT NOT NULL);
CREATE TABLE dbo.Bill (Id BIGINT PRIMARY KEY, QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL, IsDraft BIT NOT NULL);
CREATE TABLE dbo.Expense (Id BIGINT PRIMARY KEY, QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL, IsDraft BIT NOT NULL);
CREATE TABLE dbo.BillCredit (Id BIGINT PRIMARY KEY, QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL, IsDraft BIT NOT NULL);
CREATE TABLE dbo.BillLineItem (Id BIGINT PRIMARY KEY, BillId BIGINT NOT NULL, SubCostCodeId BIGINT NULL, ProjectId BIGINT NULL,
    Amount DECIMAL(18,2) NULL, IsBillable BIT NULL, IsBilled BIT NULL, Markup DECIMAL(18,4) NULL, Price DECIMAL(18,2) NULL,
    QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL);
CREATE TABLE dbo.ExpenseLineItem (Id BIGINT PRIMARY KEY, ExpenseId BIGINT NOT NULL, SubCostCodeId BIGINT NULL, ProjectId BIGINT NULL,
    Amount DECIMAL(18,2) NULL, IsBillable BIT NULL, IsBilled BIT NULL, Markup DECIMAL(18,4) NULL, Price DECIMAL(18,2) NULL,
    QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL);
CREATE TABLE dbo.BillCreditLineItem (Id BIGINT PRIMARY KEY, BillCreditId BIGINT NOT NULL, SubCostCodeId BIGINT NULL, ProjectId BIGINT NULL,
    Amount DECIMAL(18,2) NULL, IsBillable BIT NULL, IsBilled BIT NULL, BillableAmount DECIMAL(18,2) NULL,
    QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL);
CREATE TABLE dbo.InvoiceLineItem (Id BIGINT PRIMARY KEY, InvoiceId BIGINT NOT NULL, BillLineItemId BIGINT NULL,
    ExpenseLineItemId BIGINT NULL, BillCreditLineItemId BIGINT NULL);
CREATE TABLE qbo.Bill (Id BIGINT PRIMARY KEY, QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL);
CREATE TABLE qbo.BillLine (Id BIGINT PRIMARY KEY, QboBillId BIGINT NOT NULL, QboLineId NVARCHAR(50) NULL, ReimburseChargeQboId NVARCHAR(50) NULL);
CREATE TABLE qbo.Purchase (Id BIGINT PRIMARY KEY, QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL);
CREATE TABLE qbo.PurchaseLine (Id BIGINT PRIMARY KEY, QboPurchaseId BIGINT NOT NULL, QboLineId NVARCHAR(50) NULL, ReimburseChargeQboId NVARCHAR(50) NULL);
CREATE TABLE qbo.VendorCredit (Id BIGINT PRIMARY KEY, QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL);
CREATE TABLE qbo.VendorCreditLine (Id BIGINT PRIMARY KEY, QboVendorCreditId BIGINT NOT NULL, QboLineId NVARCHAR(50) NULL, ReimburseChargeQboId NVARCHAR(50) NULL);
CREATE TABLE qbo.Invoice (Id BIGINT PRIMARY KEY, QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL);
CREATE TABLE qbo.InvoiceLine (Id BIGINT PRIMARY KEY, QboInvoiceId BIGINT NOT NULL, QboLineId NVARCHAR(50) NULL,
    LinkedTxnType NVARCHAR(64) NULL, LinkedTxnId NVARCHAR(50) NULL);
"""
TABLES = ["dbo.InvoiceLineItem", "dbo.BillLineItem", "dbo.ExpenseLineItem", "dbo.BillCreditLineItem", "dbo.Bill",
          "dbo.Expense", "dbo.BillCredit", "dbo.SubCostCode", "dbo.Invoice", "dbo.Project", "qbo.BillLine", "qbo.Bill",
          "qbo.PurchaseLine", "qbo.Purchase", "qbo.VendorCreditLine", "qbo.VendorCredit", "qbo.InvoiceLine", "qbo.Invoice"]

# kind -> (proc, header table, line table, fk col, ILI col, qbo header, qbo line, qbo fk, has price columns)
KINDS = {
    "bill": ("ReadInvoicePushBillLineFacts", "Bill", "BillLineItem", "BillId", "BillLineItemId",
             "Bill", "BillLine", "QboBillId", True),
    "expense": ("ReadInvoicePushExpenseLineFacts", "Expense", "ExpenseLineItem", "ExpenseId", "ExpenseLineItemId",
                "Purchase", "PurchaseLine", "QboPurchaseId", True),
    "credit": ("ReadInvoicePushBillCreditLineFacts", "BillCredit", "BillCreditLineItem", "BillCreditId",
               "BillCreditLineItemId", "VendorCredit", "VendorCreditLine", "QboVendorCreditId", False),
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


def _drain(cur):
    while True:
        if cur.description:
            cur.fetchall()
        if not cur.nextset():
            break


def _drop_all(c):
    for p in PROCS:
        c.execute(f"IF OBJECT_ID('dbo.{p}','P') IS NOT NULL DROP PROCEDURE dbo.{p}")
    for t in TABLES:
        c.execute(f"IF OBJECT_ID('{t}','U') IS NOT NULL DROP TABLE {t}")


def _seed(c, kind):
    proc, hdr, line, fk, ili, qh, ql, qfk, priced = KINDS[kind]
    c.execute("INSERT dbo.SubCostCode VALUES (100,0),(101,1)") if kind == "bill" else None
    # parents: 1 clean (R1), 2 draft (R1)
    c.execute(f"INSERT dbo.{hdr} VALUES (1,'P1','R1',0),(2,'P2','R1',1)")
    cols = "(Id,%s,SubCostCodeId,ProjectId,IsBillable,IsBilled,QboId,RealmId,%s)" % (
        fk, "Amount,Markup,Price" if priced else "Amount,BillableAmount")
    vals = (
        "(11,1,100,7,1,0,'L1','R1',32.50,0.5000,48.75),"
        "(12,1,101,7,1,1,'L2','R1',10.00,NULL,NULL),"
        "(13,2,100,7,0,0,'L3','R1',5.00,NULL,NULL),"
        "(14,1,100,8,1,0,'L4','R2',6.00,NULL,NULL)"
    ) if priced else (
        "(11,1,100,7,1,0,'L1','R1',-15.34,-15.34),"
        "(12,1,101,7,1,1,'L2','R1',-10.00,-10.00),"
        "(13,2,100,7,0,0,'L3','R1',-5.00,NULL),"
        "(14,1,100,8,1,0,'L4','R2',-6.00,NULL)"
    )
    c.execute(f"INSERT dbo.{line} {cols} VALUES {vals}")
    # staged: the WRONG-realm parent P1/R2 has the LOWER ids (header 1, line 1) so a dropped RealmId scope picks it
    # (TOP 1 ORDER BY Id); the real R1 parent is header 2. Line L2 has THREE staged rows (duplicates + a disagreement).
    # L4 is stamped R2 in dbo and staged ONLY under the R1 parent, so dropping the line-realm gate would surface it.
    c.execute(f"INSERT qbo.{qh} VALUES (1,'P1','R2'),(2,'P1','R1')")
    c.execute(f"INSERT qbo.{ql} VALUES (1,1,'L1','99999'),(2,2,'L1','68056'),(3,2,'L2','70000'),"
              f"(4,2,'L2','70000'),(5,2,'L2','70001'),(6,2,'L4','55555')")
    # ILIs: line 12 is cited by invoices 1 AND 2 (any invoice counts); line 11 by nobody.
    base = ILI_BASE[kind]
    c.execute(f"INSERT dbo.InvoiceLineItem (Id,InvoiceId,{ili}) VALUES ({base + 21},2,12),({base + 20},1,12)")


def _seed_staged_invoices(c):
    # R1 invoice links charge 70000 (line 12's staged charge); R2 invoice links 68056 (must NOT count for line 11);
    # an R1 line with a different TxnType and id 68056 must not count either.
    # the R2 invoice is header 1 (lowest id) and carries the wrong-realm candidates, incl. a charge present ONLY in R2
    c.execute("INSERT qbo.Invoice VALUES (1,'2000','R2'),(2,'1000','R1')")
    c.execute("INSERT qbo.InvoiceLine VALUES (30,2,'d','ReimburseCharge','70000'),(31,2,'a','ReimburseCharge','70000'),"
              "(32,1,'b','ReimburseCharge','68056'),(33,2,'c','Purchase','68056'),(34,1,'e','ReimburseCharge','90909')")
    # dbo invoices: 1 = R1 'INV-1' (QboId 1000), 2 = stamped R2, 3/4 = more INV-1 rows in project 7 (one unmapped)
    c.execute("INSERT dbo.Invoice VALUES (1,7,'INV-1','1000','R1'),(2,8,'INV-2','2000','R2'),"
              "(3,7,'INV-1','1001','R1'),(4,7,'INV-1',NULL,NULL)")


ILI_BASE = {"bill": 0, "expense": 100, "credit": 200}


@pytest.fixture(scope="module")
def cur():
    conn = pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=True)
    c = conn.cursor()
    c.execute("IF SCHEMA_ID('qbo') IS NULL EXEC('CREATE SCHEMA qbo')")
    _drop_all(c)
    for stmt in [s for s in TABLES_DDL.split(";") if s.strip()]:
        c.execute(stmt)
    text = SQL_FILE.read_text(encoding="utf-8")
    for p in PROCS:
        batch = next(b for b in _batches(text) if re.search(r"CREATE OR ALTER PROCEDURE\s+(dbo\.)?" + p + r"\b", b))
        c.execute(batch)
    # applying the base file twice is idempotent
    for b in _batches(text):
        if "CREATE OR ALTER PROCEDURE" in b:
            c.execute(b)
    yield c
    _drop_all(c)
    conn.close()


def _run_raw(cur, proc, ids, realm="R1"):
    cur.execute(f"SET NOCOUNT ON; EXEC dbo.{proc} @IdsJson=?, @RealmId=?", json.dumps(ids), realm)
    rows = cur.fetchall()
    _drain(cur)
    return rows


def _run(cur, proc, ids, realm="R1"):
    raw = _run_raw(cur, proc, ids, realm)
    rows = {r.LineId: r for r in raw}
    assert len(rows) == len(raw), "one row per found line: duplicate input ids must not duplicate rows"
    return rows


@pytest.fixture(scope="module", autouse=True)
def seeded(cur):
    _seed_staged_invoices(cur)
    for kind in KINDS:
        _seed(cur, kind)
    return True


def _ids(s):
    return [int(x) for x in s.split(",")] if s else []


@pytest.mark.parametrize("kind", list(KINDS))
def test_facts_rows_and_missing_id(cur, kind):
    rows = _run(cur, KINDS[kind][0], [11, 12, 13, 14, 999])
    assert sorted(rows) == [11, 12, 13, 14]  # 999 has no row


@pytest.mark.parametrize("kind", list(KINDS))
def test_clean_line_fields(cur, kind):
    r = _run(cur, KINDS[kind][0], [11])[11]
    assert (r.ProjectId, r.SubCostCodeId, bool(r.IsHoldingCode), bool(r.IsBillable), bool(r.IsBilled)) == (7, 100, False, True, False)
    assert (r.ParentId, r.ParentQboId, r.LineQboId, bool(r.ParentIsDraft)) == (1, "P1", "L1", False)
    assert r.StagedChargeIds == "68056"  # the R1 staged parent, NOT the lower-id R2 one carrying 99999
    assert not r.InvoiceLineItemIds and not r.InvoiceLineItemInvoiceQboIds
    assert not hasattr(r, "StagedInvoiceLineIds")  # F1: staged invoice evidence is keyed by the LIVE charge now
    if KINDS[kind][8]:
        from decimal import Decimal
        assert (r.Amount, r.Markup, r.Price) == (Decimal("32.50"), Decimal("0.5000"), Decimal("48.75"))
    else:
        from decimal import Decimal
        assert r.BillableAmount == Decimal("-15.34") and not hasattr(r, "Amount")


@pytest.mark.parametrize("kind", list(KINDS))
def test_holding_billed_ili_on_any_invoice_and_staged_invoice_lines(cur, kind):
    r = _run(cur, KINDS[kind][0], [12])[12]
    assert bool(r.IsHoldingCode) is True and bool(r.IsBilled) is True
    assert r.StagedChargeIds == "70000,70001"  # distinct, ordered, NO TOP 1: duplicates collapse, disagreement shows
    b = ILI_BASE[kind]
    assert _ids(r.InvoiceLineItemIds) == [b + 20, b + 21]  # invoice 1 AND invoice 2; line 11 has none
    # aligned by ILI id: invoice 1 is R1 'INV-1' (QboId 1000); invoice 2 is stamped R2 -> '-' (never the foreign QboId)
    assert r.InvoiceLineItemInvoiceQboIds == "1000,-"


@pytest.mark.parametrize("kind", list(KINDS))
def test_draft_parent_flag(cur, kind):
    r = _run(cur, KINDS[kind][0], [13])[13]
    assert bool(r.ParentIsDraft) is True and bool(r.IsBillable) is False


@pytest.mark.parametrize("kind", list(KINDS))
def test_identity_stamped_in_another_realm_is_not_returned(cur, kind):
    r = _run(cur, KINDS[kind][0], [14])[14]
    assert r.LineQboId is None  # line stamped R2, asked for R1
    assert r.StagedChargeIds is None  # NOT 55555, staged under the R1 parent as 'L4'
    assert r.ParentQboId == "P1"


@pytest.mark.parametrize("kind", list(KINDS))
def test_same_staged_parent_in_other_realm_when_asking_that_realm(cur, kind):
    # dbo parent/lines are stamped R1, so asking for R2 yields no identity (and so no staged lookups) for 11
    r = _run(cur, KINDS[kind][0], [11], realm="R2")[11]
    assert r.ParentQboId is None and r.LineQboId is None and r.StagedChargeIds is None


@pytest.mark.parametrize("kind", list(KINDS))
def test_empty_and_large_id_lists(cur, kind):
    assert _run(cur, KINDS[kind][0], []) == {}
    assert sorted(_run(cur, KINDS[kind][0], list(range(1, 400)))) == [11, 12, 13, 14]


@pytest.mark.parametrize("kind", list(KINDS))
def test_duplicate_input_ids_give_one_raw_row(cur, kind):
    raw = _run_raw(cur, KINDS[kind][0], [11, 11, 12, 11, 12])
    assert sorted(r.LineId for r in raw) == [11, 12]


def test_project_facts(cur):
    cur.execute("INSERT dbo.Project VALUES (7,'1383','R1'),(8,NULL,NULL)")

    def go(pid, doc):
        cur.execute("SET NOCOUNT ON; EXEC dbo.ReadInvoicePushProjectFacts @ProjectId=?, @DocNumber=?", pid, doc)
        rows = cur.fetchall()
        _drain(cur)
        return rows

    r = go(7, "INV-1")[0]
    assert (r.ProjectId, r.QboId, r.RealmId, bool(r.DocNumberUsedLocally)) == (7, "1383", "R1", True)
    assert r.DocNumberLocalInvoiceQboIds == "1000,1001,-"  # per local invoice with that number; unmapped -> '-'
    assert bool(go(7, "INV-2")[0].DocNumberUsedLocally) is False  # INV-2 exists, but in ANOTHER project
    assert bool(go(7, "")[0].DocNumberUsedLocally) is False
    assert go(999, "INV-1") == []
    r8 = go(8, "INV-2")[0]
    assert r8.QboId is None and bool(r8.DocNumberUsedLocally) is True
    assert r8.DocNumberLocalInvoiceQboIds == "-"  # project 8 has no realm; the R2-stamped QboId is never returned
    assert go(7, "INV-2")[0].DocNumberLocalInvoiceQboIds is None


def _links(cur, ids, realm="R1"):
    cur.execute("SET NOCOUNT ON; EXEC dbo.ReadInvoicePushStagedInvoiceLinksByChargeIds @ChargeIdsJson=?, @RealmId=?",
                json.dumps(ids), realm)
    rows = [(r.ChargeId, r.QboInvoiceQboId, r.QboInvoiceLineId) for r in cur.fetchall()]
    _drain(cur)
    return sorted(rows)


def test_staged_invoice_links_by_live_charge_id_are_realm_scoped_and_typed(cur):
    # R1: 70000 is linked by two lines of invoice 1000 (all of them returned); 68056 only on the R2 invoice or a
    # Purchase-typed line; 90909 exists only in R2.
    assert _links(cur, ["70000", "68056", "90909"]) == [("70000", "1000", "a"), ("70000", "1000", "d")]
    assert _links(cur, ["68056", "90909"], realm="R2") == [("68056", "2000", "b"), ("90909", "2000", "e")]
    assert _links(cur, ["68056", "90909"]) == []
    assert _links(cur, []) == []


def test_staged_invoice_links_duplicate_input_ids_give_one_row_per_link(cur):
    assert _links(cur, ["70000", "70000", "70000"]) == [("70000", "1000", "a"), ("70000", "1000", "d")]
