"""U-654b: BillTextCodingSuggestion sprocs against local SQL Server."""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime
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
BILL_CODING_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_coding_suggestion.sql"
BILL_TEXT_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_text_coding_suggestion.sql"


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


def _exec_batches(cur, path: Path) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        cur.execute(batch)


def _install_bill_dominant_vote(cur) -> None:
    for batch in _batches(BILL_CODING_SQL.read_text(encoding="utf-8")):
        if "CREATE OR ALTER FUNCTION dbo.BillDominantVoteForVendor" in batch:
            cur.execute(batch)
            return
    raise RuntimeError("BillDominantVoteForVendor batch missing from bill_coding_suggestion.sql")


def _drop_all(cur) -> None:
    for proc in (
        "dbo.ClaimNextBillForTextCodingSuggestion",
        "dbo.CompleteBillTextCodingSuggestion",
        "dbo.ReadBillTextCodingSuggestionByBillId",
        "dbo.CountBillTextCodingCallsToday",
        "dbo.ReadVendorSccCountsForBills",
        "dbo.BillDominantVoteForVendor",
    ):
        kind = "IF" if proc.endswith("ForVendor") else "P"
        obj_type = "FUNCTION" if kind == "IF" else "PROCEDURE"
        cur.execute(f"IF OBJECT_ID('{proc}', '{kind}') IS NOT NULL DROP {obj_type} {proc}")
    cur.execute(
        "IF OBJECT_ID('dbo.BillTextCodingSuggestion', 'U') IS NOT NULL "
        "DROP TABLE dbo.BillTextCodingSuggestion"
    )
    for tbl in (
        "dbo.BillLineItemAttachment",
        "dbo.BillLineItem",
        "dbo.Attachment",
        "dbo.Bill",
        "dbo.SubCostCode",
        "dbo.Project",
        "dbo.Vendor",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Vendor (
            Id BIGINT NOT NULL PRIMARY KEY,
            Name NVARCHAR(255) NOT NULL
        );
        CREATE TABLE dbo.Project (
            Id BIGINT NOT NULL PRIMARY KEY,
            Abbreviation NVARCHAR(50) NULL,
            Name NVARCHAR(255) NULL
        );
        CREATE TABLE dbo.SubCostCode (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            Number NVARCHAR(20) NOT NULL,
            Name NVARCHAR(255) NOT NULL,
            IsHoldingCode BIT NOT NULL DEFAULT 0
        );
        CREATE TABLE dbo.Bill (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            VendorId BIGINT NOT NULL,
            Status NVARCHAR(20) NOT NULL DEFAULT 'draft',
            BillDate DATETIME2(3) NULL,
            CreatedDatetime DATETIME2(3) NOT NULL,
            IsDraft AS (CASE WHEN Status = 'completed' THEN CAST(0 AS BIT) ELSE CAST(1 AS BIT) END) PERSISTED
        );
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BillId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            ProjectId BIGINT NULL,
            Amount DECIMAL(18,2) NULL
        );
        CREATE TABLE dbo.Attachment (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ExtractionStatus NVARCHAR(20) NOT NULL,
            ExtractedTextBlobUrl NVARCHAR(2048) NULL,
            ExtractedDatetime DATETIME2(3) NULL
        );
        CREATE TABLE dbo.BillLineItemAttachment (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BillLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        """
    )
    _install_bill_dominant_vote(cur)
    _exec_batches(cur, BILL_TEXT_SQL)


@pytest.fixture()
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    _drop_all(cur)
    _install_schema(cur)
    yield c
    _drop_all(cur)
    c.close()


def _fetch_optional_row(cur):
    try:
        return cur.fetchone()
    except pyodbc.ProgrammingError:
        return None


def _exec_claim(cur):
    cur.execute("EXEC dbo.ClaimNextBillForTextCodingSuggestion")
    return _fetch_optional_row(cur)


def _insert_draft_with_attachment(
    cur,
    *,
    status: str = "draft",
    vendor_id: int = 1,
    project_id: int = 10,
    extracted: datetime,
    blob: str = "https://blob/x",
) -> int:
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM dbo.Vendor WHERE Id = ?)
            INSERT INTO dbo.Vendor (Id, Name) VALUES (?, 'V')
        """,
        vendor_id,
        vendor_id,
    )
    cur.execute(
        """
        INSERT INTO dbo.Bill (VendorId, Status, CreatedDatetime)
        OUTPUT INSERTED.Id VALUES (?, ?, SYSUTCDATETIME())
        """,
        vendor_id,
        status,
    )
    bill_id = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO dbo.Attachment (ExtractionStatus, ExtractedTextBlobUrl, ExtractedDatetime)
        OUTPUT INSERTED.Id VALUES ('completed', ?, ?)
        """,
        blob,
        extracted,
    )
    att_id = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO dbo.BillLineItem (BillId, ProjectId, Amount)
        OUTPUT INSERTED.Id VALUES (?, ?, 1)
        """,
        bill_id,
        project_id,
    )
    line_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (?, ?)",
        line_id,
        att_id,
    )
    return bill_id


