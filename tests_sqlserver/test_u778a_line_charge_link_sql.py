"""U-778a bite 1: ReimburseChargeQboId on qbo.{Bill,Purchase,VendorCredit}Line against real SQL Server.

Loads the real CREATE TABLE / upgrade / index / sproc batches out of the three base files, applies the base
file's own guarded upgrade twice onto a pre-column table shape (prod today; there is no separate migration), and proves the write semantics (flag 0 preserves, flag 1 + NULL
clears, flag 1 + value overwrites, narrow sproc writes only on change and rejects NULL).
"""
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

REPO_ROOT = Path(__file__).resolve().parents[1]
QBO = REPO_ROOT / "integrations/intuit/qbo"

# (entity, line table, header table, parent column, base file, create/update/narrow/read sprocs)
ENTITIES = {
    "bill": dict(
        line="BillLine", header="Bill", parent="QboBillId",
        file=QBO / "bill/sql/qbo.bill.sql",
        create="CreateQboBillLine", update="UpdateQboBillLineById",
        narrow="UpdateQboBillLineReimburseChargeById",
        read="ReadQboBillLinesByQboBillId", read_one="ReadQboBillLineByQboBillIdAndQboLineId",
        delete="DeleteQboBillLineById", full=True,
    ),
    "purchase": dict(
        line="PurchaseLine", header="Purchase", parent="QboPurchaseId",
        file=QBO / "purchase/sql/qbo.purchase.sql",
        create="CreateQboPurchaseLine", update="UpdateQboPurchaseLineById",
        narrow="UpdateQboPurchaseLineReimburseChargeById",
        read="ReadQboPurchaseLinesByQboPurchaseId", read_one="ReadQboPurchaseLineByQboPurchaseIdAndQboLineId",
        delete="DeleteQboPurchaseLineById", full=True,
    ),
    "vendorcredit": dict(
        line="VendorCreditLine", header="VendorCredit", parent="QboVendorCreditId",
        file=QBO / "vendorcredit/sql/qbo.vendorcredit.sql",
        create="CreateQboVendorCreditLine", update="UpdateQboVendorCreditLineById",
        narrow="UpdateQboVendorCreditLineReimburseChargeById",
        read="ReadQboVendorCreditLinesByVendorCreditId",
        read_one="ReadQboVendorCreditLineByVendorCreditIdAndQboLineId",
        delete=None, full=False,
    ),
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


def _proc_batch(path: Path, name: str) -> str:
    for b in _batches(path.read_text(encoding="utf-8")):
        if re.search(r"CREATE OR ALTER PROCEDURE\s+(dbo\.)?" + name + r"\b", b):
            return b
    raise RuntimeError(f"{name} batch missing from {path}")


def _table_batch(path: Path, line_table: str) -> str:
    for b in _batches(path.read_text(encoding="utf-8")):
        if f"CREATE TABLE [qbo].[{line_table}]" in b:
            return b
    raise RuntimeError(f"CREATE TABLE {line_table} missing")


def _drain(cur):
    while True:
        if cur.description:
            cur.fetchall()
        if not cur.nextset():
            break


@pytest.fixture(scope="module")
def cur():
    conn = pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=True)
    c = conn.cursor()
    c.execute("IF SCHEMA_ID('qbo') IS NULL EXEC('CREATE SCHEMA qbo')")
    for e in ENTITIES.values():
        c.execute(f"IF OBJECT_ID('qbo.{e['line']}','U') IS NOT NULL DROP TABLE qbo.{e['line']}")
        c.execute(f"IF OBJECT_ID('qbo.{e['header']}','U') IS NOT NULL DROP TABLE qbo.{e['header']}")
        for p in ("create", "update", "narrow", "read", "read_one", "delete"):
            if e[p]:
                c.execute(f"IF OBJECT_ID('dbo.{e[p]}','P') IS NOT NULL DROP PROCEDURE dbo.{e[p]}")
        c.execute(f"CREATE TABLE qbo.{e['header']} (Id BIGINT IDENTITY(1,1) PRIMARY KEY)")
        c.execute(_table_batch(e["file"], e["line"]))  # real base-file CREATE TABLE (fresh-build shape)
    # Put the line tables back into the PRE-column shape (prod today) so the base file's own upgrade does real work.
    for e in ENTITIES.values():
        c.execute(f"DROP INDEX IF EXISTS IX_{e['line']}_ReimburseChargeQboId ON qbo.{e['line']}")
        c.execute(f"ALTER TABLE qbo.{e['line']} DROP COLUMN ReimburseChargeQboId")
        assert c.execute(f"SELECT COL_LENGTH('qbo.{e['line']}','ReimburseChargeQboId')").fetchone()[0] is None
    for _ in range(2):  # the base file's guarded upgrade + index batches are idempotent
        for e in ENTITIES.values():
            c.execute(_upgrade_batch(e["file"], e["line"]))
            c.execute(_index_batch(e["file"], e["line"]))
    for e in ENTITIES.values():
        for p in ("create", "update", "narrow", "read", "read_one", "delete"):
            if e[p]:
                c.execute(_proc_batch(e["file"], e[p]))
    yield c
    for e in ENTITIES.values():
        c.execute(f"IF OBJECT_ID('qbo.{e['line']}','U') IS NOT NULL DROP TABLE qbo.{e['line']}")
        c.execute(f"IF OBJECT_ID('qbo.{e['header']}','U') IS NOT NULL DROP TABLE qbo.{e['header']}")
        for p in ("create", "update", "narrow", "read", "read_one", "delete"):
            if e[p]:
                c.execute(f"IF OBJECT_ID('dbo.{e[p]}','P') IS NOT NULL DROP PROCEDURE dbo.{e[p]}")
    conn.close()


