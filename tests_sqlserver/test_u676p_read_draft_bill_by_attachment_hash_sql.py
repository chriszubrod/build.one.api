"""U-676p: ReadDraftBillByAttachmentHash against stub tables."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pyodbc
import pytest


def _sqlserver_connect(conn_str: str, *, autocommit: bool = False):
    return pyodbc.connect(conn_str, autocommit=autocommit)


pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
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
    raise RuntimeError(
        "tests_sqlserver refuses to run: SERVER in BUILDONE_TEST_SQLSERVER must be 127.0.0.1 or localhost"
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
BLIA_SQL = REPO_ROOT / "entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql"


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


def _exec_batch(cur, batch: str) -> None:
    cur.execute(batch)


def _install_sproc(cur) -> None:
    text = BLIA_SQL.read_text(encoding="utf-8")
    start = text.index("CREATE OR ALTER PROCEDURE ReadDraftBillByAttachmentHash")
    end = text.index("GO", start)
    batch = text[start:end]
    cur.execute(batch)


def _drop_all(cur) -> None:
    cur.execute(
        "IF OBJECT_ID('dbo.ReadDraftBillByAttachmentHash', 'P') IS NOT NULL "
        "DROP PROCEDURE dbo.ReadDraftBillByAttachmentHash"
    )
    for fn in ("dbo.UserCanAccessBill",):
        cur.execute(f"IF OBJECT_ID('{fn}', 'FN') IS NOT NULL DROP FUNCTION {fn}")
    for tbl in (
        "dbo.BillLineItemAttachment",
        "dbo.BillLineItem",
        "dbo.Attachment",
        "dbo.Bill",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Bill (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL,
            Status NVARCHAR(32) NOT NULL,
            IsDraft AS (
                CASE WHEN Status = 'completed' THEN CAST(0 AS BIT) ELSE CAST(1 AS BIT) END
            ) PERSISTED
        );
        CREATE TABLE dbo.Attachment (
            Id BIGINT NOT NULL PRIMARY KEY,
            FileHash NVARCHAR(64) NULL
        );
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT NOT NULL PRIMARY KEY,
            BillId BIGINT NOT NULL
        );
        CREATE TABLE dbo.BillLineItemAttachment (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BillLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        EXEC('
        CREATE FUNCTION dbo.UserCanAccessBill(
            @ActorUserId BIGINT,
            @ActorIsSystemAdmin BIT,
            @BillId BIGINT
        ) RETURNS BIT AS BEGIN
            IF @ActorIsSystemAdmin = 1 RETURN 1;
            IF @BillId = 2 RETURN 0;
            RETURN 1;
        END');
        """
    )
    _install_sproc(cur)


@pytest.fixture()
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    name = cur.execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    _drop_all(cur)
    _install_schema(cur)
    yield c
    _drop_all(cur)
    c.close()


def _link_bill_attachment(cur, *, bill_id: int, status: str, att_id: int, file_hash: str):
    pid = uuid.uuid4()
    cur.execute(
        "INSERT INTO dbo.Bill (Id, PublicId, Status) VALUES (?, ?, ?)",
        bill_id,
        pid,
        status,
    )
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, FileHash) VALUES (?, ?)",
        att_id,
        file_hash,
    )
    cur.execute(
        "INSERT INTO dbo.BillLineItem (Id, BillId) VALUES (?, ?)",
        bill_id * 10,
        bill_id,
    )
    cur.execute(
        "INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (?, ?)",
        bill_id * 10,
        att_id,
    )
    return pid


def _call_sproc(cur, *, file_hash: str, actor: int = 17, admin: int = 0):
    cur.execute(
        "EXEC dbo.ReadDraftBillByAttachmentHash @FileHash=?, "
        "@ActorUserId=?, @ActorIsSystemAdmin=?",
        file_hash,
        actor,
        admin,
    )
    row = cur.fetchone()
    while cur.nextset():  # drain so the connection is free for the next command
        pass
    return str(row.PublicId).lower() if row else None


def test_finds_accessible_pure_draft_by_hash(conn):
    cur = conn.cursor()
    pid = _link_bill_attachment(
        cur, bill_id=1, status="draft", att_id=100, file_hash="abc"
    )
    found = _call_sproc(cur, file_hash="abc")
    assert found == str(pid).lower()


def test_ignores_non_draft_status(conn):
    cur = conn.cursor()
    _link_bill_attachment(
        cur, bill_id=1, status="submitted", att_id=100, file_hash="abc"
    )
    assert _call_sproc(cur, file_hash="abc") is None


def test_ignores_inaccessible_bill(conn):
    cur = conn.cursor()
    _link_bill_attachment(
        cur, bill_id=2, status="draft", att_id=100, file_hash="abc"
    )
    assert _call_sproc(cur, file_hash="abc") is None


def test_includes_same_attachment_for_retry(conn):
    cur = conn.cursor()
    pid = _link_bill_attachment(
        cur, bill_id=1, status="draft", att_id=100, file_hash="abc"
    )
    assert _call_sproc(cur, file_hash="abc") == str(pid).lower()


def test_returns_newest_draft_when_multiple_match(conn):
    cur = conn.cursor()
    older = _link_bill_attachment(
        cur, bill_id=1, status="draft", att_id=100, file_hash="abc"
    )
    newer = _link_bill_attachment(
        cur, bill_id=3, status="draft", att_id=101, file_hash="abc"
    )
    found = _call_sproc(cur, file_hash="abc")
    assert found == str(newer).lower()
    assert found != str(older).lower()