def test_claim_skips_approved_and_completed(conn):
    cur = conn.cursor()
    _insert_draft_with_attachment(
        cur, status="approved", extracted=datetime(2026, 5, 1)
    )
    _insert_draft_with_attachment(
        cur, status="completed", extracted=datetime(2026, 5, 2)
    )
    draft_id = _insert_draft_with_attachment(
        cur, status="draft", extracted=datetime(2026, 5, 3)
    )
    row = _exec_claim(cur)
    assert row is not None
    assert int(row.BillId) == draft_id


def test_readpast_two_claims_different_bills(conn):
    cur = conn.cursor()
    _insert_draft_with_attachment(cur, extracted=datetime(2026, 5, 1))
    _insert_draft_with_attachment(cur, extracted=datetime(2026, 5, 2))
    results: list = []
    errors: list = []

    def worker():
        try:
            c = _sqlserver_connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=True)
            cc = c.cursor()
            cc.execute("EXEC dbo.ClaimNextBillForTextCodingSuggestion")
            results.append(cc.fetchone())
            c.close()
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors
    ids = {int(r.BillId) for r in results if r}
    assert len(ids) == 2


def test_extracted_datetime_supersede(conn):
    cur = conn.cursor()
    bill_id = _insert_draft_with_attachment(cur, extracted=datetime(2026, 5, 1, 10, 0, 0))
    row = _exec_claim(cur)
    assert row is not None
    cur.execute(
        """
        UPDATE dbo.Attachment
        SET ExtractedDatetime = ?
        FROM dbo.Attachment a
        INNER JOIN dbo.BillLineItemAttachment blia ON blia.AttachmentId = a.Id
        INNER JOIN dbo.BillLineItem bli ON bli.Id = blia.BillLineItemId
        WHERE bli.BillId = ?
        """,
        datetime(2026, 5, 2, 10, 0, 0),
        bill_id,
    )
    row2 = _exec_claim(cur)
    assert row2 is not None
    assert int(row2.BillId) == bill_id


def test_complete_refuses_stale_extraction(conn):
    cur = conn.cursor()
    bill_id = _insert_draft_with_attachment(cur, extracted=datetime(2026, 5, 1))
    claimed = _exec_claim(cur)
    assert claimed is not None
    cur.execute(
        """
        EXEC dbo.CompleteBillTextCodingSuggestion
            @BillId = ?,
            @AttachmentId = ?,
            @ExtractedDatetime = ?,
            @Status = 'done',
            @SubCostCodeId = 1,
            @ModelVersion = 'u654b-text-v1'
        """,
        bill_id,
        int(claimed.AttachmentId),
        datetime(2026, 4, 1),
    )
    row = _fetch_optional_row(cur)
    assert row is None
    cur.execute("EXEC dbo.ReadBillTextCodingSuggestionByBillId @BillId = ?", bill_id)
    st = cur.fetchone()
    assert st.Status == "pending"


def _complete_status(
    cur,
    bill_id: int,
    attachment_id: int,
    extracted: datetime,
    status: str,
) -> None:
    cur.execute(
        """
        EXEC dbo.CompleteBillTextCodingSuggestion
            @BillId = ?,
            @AttachmentId = ?,
            @ExtractedDatetime = ?,
            @Status = ?,
            @ModelVersion = 'u654b-text-v1'
        """,
        bill_id,
        attachment_id,
        extracted,
        status,
    )
    _fetch_optional_row(cur)


