"""U-535b b1: dbo.attachment_backfill.sql against local SQL Server (synthetic rows only)."""
from __future__ import annotations

import os
import re
import uuid
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
# Test-only override: lets a reviewer run this module against an earlier cut of the file to see it red.
SQL_FILE = Path(os.environ.get("U535B_SQL_FILE") or REPO_ROOT / "entities/attachment_backfill/sql/dbo.attachment_backfill.sql")

PROCS = (
    "CreateAttachmentBackfillRun",
    "DeleteStaleAttachmentBackfillCandidates",
    "InsertAttachmentBackfillCandidate",
    "ReadAttachmentBackfillCandidatesForVerification",
    "ReadAttachmentBackfillRunByPublicId",
    "ReadAttachmentBackfillRunSummary",
    "ReadBillsForAttachmentBackfill",
    "ReadOpenAttachmentBackfillRuns",
    "UpdateAttachmentBackfillCandidateVerification",
    "UpdateAttachmentBackfillRunStatus",
)
TABLES = (
    "dbo.AttachmentBackfillCandidate",
    "dbo.AttachmentBackfillRun",
    "dbo.BillLineItemAttachment",
    "dbo.BillLineItem",
    "dbo.Bill",
    "dbo.Vendor",
)


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


def _exec_file(cur) -> None:
    for batch in _batches(SQL_FILE.read_text(encoding="utf-8")):
        cur.execute(batch)


def _drain(cur) -> None:
    while True:
        try:
            if not cur.nextset():
                break
        except pyodbc.Error:
            break


