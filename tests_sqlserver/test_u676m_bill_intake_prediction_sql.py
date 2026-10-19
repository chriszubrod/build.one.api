"""U-676m: BillIntakePrediction sprocs against local SQL Server."""
from __future__ import annotations

import os
import re
from datetime import date
from decimal import Decimal
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
INTAKE_SQL = REPO_ROOT / "entities/bill_intake_prediction/sql/dbo.bill_intake_prediction.sql"


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


def _exec_batches(cur, path: Path, *, times: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    for _ in range(times):
        for batch in _batches(text):
            cur.execute(batch)


def _drop_all(cur) -> None:
    for proc in (
        "dbo.ReadBillIntakePredictionsForAccuracy",
        "dbo.ReadBillIntakePredictionByAttachmentId",
        "dbo.ReadBillIntakePredictionByBillId",
        "dbo.RecordBillIntakePredictionFinal",
        "dbo.BindBillIntakePredictionToBill",
        "dbo.UpsertBillIntakePrediction",
    ):
        cur.execute(f"IF OBJECT_ID('{proc}', 'P') IS NOT NULL DROP PROCEDURE {proc}")
    cur.execute("IF OBJECT_ID('dbo.BillIntakePrediction', 'U') IS NOT NULL DROP TABLE dbo.BillIntakePrediction")
    for tbl in ("dbo.Attachment", "dbo.Bill"):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Bill (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME()
        );
        CREATE TABLE dbo.Attachment (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ExtractedDatetime DATETIME2(3) NULL
        );
        """
    )
    _exec_batches(cur, INTAKE_SQL, times=2)


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


def _insert_attachment(cur, extracted: str) -> int:
    cur.execute(
        "INSERT INTO dbo.Attachment (ExtractedDatetime) OUTPUT INSERTED.Id VALUES (?)",
        extracted,
    )
    return int(cur.fetchone()[0])


def _insert_bill(cur) -> int:
    cur.execute("SET NOCOUNT ON; INSERT INTO dbo.Bill DEFAULT VALUES; SELECT CAST(SCOPE_IDENTITY() AS BIGINT)")
    return int(cur.fetchone()[0])


def test_upsert_insert_update_extracted_datetime_ms(conn):
    cur = conn.cursor()
    att_id = _insert_attachment(cur, "2026-05-01 12:00:00.123")
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='u654b-text-v1',
            @Status='ok',
            @PredBillNumber='INV-1'
        """,
        att_id,
    )
    row = cur.fetchone()
    assert row.PredBillNumber == "INV-1"
    assert row.ExtractedDatetime.microsecond == 123000

    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='u654b-text-v1',
            @Status='partial',
            @PredBillNumber='INV-2'
        """,
        att_id,
    )
    row2 = cur.fetchone()
    assert row2.Status == "partial" and row2.PredBillNumber == "INV-2"


def test_upsert_no_op_when_bound_or_finalized(conn):
    cur = conn.cursor()
    att_id = _insert_attachment(cur, "2026-05-01 12:00:00")
    bill_id = _insert_bill(cur)
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok',
            @PredBillNumber='KEEP'
        """,
        att_id,
    )
    cur.execute("EXEC dbo.BindBillIntakePredictionToBill @AttachmentId=?, @BillId=?", att_id, bill_id)
    cur.fetchone()
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok',
            @PredBillNumber='SHOULD-NOT-WRITE'
        """,
        att_id,
    )
    cur.execute("SELECT PredBillNumber FROM dbo.BillIntakePrediction WHERE AttachmentId=?", att_id)
    assert cur.fetchone()[0] == "KEEP"

    cur.execute(
        "EXEC dbo.RecordBillIntakePredictionFinal @BillId=?, @FinalBillNumber='F1'",
        bill_id,
    )
    cur.fetchone()
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok',
            @PredBillNumber='STILL-NO'
        """,
        att_id,
    )
    cur.execute("SELECT PredBillNumber FROM dbo.BillIntakePrediction WHERE AttachmentId=?", att_id)
    assert cur.fetchone()[0] == "KEEP"