def test_claim_r3_terminal_and_lease_rules(conn):
    """Failed/skipped/done at same extraction are not re-claimed; newer extraction and stale pending are."""
    cur = conn.cursor()
    extracted = datetime(2026, 5, 1, 10, 0, 0)
    bill_failed = _insert_draft_with_attachment(cur, extracted=extracted, vendor_id=1)
    row = _exec_claim(cur)
    assert row is not None and int(row.BillId) == bill_failed
    _complete_status(cur, bill_failed, int(row.AttachmentId), extracted, "failed")
    assert _exec_claim(cur) is None

    bill_skipped = _insert_draft_with_attachment(
        cur, extracted=extracted, vendor_id=2
    )
    row = _exec_claim(cur)
    assert row is not None and int(row.BillId) == bill_skipped
    _complete_status(cur, bill_skipped, int(row.AttachmentId), extracted, "skipped")
    assert _exec_claim(cur) is None

    newer = datetime(2026, 5, 2, 10, 0, 0)
    cur.execute(
        """
        UPDATE dbo.Attachment
        SET ExtractedDatetime = ?
        FROM dbo.Attachment a
        INNER JOIN dbo.BillLineItemAttachment blia ON blia.AttachmentId = a.Id
        INNER JOIN dbo.BillLineItem bli ON bli.Id = blia.BillLineItemId
        WHERE bli.BillId = ?
        """,
        newer,
        bill_failed,
    )
    row = _exec_claim(cur)
    assert row is not None and int(row.BillId) == bill_failed

    bill_pending = _insert_draft_with_attachment(
        cur, extracted=datetime(2026, 6, 1), vendor_id=3
    )
    row = _exec_claim(cur)
    assert row is not None and int(row.BillId) == bill_pending
    assert _exec_claim(cur) is None

    cur.execute(
        """
        UPDATE dbo.BillTextCodingSuggestion
        SET ModifiedDatetime = DATEADD(MINUTE, -31, SYSUTCDATETIME())
        WHERE BillId = ?
        """,
        bill_pending,
    )
    row = _exec_claim(cur)
    assert row is not None and int(row.BillId) == bill_pending


def test_holding_code_excluded_from_top_n(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (1, 'V')")
    for scc_id, num, holding in ((1, "A", 0), (2, "HOLD", 1)):
        cur.execute(
            "INSERT INTO dbo.SubCostCode (Id, Number, Name, IsHoldingCode) VALUES (?, ?, ?, ?)",
            scc_id,
            num,
            num,
            holding,
        )
    for i in range(3):
        cur.execute(
            """
            INSERT INTO dbo.Bill (VendorId, Status, CreatedDatetime)
            OUTPUT INSERTED.Id
            VALUES (1, 'completed', DATEADD(day, ?, '2026-01-01'))
            """,
            -i,
        )
        bid = int(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId, ProjectId, Amount) VALUES (?, 1, 10, 10)",
            bid,
        )
    cur.execute(
        """
        INSERT INTO dbo.Bill (VendorId, Status, CreatedDatetime)
        OUTPUT INSERTED.Id VALUES (1, 'completed', '2026-01-05')
        """
    )
    bid = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId, ProjectId, Amount) VALUES (?, 2, 10, 10)",
        bid,
    )
    cur.execute(
        "EXEC dbo.ReadVendorSccCountsForBills @VendorId = 1, @AsOf = '2026-06-01', @Top = 5"
    )
    rows = cur.fetchall()
    numbers = [r.Number for r in rows]
    assert "HOLD" not in numbers
    assert numbers[0] == "A"


def test_empty_queue_claim_returns_no_row(conn):
    cur = conn.cursor()
    row = _exec_claim(cur)
    assert row is None


def _drain_result_sets(cur) -> None:
    while True:
        if cur.description:
            cur.fetchall()
        if not cur.nextset():
            break


def test_millisecond_claim_complete_done(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.SubCostCode (Id, Number, Name) VALUES (1, 'A', 'Alpha')"
    )
    extracted = datetime(2026, 5, 1, 10, 0, 0, 123000)
    bill_id = _insert_draft_with_attachment(cur, extracted=extracted)
    claimed = _exec_claim(cur)
    assert claimed is not None
    assert claimed.ExtractedDatetime == extracted
    cur.execute(
        """
        EXEC dbo.CompleteBillTextCodingSuggestion
            @BillId = ?,
            @AttachmentId = ?,
            @ExtractedDatetime = ?,
            @Status = 'done',
            @SubCostCodeId = 1,
            @ModelVersion = 'u654b-text-v1'
        """,
        bill_id,
        int(claimed.AttachmentId),
        extracted,
    )
    done_row = cur.fetchone()
    assert done_row is not None
    assert done_row.Status == "done"
    assert done_row.ExtractedDatetime == extracted
    _drain_result_sets(cur)