def _drop_all(cur) -> None:
    for proc in PROCS:
        cur.execute(f"IF OBJECT_ID('dbo.{proc}', 'P') IS NOT NULL DROP PROCEDURE dbo.{proc}")
    for tbl in TABLES:
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_base(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Vendor (Id BIGINT NOT NULL PRIMARY KEY, Name NVARCHAR(255) NULL);
        CREATE TABLE dbo.Bill (
            Id BIGINT NOT NULL PRIMARY KEY,
            VendorId BIGINT NULL,
            BillDate DATETIME2(3) NOT NULL,
            BillNumber NVARCHAR(50) NULL,
            TotalAmount DECIMAL(18,2) NULL,
            Memo NVARCHAR(MAX) NULL,
            Status NVARCHAR(32) NOT NULL,
            IsDraft AS (CASE WHEN Status = 'draft' THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END) PERSISTED,
            CompanyId BIGINT NOT NULL DEFAULT 1
        );
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT NOT NULL PRIMARY KEY,
            BillId BIGINT NOT NULL,
            ProjectId BIGINT NULL,
            Description NVARCHAR(MAX) NULL
        );
        CREATE TABLE dbo.BillLineItemAttachment (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BillLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        """
    )
    _drain(cur)


@pytest.fixture()
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    setup = pyodbc.connect(conn_str, autocommit=True)
    # A pooled handle can come back with autocommit OFF from a previous test's app connection;
    # set it explicitly or the DDL below sits in an open transaction and blocks the app connection.
    setup.autocommit = True
    cur = setup.cursor()
    name = cur.execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    _drop_all(cur)
    _install_base(cur)
    _exec_file(cur)
    _seed(cur)
    # The app's shape: pyodbc autocommit OFF, so every sproc runs inside an implicit outer txn.
    app = pyodbc.connect(conn_str, autocommit=False)
    app.autocommit = False
    yield app
    app.rollback()
    app.close()
    _drop_all(cur)
    setup.close()


def _seed(cur) -> None:
    cur.execute(
        """
        INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'Acme Lumber'), (2, N'Other Co');
        INSERT INTO dbo.Bill (Id, VendorId, BillDate, BillNumber, TotalAmount, Memo, Status, CompanyId) VALUES
            (101, 1, '2023-01-02', N'INV-1', 100.00, N'memo text', 'completed', 1),  -- unlinked, 2 projects
            (102, 1, '2023-01-03', N'INV-2', 200.00, NULL, 'completed', 1),          -- linked on one line
            (103, 2, '2023-01-04', N'INV-3', 300.00, NULL, 'draft', 1),              -- draft
            (104, 2, '2023-01-05', N'INV-4', 400.00, NULL, 'completed', 2),          -- other company
            (105, 2, '2023-01-06', N'INV-5', 500.00, NULL, 'completed', 1);          -- unlinked, no lines
        INSERT INTO dbo.BillLineItem (Id, BillId, ProjectId, Description) VALUES
            (1, 101, 10, N'desc'), (2, 101, 11, N'desc'), (3, 101, 10, N'desc'),
            (4, 102, 10, NULL), (5, 102, 10, NULL),
            (6, 103, 10, NULL), (7, 104, 10, NULL);
        INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (5, 900);
        """
    )
    _drain(cur)


def _rows(cur, sql: str, *params) -> list:
    cur.execute(sql, params)
    rows = cur.fetchall() if cur.description else []
    _drain(cur)
    return rows


def _create_run(app) -> tuple[int, str]:
    cur = app.cursor()
    row = _rows(cur, "EXEC dbo.CreateAttachmentBackfillRun @Source=?, @Notes=?, @CreatedByUserId=?", "box", None, None)[0]
    app.commit()
    assert row.Status == "generating" and row.CreatedByUserId == 17
    return int(row.Id), str(row.PublicId)


def _insert(app, run_id, bill_id, file_id, tier="A", size=None):
    cur = app.cursor()
    sql = (
        "EXEC dbo.InsertAttachmentBackfillCandidate @RunId=?, @BillId=?, @SourceFileId=?, @SourceFileName=?, "
        "@SourceFolderId=?, @MatchTier=?"
    )
    params = [run_id, bill_id, file_id, f"name-{file_id}.pdf", "fold", tier]
    if size is not None:
        sql += ", @SourceFileSize=?"
        params.append(size)
    rows = _rows(cur, sql, *params)
    app.commit()
    return rows[0] if rows else None


def _verify(app, cand_id, status="checked", sha="a" * 64):
    cur = app.cursor()
    rows = _rows(
        cur,
        "EXEC dbo.UpdateAttachmentBackfillCandidateVerification @Id=?, @SourceSha256=?, @CheckStatus=?, "
        "@InvoiceSeenCount=?, @AmountSeenCount=?, @VendorSeen=?, @UsedOcr=?",
        cand_id, sha, status, 1, 1, 0, 0,
    )
    app.commit()
    return rows[0] if rows else None


def test_file_rerun_is_idempotent(conn):
    cur = conn.cursor()
    _exec_file(cur)
    conn.commit()
    _exec_file(cur)
    conn.commit()
    names = {r[0] for r in _rows(cur, "SELECT name FROM sys.procedures WHERE name LIKE '%AttachmentBackfill%'")}
    assert names == set(PROCS)


def test_tables_constraints_and_index(conn):
    cur = conn.cursor()
    uqs = {r[0] for r in _rows(cur, "SELECT name FROM sys.key_constraints WHERE type = 'UQ' AND name LIKE 'UQ_AttachmentBackfill%'")}
    assert {"UQ_AttachmentBackfillCandidate_Run_SourceFile", "UQ_AttachmentBackfillCandidate_Run_Bill",
            "UQ_AttachmentBackfillRun_PublicId", "UQ_AttachmentBackfillCandidate_PublicId"} <= uqs
    fks = {r[0] for r in _rows(cur, "SELECT name FROM sys.foreign_keys WHERE name LIKE 'FK_AttachmentBackfill%'")}
    assert fks == {"FK_AttachmentBackfillCandidate_Run"}  # no FK to dbo.Bill (fix round 1)
    assert _rows(cur, "SELECT 1 FROM sys.indexes WHERE name = 'IX_AttachmentBackfillCandidate_Run_Review_Check'")
    with pytest.raises(pyodbc.IntegrityError):
        cur.execute("INSERT INTO dbo.AttachmentBackfillRun (Source, CreatedByUserId) VALUES (N'sharepoint', 1)")
    conn.rollback()


def test_read_bills_excludes_linked_drafts_and_other_company(conn):
    cur = conn.cursor()
    rows = _rows(cur, "EXEC dbo.ReadBillsForAttachmentBackfill @CompanyId=?", 1)
    by_id = {int(r.Id): r for r in rows}
    assert set(by_id) == {101, 105}
    assert sorted(by_id[101].ProjectIds.split(",")) == ["10", "11"]
    assert by_id[105].ProjectIds is None
    assert by_id[101].TotalAmount == Decimal("100.00") and by_id[101].BillNumber == "INV-1"
    cur.execute("EXEC dbo.ReadBillsForAttachmentBackfill @CompanyId=?", 1)
    columns = {c[0] for c in cur.description}
    _drain(cur)
    assert columns == {"Id", "BillNumber", "TotalAmount", "ProjectIds"}  # no memo, description or vendor
    assert {int(r.Id) for r in _rows(cur, "EXEC dbo.ReadBillsForAttachmentBackfill @CompanyId=?", 2)} == {104}


def test_insert_idempotent_and_unique_per_run(conn):
    run_id, _ = _create_run(conn)
    first = _insert(conn, run_id, 101, "f1")
    assert first.Inserted is True
    again = _insert(conn, run_id, 101, "f1")
    assert again.Inserted is False and again.Id == first.Id
    # Another file claiming the same bill in the same run is refused (empty result set).
    refused = _insert(conn, run_id, 101, "f2")
    assert refused.Id is None and refused.RefusedReason == "bill_claimed_in_run"
    # Same file id for another bill: returned unchanged (still bill 101).
    same_file = _insert(conn, run_id, 105, "f1")
    assert same_file.Inserted is False and int(same_file.BillId) == 101
    cur = conn.cursor()
    assert _rows(cur, "SELECT COUNT(*) FROM dbo.AttachmentBackfillCandidate WHERE RunId = ?", run_id)[0][0] == 1
    # A second run may hold the same (file, bill) pair.
    # Another run may hold the same (file, bill) pair once this run is cancelled.
    _rows(cur, "EXEC dbo.UpdateAttachmentBackfillRunStatus @Id=?, @Status=?", run_id, "cancelled")
    conn.commit()
    run2, _ = _create_run(conn)
    assert _insert(conn, run2, 101, "f1").Inserted is True
    with pytest.raises(pyodbc.IntegrityError):
        cur.execute(
            "INSERT INTO dbo.AttachmentBackfillCandidate (RunId, BillId, SourceFileId, SourceFileName, MatchTier) "
            "VALUES (?, 105, N'f1', N'x', N'A')",
            run_id,
        )
    conn.rollback()


def test_insert_refused_unless_run_generating(conn):
    run_id, _ = _create_run(conn)
    cur = conn.cursor()
    row = _rows(cur, "EXEC dbo.UpdateAttachmentBackfillRunStatus @Id=?, @Status=?", run_id, "ready")[0]
    conn.commit()
    assert row.Status == "ready"
    refused = _insert(conn, run_id, 101, "f1")
    assert refused.Id is None and refused.RefusedReason == "run_not_generating"
    _rows(cur, "EXEC dbo.UpdateAttachmentBackfillRunStatus @Id=?, @Status=?", run_id, "cancelled")
    conn.commit()
    # Terminal: no further status change.
    assert _rows(cur, "EXEC dbo.UpdateAttachmentBackfillRunStatus @Id=?, @Status=?", run_id, "generating") == []
    conn.commit()


def test_run_read_by_public_id(conn):
    run_id, public_id = _create_run(conn)
    cur = conn.cursor()
    row = _rows(cur, "EXEC dbo.ReadAttachmentBackfillRunByPublicId @PublicId=?", public_id)[0]
    assert int(row.Id) == run_id and row.Source == "box"
    assert _rows(cur, "EXEC dbo.ReadAttachmentBackfillRunByPublicId @PublicId=?", str(uuid.uuid4())) == []


def test_verification_never_changes_review_status(conn):
    run_id, _ = _create_run(conn)
    cand = _insert(conn, run_id, 101, "f1")
    cand2 = _insert(conn, run_id, 105, "f5")
    cand3 = _insert(conn, run_id, 102, "f2")
    _mark(conn, run_id, "ready")
    row = _verify(conn, cand.Id, "checked")
    assert row.CheckStatus == "checked" and row.ReviewStatus == "pending" and row.SourceSha256 == "a" * 64
    cur = conn.cursor()
    # A reviewed (b2) candidate is never re-verified, and its ReviewStatus stays as it was.
    cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET ReviewStatus = N'approved' WHERE Id = ?", cand.Id)
    conn.commit()
    refused = _verify(conn, cand.Id, "unreadable")
    assert refused.Id is None and refused.RefusedReason == "reviewed_or_linked"
    after = _rows(cur, "SELECT ReviewStatus, CheckStatus FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", cand.Id)[0]
    assert (after.ReviewStatus, after.CheckStatus) == ("approved", "checked")
    # A linked candidate is never re-verified either.
    cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET AttachmentId = 1 WHERE Id = ?", cand2.Id)
    conn.commit()
    assert _verify(conn, cand2.Id).RefusedReason == "reviewed_or_linked"
    # The CheckStatus CHECK holds on a writable (pending, unlinked) candidate.
    with pytest.raises(pyodbc.IntegrityError):
        _verify(conn, cand3.Id, "bogus")
    conn.rollback()


def test_read_for_verification_keyset_and_errors(conn):
    run_id, _ = _create_run(conn)
    c1 = _insert(conn, run_id, 101, "f1", size=1234)
    c2 = _insert(conn, run_id, 105, "f5")
    cur = conn.cursor()
    # Fix round 1: only a 'ready' run is verified.
    assert _rows(cur, "EXEC dbo.ReadAttachmentBackfillCandidatesForVerification @RunId=?, @Top=?", run_id, 10) == []
    _mark(conn, run_id, "ready")
    rows = _rows(cur, "EXEC dbo.ReadAttachmentBackfillCandidatesForVerification @RunId=?, @Top=?", run_id, 10)
    assert rows[0].SourceFileSize == 1234 and rows[1].SourceFileSize is None
    assert [int(r.Id) for r in rows] == [c1.Id, c2.Id]
    assert rows[0].BillNumber == "INV-1" and rows[0].VendorName == "Acme Lumber"
    rows = _rows(
        cur, "EXEC dbo.ReadAttachmentBackfillCandidatesForVerification @RunId=?, @Top=?, @AfterId=?", run_id, 10, c1.Id
    )
    assert [int(r.Id) for r in rows] == [c2.Id]
    _verify(conn, c1.Id, "error", sha=None)
    _verify(conn, c2.Id, "checked")
    assert _rows(cur, "EXEC dbo.ReadAttachmentBackfillCandidatesForVerification @RunId=?, @Top=?", run_id, 10) == []
    rows = _rows(
        cur,
        "EXEC dbo.ReadAttachmentBackfillCandidatesForVerification @RunId=?, @Top=?, @AfterId=?, @IncludeErrors=?",
        run_id, 10, 0, 1,
    )
    assert [int(r.Id) for r in rows] == [c1.Id]


def test_run_summary_counts(conn):
    run_id, _ = _create_run(conn)
    c1 = _insert(conn, run_id, 101, "f1")
    _insert(conn, run_id, 105, "f5")
    _mark(conn, run_id, "ready")
    _verify(conn, c1.Id, "checked")
    cur = conn.cursor()
    rows = _rows(cur, "EXEC dbo.ReadAttachmentBackfillRunSummary @RunId=?", run_id)
    got = {(r.MatchTier, r.ReviewStatus): (int(r.CandidateCount), int(r.Unchecked), int(r.Checked)) for r in rows}
    assert got == {("A", "pending"): (2, 1, 1)}


def _mark(app, run_id, status):
    cur = app.cursor()
    rows = _rows(cur, "EXEC dbo.UpdateAttachmentBackfillRunStatus @Id=?, @Status=?", run_id, status)
    app.commit()
    return rows[0] if rows else None


def test_bill_delete_leaves_stale_candidate(conn):
    run_id, _ = _create_run(conn)
    _insert(conn, run_id, 101, "f1")
    cur = conn.cursor()
    cur.execute("DELETE FROM dbo.Bill WHERE Id = 101")
    conn.commit()
    assert _rows(cur, "SELECT COUNT(*) FROM dbo.AttachmentBackfillCandidate WHERE BillId = 101")[0][0] == 1
    _mark(conn, run_id, "ready")
    # A stale candidate (bill gone) is never handed to the verifier.
    assert _rows(cur, "EXEC dbo.ReadAttachmentBackfillCandidatesForVerification @RunId=?, @Top=?", run_id, 10) == []


def test_bill_with_active_candidate_in_another_run_is_refused(conn):
    run1, _ = _create_run(conn)
    _insert(conn, run1, 101, "f1")
    _mark(conn, run1, "ready")
    run2, _ = _create_run(conn)
    refused = _insert(conn, run2, 101, "f9")
    assert refused.Id is None and refused.RefusedReason == "bill_claimed_elsewhere"
    cur = conn.cursor()
    # A rejected candidate does not block, nor does a cancelled run.
    cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET ReviewStatus = N'rejected' WHERE RunId = ?", run1)
    conn.commit()
    assert _insert(conn, run2, 101, "f9").Inserted is True
    run3, _ = _create_run(conn)
    _insert(conn, run3, 105, "f5")
    _mark(conn, run3, "cancelled")
    run4, _ = _create_run(conn)
    assert _insert(conn, run4, 105, "f5").Inserted is True
    # An approved candidate in a live run blocks.
    cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET ReviewStatus = N'approved' WHERE RunId = ?", run2)
    conn.commit()
    run5, _ = _create_run(conn)
    assert _insert(conn, run5, 101, "f10").RefusedReason == "bill_claimed_elsewhere"


def test_run_status_transitions(conn):
    run_id, _ = _create_run(conn)
    assert _mark(conn, run_id, "applied") is None  # generating -> applied refused
    assert _mark(conn, run_id, "ready").Status == "ready"
    assert _mark(conn, run_id, "generating") is None  # no going back
    assert _mark(conn, run_id, "ready") is None
    assert _mark(conn, run_id, "applied").Status == "applied"
    assert _mark(conn, run_id, "cancelled") is None  # never out of applied


# -- fix round 2 ----------------------------------------------------------------------- #


def _verify2(app, cand_id, status="checked", statement_like=None):
    cur = app.cursor()
    sql = (
        "EXEC dbo.UpdateAttachmentBackfillCandidateVerification @Id=?, @SourceSha256=?, @CheckStatus=?, "
        "@InvoiceSeenCount=?, @AmountSeenCount=?, @VendorSeen=?, @UsedOcr=?"
    )
    params = [cand_id, "b" * 64, status, 1, 1, 0, 0]
    if statement_like is not None:
        sql += ", @StatementLike=?"
        params.append(statement_like)
    rows = _rows(cur, sql, *params)
    app.commit()
    return rows[0] if rows else None


def test_previously_rejected_pair_is_refused(conn):
    run1, _ = _create_run(conn)
    _insert(conn, run1, 101, "f1")
    cur = conn.cursor()
    cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET ReviewStatus = N'rejected' WHERE RunId = ?", run1)
    conn.commit()
    run2, _ = _create_run(conn)
    refused = _insert(conn, run2, 101, "f1")
    assert refused.Id is None and refused.RefusedReason == "previously_rejected"
    # Another file for the same bill is still allowed.
    assert _insert(conn, run2, 101, "f2").Inserted is True


def test_verification_update_requires_ready_run(conn):
    run_id, _ = _create_run(conn)
    cand = _insert(conn, run_id, 101, "f1")
    refused = _verify2(conn, cand.Id)
    assert refused is not None and refused.Id is None and refused.RefusedReason == "run_not_ready"
    _mark(conn, run_id, "ready")
    ok = _verify2(conn, cand.Id, "checked", statement_like=1)
    assert ok.Id == cand.Id and ok.CheckStatus == "checked"
    cur = conn.cursor()
    row = _rows(cur, "SELECT StatementLike FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", cand.Id)[0]
    assert row.StatementLike is True
    summary = _rows(cur, "EXEC dbo.ReadAttachmentBackfillRunSummary @RunId=?", run_id)
    assert int(summary[0].StatementLike) == 1


def test_delete_stale_candidates_on_resume(conn):
    import json

    run_id, _ = _create_run(conn)
    keep = _insert(conn, run_id, 101, "f1")
    stale = _insert(conn, run_id, 105, "f5")
    reviewed = _insert(conn, run_id, 102, "f2")
    cur = conn.cursor()
    cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET ReviewStatus = N'rejected' WHERE Id = ?", reviewed.Id)
    conn.commit()
    plan = json.dumps([{"f": "f1", "b": 101}, {"f": "f1", "b": 101}])  # duplicates tolerated by the @Keep PK
    row = _rows(cur, "EXEC dbo.DeleteStaleAttachmentBackfillCandidates @RunId=?, @KeepJson=?", run_id, plan)[0]
    conn.commit()
    assert int(row.Deleted) == 1
    left = {int(r.Id) for r in _rows(cur, "SELECT Id FROM dbo.AttachmentBackfillCandidate WHERE RunId = ?", run_id)}
    assert left == {keep.Id, reviewed.Id} and stale.Id not in left
    # Never on a run that is not generating.
    _mark(conn, run_id, "ready")
    row = _rows(cur, "EXEC dbo.DeleteStaleAttachmentBackfillCandidates @RunId=?, @KeepJson=?", run_id, "[]")[0]
    conn.commit()
    assert int(row.Deleted) == 0


def test_read_open_runs_by_source(conn):
    r1, p1 = _create_run(conn)
    r2, p2 = _create_run(conn)
    r3, _ = _create_run(conn)
    _mark(conn, r2, "ready")
    _mark(conn, r3, "cancelled")
    cur = conn.cursor()
    rows = _rows(cur, "EXEC dbo.ReadOpenAttachmentBackfillRuns @Source=?", "box")
    assert [(int(r.Id), r.Status) for r in rows] == [(r2, "ready"), (r1, "generating")]
    assert _rows(cur, "EXEC dbo.ReadOpenAttachmentBackfillRuns @Source=?", "qbo") == []


# -- neutral evidence ------------------------------------------------------------- #


def _evidence(app, cand_id, status="checked", seen=1, amount=1, vendor=1, other=0, stmt=0, ocr=0):
    cur = app.cursor()
    rows = _rows(
        cur,
        "EXEC dbo.UpdateAttachmentBackfillCandidateVerification @Id=?, @SourceSha256=?, @CheckStatus=?, "
        "@InvoiceSeenCount=?, @AmountSeenCount=?, @VendorSeen=?, @OtherInvoiceLabelCount=?, @StatementLike=?, @UsedOcr=?",
        cand_id, "c" * 64, status, seen, amount, vendor, other, stmt, ocr,
    )
    app.commit()
    return rows[0] if rows else None


def test_evidence_columns_and_status_check(conn):
    run_id, _ = _create_run(conn)
    c1 = _insert(conn, run_id, 101, "f1")
    c2 = _insert(conn, run_id, 105, "f5")
    _mark(conn, run_id, "ready")
    assert _evidence(conn, c1.Id, seen=2, amount=None, other=1, stmt=1).CheckStatus == "checked"
    cur = conn.cursor()
    row = _rows(
        cur,
        "SELECT CheckStatus, InvoiceSeenCount, AmountSeenCount, VendorSeen, OtherInvoiceLabelCount, StatementLike "
        "FROM dbo.AttachmentBackfillCandidate WHERE Id = ?",
        c1.Id,
    )[0]
    assert tuple(row) == ("checked", 2, None, True, 1, True)
    for old in ("verified", "mismatch", "partial", "statement", "unverifiable"):
        with pytest.raises(pyodbc.IntegrityError):
            _evidence(conn, c2.Id, status=old)
        conn.rollback()
    cols = {r[0] for r in _rows(cur, "SELECT name FROM sys.columns WHERE object_id = OBJECT_ID('dbo.AttachmentBackfillCandidate')")}
    assert not cols & {"InvoiceFound", "AmountFound", "VendorFound"}


def test_summary_reports_distributions_without_verdict_words(conn):
    run_id, _ = _create_run(conn)
    ids = [_insert(conn, run_id, b, f"f{b}").Id for b in (101, 102, 105)]
    _mark(conn, run_id, "ready")
    _evidence(conn, ids[0], seen=0, other=1)
    _evidence(conn, ids[1], seen=3, stmt=1)
    cur = conn.cursor()
    cur.execute("EXEC dbo.ReadAttachmentBackfillRunSummary @RunId=?", run_id)
    columns = [c[0] for c in cur.description]
    rows = cur.fetchall()
    _drain(cur)
    assert len(rows) == 1
    r = dict(zip(columns, rows[0]))
    assert (r["CandidateCount"], r["Unchecked"], r["Checked"]) == (3, 1, 2)
    assert (r["InvoiceSeen0"], r["InvoiceSeen1"], r["InvoiceSeen2Plus"]) == (1, 0, 1)
    assert (r["OtherLabel0"], r["OtherLabel1Plus"], r["StatementLike"]) == (1, 1, 1)
    lowered = " ".join(columns).lower()
    for word in ("verified", "mismatch", "partial", "verdict"):
        assert word not in lowered