def test_bind_final_and_reads(conn):
    cur = conn.cursor()
    att_id = _insert_attachment(cur, "2026-05-01 12:00:00.500")
    bill_id = _insert_bill(cur)
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok',
            @PredBillDate='2026-05-02',
            @PredTotalAmount=123.45,
            @CostUsd=0.001234
        """,
        att_id,
    )
    cur.execute("EXEC dbo.BindBillIntakePredictionToBill @AttachmentId=?, @BillId=?", att_id, bill_id)
    assert cur.fetchone().BillId == bill_id
    cur.execute("EXEC dbo.BindBillIntakePredictionToBill @AttachmentId=?, @BillId=?", att_id, bill_id)
    assert cur.fetchone() is None

    cur.execute(
        "EXEC dbo.RecordBillIntakePredictionFinal @BillId=?, @FinalBillNumber='FIN-1'",
        bill_id,
    )
    assert cur.fetchone().FinalBillNumber == "FIN-1"
    cur.execute(
        "EXEC dbo.RecordBillIntakePredictionFinal @BillId=?, @FinalBillNumber='FIN-2'",
        bill_id,
    )
    assert cur.fetchone() is None

    cur.execute("EXEC dbo.ReadBillIntakePredictionByAttachmentId @AttachmentId=?", att_id)
    by_att = cur.fetchone()
    assert by_att.PredBillDate == date(2026, 5, 2)
    assert Decimal(str(by_att.PredTotalAmount)) == Decimal("123.45")
    cur.execute("EXEC dbo.ReadBillIntakePredictionByBillId @BillId=?", bill_id)
    assert cur.fetchone().AttachmentId == att_id


def test_delete_attachment_without_fk_block(conn):
    cur = conn.cursor()
    att_id = _insert_attachment(cur, "2026-05-01 12:00:00")
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok'
        """,
        att_id,
    )
    cur.execute("DELETE FROM dbo.Attachment WHERE Id=?", att_id)
    cur.execute("SELECT COUNT(*) FROM dbo.BillIntakePrediction WHERE AttachmentId=?", att_id)
    assert cur.fetchone()[0] == 1


def test_rebind_to_different_bill_is_no_op(conn):
    cur = conn.cursor()
    att_id = _insert_attachment(cur, "2026-05-01 12:00:00")
    bill_a = _insert_bill(cur)
    bill_b = _insert_bill(cur)
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok'
        """,
        att_id,
    )
    cur.fetchone()
    cur.execute("EXEC dbo.BindBillIntakePredictionToBill @AttachmentId=?, @BillId=?", att_id, bill_a)
    cur.fetchone()
    cur.execute("EXEC dbo.BindBillIntakePredictionToBill @AttachmentId=?, @BillId=?", att_id, bill_b)
    assert cur.fetchone() is None
    cur.execute("SELECT BillId FROM dbo.BillIntakePrediction WHERE AttachmentId=?", att_id)
    assert cur.fetchone()[0] == bill_a


def test_upsert_no_op_when_finalized_without_bill_id(conn):
    cur = conn.cursor()
    att_id = _insert_attachment(cur, "2026-05-01 12:00:00")
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok',
            @PredBillNumber='KEEP'
        """,
        att_id,
    )
    cur.fetchone()
    cur.execute(
        """
        UPDATE dbo.BillIntakePrediction
        SET FinalizedDatetime = SYSUTCDATETIME(), FinalBillNumber = 'F-only'
        WHERE AttachmentId = ?
        """,
        att_id,
    )
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok',
            @PredBillNumber='NOPE'
        """,
        att_id,
    )
    cur.execute("SELECT PredBillNumber FROM dbo.BillIntakePrediction WHERE AttachmentId=?", att_id)
    assert cur.fetchone()[0] == "KEEP"


def test_upsert_missing_attachment_returns_empty(conn):
    cur = conn.cursor()
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok'
        """,
        999999,
    )
    assert cur.fetchone() is None


def test_delete_bill_leaves_prediction_row(conn):
    cur = conn.cursor()
    att_id = _insert_attachment(cur, "2026-05-01 12:00:00")
    bill_id = _insert_bill(cur)
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok'
        """,
        att_id,
    )
    cur.fetchone()
    cur.execute("EXEC dbo.BindBillIntakePredictionToBill @AttachmentId=?, @BillId=?", att_id, bill_id)
    cur.fetchone()
    cur.execute("DELETE FROM dbo.Bill WHERE Id=?", bill_id)
    cur.execute("SELECT COUNT(*) FROM dbo.BillIntakePrediction WHERE AttachmentId=?", att_id)
    assert cur.fetchone()[0] == 1


def test_cost_usd_decimal_round_trip(conn):
    cur = conn.cursor()
    att_id = _insert_attachment(cur, "2026-05-01 12:00:00")
    cost = Decimal("0.001234")
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='v1',
            @Status='ok',
            @CostUsd=?
        """,
        att_id,
        cost,
    )
    row = cur.fetchone()
    assert Decimal(str(row.CostUsd)) == cost