def _new_header(cur, e) -> int:
    cur.execute(f"INSERT INTO qbo.{e['header']} DEFAULT VALUES; SELECT CAST(SCOPE_IDENTITY() AS BIGINT)")
    _drain_to_scalar = None
    while cur.description is None and cur.nextset():
        pass
    return int(cur.fetchone()[0])


_NC = "SET NOCOUNT ON; "


def _create(cur, e, header_id, line_id, charge=None, nc=_NC):
    ph = "@{0}=?, @QboLineId=?, @LineNum=1, @Description='d', @Amount=10, @DetailType='X'".format(e["parent"])
    if e["full"]:
        ph += (", @ItemRefValue=NULL, @ItemRefName=NULL, @AccountRefValue=NULL, @AccountRefName=NULL,"
               " @CustomerRefValue=NULL, @CustomerRefName=NULL, @ClassRefValue=NULL, @ClassRefName=NULL,"
               " @BillableStatus='Billable', @Qty=NULL, @UnitPrice=NULL, @MarkupPercent=NULL")
    else:
        ph += (", @ItemRefValue=NULL, @ItemRefName=NULL, @ClassRefValue=NULL, @ClassRefName=NULL,"
               " @UnitPrice=NULL, @Qty=NULL, @BillableStatus='Billable', @CustomerRefValue=NULL,"
               " @CustomerRefName=NULL, @AccountRefValue=NULL, @AccountRefName=NULL")
    args = [header_id, line_id]
    if charge is not None:
        ph += ", @ReimburseChargeQboId=?"
        args.append(charge)
    cur.execute(f"{nc}EXEC dbo.{e['create']} {ph}", *args)
    row = cur.fetchone()
    _drain(cur)
    return row