def test_concurrent_claims_no_double_claim_same_bill(conn):
    """Regression B: two claimers must not both MERGE-reset the same pending row."""
    cur = conn.cursor()
    extracted = datetime(2026, 5, 1, 10, 0, 0)

    def race_once(*, autocommit: bool) -> None:
        bid = _insert_draft_with_attachment(cur, extracted=extracted, vendor_id=50 + autocommit)
        barrier = threading.Barrier(2)
        wins: list[int] = []
        errors: list[Exception] = []

        def worker():
            try:
                c = _sqlserver_connect(
                    os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=autocommit
                )
                cc = c.cursor()
                barrier.wait(timeout=30)
                cc.execute("EXEC dbo.ClaimNextBillForTextCodingSuggestion")
                row = cc.fetchone()
                if row and row.BillId is not None:
                    wins.append(int(row.BillId))
                if not autocommit:
                    c.commit()
                c.close()
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert not errors
        assert wins.count(bid) <= 1
        cur.execute(
            "SELECT COUNT(*) FROM dbo.BillTextCodingSuggestion WHERE BillId = ?",
            bid,
        )
        assert int(cur.fetchone()[0]) == 1

    for autocommit in (True, False):
        for _ in range(10):
            race_once(autocommit=autocommit)


def test_claim_picks_latest_completed_attachment(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (1, 'V')")
    cur.execute(
        """
        INSERT INTO dbo.Bill (VendorId, Status, CreatedDatetime)
        OUTPUT INSERTED.Id VALUES (1, 'draft', SYSUTCDATETIME())
        """
    )
    bill_id = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO dbo.BillLineItem (BillId, ProjectId, Amount)
        OUTPUT INSERTED.Id VALUES (?, 10, 1)
        """,
        bill_id,
    )
    bli_id = int(cur.fetchone()[0])
    older = datetime(2026, 5, 1, 9, 0, 0)
    newer = datetime(2026, 5, 1, 11, 0, 0)
    cur.execute(
        """
        INSERT INTO dbo.Attachment (ExtractionStatus, ExtractedTextBlobUrl, ExtractedDatetime)
        OUTPUT INSERTED.Id VALUES ('completed', 'https://old', ?)
        """,
        older,
    )
    old_att = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO dbo.Attachment (ExtractionStatus, ExtractedTextBlobUrl, ExtractedDatetime)
        OUTPUT INSERTED.Id VALUES ('completed', 'https://new', ?)
        """,
        newer,
    )
    new_att = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (?, ?)",
        bli_id,
        old_att,
    )
    cur.execute(
        "INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (?, ?)",
        bli_id,
        new_att,
    )
    claimed = _exec_claim(cur)
    assert claimed is not None
    assert int(claimed.AttachmentId) == new_att
    assert claimed.ExtractedDatetime == newer


def test_call_datetime_counts_only_recorded_calls(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.SubCostCode (Id, Number, Name) VALUES (1, 'A', 'Alpha')"
    )
    extracted = datetime(2026, 5, 1, 10, 0, 0)
    bill_id = _insert_draft_with_attachment(cur, extracted=extracted)
    claimed = _exec_claim(cur)
    assert claimed is not None
    cur.execute(
        """
        EXEC dbo.CompleteBillTextCodingSuggestion
            @BillId = ?,
            @AttachmentId = ?,
            @ExtractedDatetime = ?,
            @Status = 'skipped',
            @FailureReason = 'history_strong',
            @ModelVersion = 'u654b-text-v1',
            @RecordCall = 0
        """,
        bill_id,
        int(claimed.AttachmentId),
        extracted,
    )
    _drain_result_sets(cur)
    cur.execute("EXEC dbo.CountBillTextCodingCallsToday")
    assert int(cur.fetchone().CallCount) == 0
    cur.execute(
        """
        EXEC dbo.CompleteBillTextCodingSuggestion
            @BillId = ?,
            @AttachmentId = ?,
            @ExtractedDatetime = ?,
            @Status = 'done',
            @SubCostCodeId = 1,
            @ModelVersion = 'u654b-text-v1',
            @RecordCall = 1
        """,
        bill_id,
        int(claimed.AttachmentId),
        extracted,
    )
    _drain_result_sets(cur)
    cur.execute("EXEC dbo.CountBillTextCodingCallsToday")
    assert int(cur.fetchone().CallCount) == 1