def _update(cur, e, row, charge=None, flag=None, nc=_NC):
    ph = "@Id=?, @LineNum=1, @Description='d2', @Amount=11, @DetailType='X'"
    args = [row.Id]
    if e["full"]:
        ph = "@Id=?, @RowVersion=?, @LineNum=1, @Description='d2', @Amount=11, @DetailType='X'"
        args = [row.Id, row.RowVersion]
        ph += (", @ItemRefValue=NULL, @ItemRefName=NULL, @AccountRefValue=NULL, @AccountRefName=NULL,"
               " @CustomerRefValue=NULL, @CustomerRefName=NULL, @ClassRefValue=NULL, @ClassRefName=NULL,"
               " @BillableStatus='Billable', @Qty=NULL, @UnitPrice=NULL, @MarkupPercent=NULL")
    else:
        ph += (", @QboLineId=?, @ItemRefValue=NULL, @ItemRefName=NULL, @ClassRefValue=NULL, @ClassRefName=NULL,"
               " @UnitPrice=NULL, @Qty=NULL, @BillableStatus='Billable', @CustomerRefValue=NULL,"
               " @CustomerRefName=NULL, @AccountRefValue=NULL, @AccountRefName=NULL")
        args.append(row.QboLineId)
    if flag is not None:
        ph += ", @ReimburseChargeQboId=?, @SetReimburseChargeQboId=?"
        args += [charge, flag]
    cur.execute(f"{nc}EXEC dbo.{e['update']} {ph}", *args)
    out = cur.fetchone()
    _drain(cur)
    return out


def _stored(cur, e, row_id):
    return cur.execute(f"SELECT ReimburseChargeQboId FROM qbo.{e['line']} WHERE Id=?", row_id).fetchone()[0]


def _narrow(cur, e, row_id, charge):
    cur.execute(f"SET NOCOUNT ON; EXEC dbo.{e['narrow']} @Id=?, @ReimburseChargeQboId=?", row_id, charge)
    rows = cur.fetchall() if cur.description else []
    _drain(cur)
    return rows


@pytest.mark.parametrize("key", list(ENTITIES))
def test_base_upgrade_added_column_and_filtered_index(cur, key):
    e = ENTITIES[key]
    assert cur.execute(f"SELECT COL_LENGTH('qbo.{e['line']}','ReimburseChargeQboId')").fetchone()[0] == 100  # NVARCHAR(50)
    idx = cur.execute(
        "SELECT has_filter, filter_definition FROM sys.indexes WHERE name=? AND object_id=OBJECT_ID(?)",
        f"IX_{e['line']}_ReimburseChargeQboId", f"qbo.{e['line']}",
    ).fetchone()
    assert idx is not None and idx[0] == 1 and "IS NOT NULL" in idx[1].upper()


@pytest.mark.parametrize("key", list(ENTITIES))
def test_create_and_read_back_two_distinct_charges(cur, key):
    e = ENTITIES[key]
    h = _new_header(cur, e)
    a = _create(cur, e, h, "1", "68056")
    b = _create(cur, e, h, "2", "99999")
    c = _create(cur, e, h, "3")  # default: no charge
    assert (a.ReimburseChargeQboId, b.ReimburseChargeQboId, c.ReimburseChargeQboId) == ("68056", "99999", None)
    cur.execute(f"SET NOCOUNT ON; EXEC dbo.{e['read']} @{e['parent']}=?", h)
    got = {r.QboLineId: r.ReimburseChargeQboId for r in cur.fetchall()}
    _drain(cur)
    assert got == {"1": "68056", "2": "99999", "3": None}
    cur.execute(f"SET NOCOUNT ON; EXEC dbo.{e['read_one']} @{e['parent']}=?, @QboLineId='2'", h)
    assert cur.fetchone().ReimburseChargeQboId == "99999"
    _drain(cur)


@pytest.mark.parametrize("key", list(ENTITIES))
def test_update_flag_semantics(cur, key):
    e = ENTITIES[key]
    h = _new_header(cur, e)
    row = _create(cur, e, h, "1", "68056")
    # legacy caller (params absent) and flag 0 + NULL both preserve
    out = _update(cur, e, row)
    assert out.ReimburseChargeQboId == "68056" and _stored(cur, e, row.Id) == "68056"
    out = _update(cur, e, row if not e["full"] else _refetch(cur, e, row), charge=None, flag=0)
    assert out.ReimburseChargeQboId == "68056"
    # flag 1 + new value overwrites
    out = _update(cur, e, _refetch(cur, e, row), charge="777", flag=1)
    assert out.ReimburseChargeQboId == "777" and _stored(cur, e, row.Id) == "777"
    # flag 1 + NULL clears
    out = _update(cur, e, _refetch(cur, e, row), charge=None, flag=1)
    assert out.ReimburseChargeQboId is None and _stored(cur, e, row.Id) is None


def _refetch(cur, e, row):
    cur.execute(f"SELECT Id, RowVersion, QboLineId FROM qbo.{e['line']} WHERE Id=?", row.Id)
    return cur.fetchone()


@pytest.mark.parametrize("key", list(ENTITIES))
def test_narrow_sproc(cur, key):
    e = ENTITIES[key]
    h = _new_header(cur, e)
    row = _create(cur, e, h, "1")
    assert [r.Id for r in _narrow(cur, e, row.Id, "68056")] == [row.Id]  # NULL -> value: 1 row
    assert _stored(cur, e, row.Id) == "68056"
    assert _narrow(cur, e, row.Id, "68056") == []                         # same value: 0 rows
    # Pass 3 P3-1: FILL-ONLY. A stored link (e.g. written by a fresher pull) is never overwritten by the backfill.
    assert _narrow(cur, e, row.Id, "70000") == []                         # value -> other value: 0 rows
    assert _stored(cur, e, row.Id) == "68056"
    with pytest.raises(pyodbc.Error):                                     # NULL rejected
        _narrow(cur, e, row.Id, None)
    assert _stored(cur, e, row.Id) == "68056"                             # and nothing was cleared
    assert _narrow(cur, e, row.Id + 10_000, "1") == []                    # unknown id: 0 rows


@pytest.mark.parametrize("key", ["bill", "purchase"])
def test_delete_output_projects_charge(cur, key):
    e = ENTITIES[key]
    h = _new_header(cur, e)
    row = _create(cur, e, h, "1", "68056")
    cur.execute(f"SET NOCOUNT ON; EXEC dbo.{e['delete']} @Id=?", row.Id)
    assert cur.fetchone().ReimburseChargeQboId == "68056"
    _drain(cur)


# --------------------------------------------------------------------------------------------
# U-778a fix round 1. These tests REBUILD the line tables, so they stay LAST in the module
# (the module fixture's earlier tests have already run; its teardown drops everything).
# --------------------------------------------------------------------------------------------
ALL_PROCS = ("create", "update", "narrow", "read", "read_one", "delete")


def _index_batch(path: Path, line_table: str) -> str:
    for b in _batches(path.read_text(encoding="utf-8")):
        if f"CREATE INDEX IX_{line_table}_ReimburseChargeQboId" in b:
            return b
    raise RuntimeError(f"ReimburseChargeQboId index batch missing for {line_table}")


def _upgrade_batch(path: Path, line_table: str):
    """The base file's own ADD-COLUMN batch for an existing table (None if the file has none)."""
    for b in _batches(path.read_text(encoding="utf-8")):
        if "ALTER TABLE" in b and "ADD [ReimburseChargeQboId]" in b and f"qbo.{line_table}" in b:
            return b
    return None


def _reset_precolumn(c, e):
    for p in ALL_PROCS:
        if e[p]:
            c.execute(f"IF OBJECT_ID('dbo.{e[p]}','P') IS NOT NULL DROP PROCEDURE dbo.{e[p]}")
    c.execute(f"IF OBJECT_ID('qbo.{e['line']}','U') IS NOT NULL DROP TABLE qbo.{e['line']}")
    c.execute(f"IF OBJECT_ID('qbo.{e['header']}','U') IS NOT NULL DROP TABLE qbo.{e['header']}")
    c.execute(f"CREATE TABLE qbo.{e['header']} (Id BIGINT IDENTITY(1,1) PRIMARY KEY)")
    c.execute(_table_batch(e["file"], e["line"]))
    c.execute(f"DROP INDEX IF EXISTS IX_{e['line']}_ReimburseChargeQboId ON qbo.{e['line']}")
    c.execute(f"ALTER TABLE qbo.{e['line']} DROP COLUMN ReimburseChargeQboId")
    assert c.execute(f"SELECT COL_LENGTH('qbo.{e['line']}','ReimburseChargeQboId')").fetchone()[0] is None


@pytest.mark.parametrize("key", list(ENTITIES))
def test_f1_base_file_alone_upgrades_existing_prod_table(cur, key):
    """Existing table WITHOUT the column + the BASE FILE only (no migration) -> column, index, sprocs, round-trip."""
    e = ENTITIES[key]
    _reset_precolumn(cur, e)
    # Fix round 2 (reviewer P3): run the base file's relevant batches IN SOURCE ORDER, exactly as run_sql.py would,
    # and pin the order itself: table -> upgrade -> index -> every touched procedure.
    batches = _batches(e["file"].read_text(encoding="utf-8"))
    table_b = _table_batch(e["file"], e["line"])
    up_b = _upgrade_batch(e["file"], e["line"])
    index_b = _index_batch(e["file"], e["line"])
    proc_bs = [_proc_batch(e["file"], e[p]) for p in ALL_PROCS if e[p]]
    assert up_b is not None, "base file has no ADD-COLUMN upgrade batch"
    pos = {id_: batches.index(b) for id_, b in (("table", table_b), ("upgrade", up_b), ("index", index_b))}
    assert pos["table"] < pos["upgrade"] < pos["index"] < min(batches.index(b) for b in proc_bs), pos
    wanted = {table_b, up_b, index_b, *proc_bs}
    for b in batches:
        if b in wanted:
            cur.execute(b)
    assert cur.execute(f"SELECT COL_LENGTH('qbo.{e['line']}','ReimburseChargeQboId')").fetchone()[0] == 100
    assert cur.execute(
        "SELECT 1 FROM sys.indexes WHERE name=? AND object_id=OBJECT_ID(?)",
        f"IX_{e['line']}_ReimburseChargeQboId", f"qbo.{e['line']}",
    ).fetchone()
    h = _new_header(cur, e)
    row = _create(cur, e, h, "1", "68056")
    assert row.ReimburseChargeQboId == "68056" and _stored(cur, e, row.Id) == "68056"


def _autocommit_off_cursor():
    conn = pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=False)
    return conn, conn.cursor()


@pytest.mark.parametrize("key", list(ENTITIES))
@pytest.mark.parametrize("kind", ["create", "update", "delete"])
def test_f2_mutation_sprocs_return_output_row_first_without_caller_nocount(cur, key, kind):
    """call_procedure does `cursor.execute('EXEC dbo.X ...')` then the repo fetchone()s: the OUTPUT row must
    be the FIRST result, with no caller-supplied SET NOCOUNT ON, autocommit OFF."""
    e = ENTITIES[key]
    if kind == "delete" and not e["delete"]:
        pytest.skip("VendorCredit DeleteQboVendorCreditLineById returns no row by design")
    _reset_precolumn(cur, e)
    cur.execute(f"ALTER TABLE qbo.{e['line']} ADD [ReimburseChargeQboId] NVARCHAR(50) NULL")
    for p in ALL_PROCS:
        if e[p]:
            cur.execute(_proc_batch(e["file"], e[p]))
    h = _new_header(cur, e)
    seeded = _create(cur, e, h, "1", "68056")
    conn, c2 = _autocommit_off_cursor()
    try:
        if kind == "create":
            got = _create(c2, e, h, "2", "99", nc="")
        elif kind == "update":
            got = _update(c2, e, _refetch(cur, e, seeded) if e["full"] else seeded, nc="")
        else:
            c2.execute(f"EXEC dbo.{e['delete']} @Id=?", seeded.Id)
            got = c2.fetchone()
        assert got is not None and got.Id is not None
        conn.commit()
    finally:
        conn.close()
