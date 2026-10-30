"""U-535b b4: apply / undo procs (b4_apply.sql) + the b2 undo audit columns, against local SQL Server (synthetic rows).

Shares the Docker test DB with the b1/b2 SQL files: run ONE file at a time. The fixture drops everything it created on
teardown (the link table FKs the candidate table, which the b2 fixture drops).
"""
from __future__ import annotations

import hashlib
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
BASE_SQL = REPO_ROOT / "entities/attachment_backfill/sql/dbo.attachment_backfill.sql"
B4_SQL = REPO_ROOT / "entities/attachment_backfill/sql/b4_apply.sql"

U_17 = "17171717-1717-1717-1717-171717171717"
U_33 = "33333333-3333-3333-3333-333333333333"
SHA = hashlib.sha256(b"%PDF-1.4 synthetic").hexdigest()
OTHER_SHA = hashlib.sha256(b"other").hexdigest()

TABLES = (
    "dbo.AttachmentBackfillCreatedAttachment",
    "dbo.AttachmentBackfillCandidateLink",
    "dbo.AttachmentBackfillCandidate",
    "dbo.AttachmentBackfillRun",
    "dbo.ExpenseLineItemAttachment",
    "dbo.U535LooseRef",
    "dbo.U535MsMessageAttachment",
    "u535t.LooseRef",
    "dbo.AttachmentTextTokenHash",
    "dbo.BillLineItemAttachment",
    "dbo.Attachment",
    "dbo.BillLineItem",
    "dbo.Bill",
    "dbo.Vendor",
    "dbo.Project",
    "dbo.[User]",
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


def _drain(cur) -> None:
    while True:
        try:
            if not cur.nextset():
                break
        except pyodbc.Error:
            break


def _defined_procs() -> list[str]:
    names = set()
    for path in (BASE_SQL, B4_SQL):
        names |= set(re.findall(r"CREATE OR ALTER PROCEDURE dbo\.(\w+)", path.read_text(encoding="utf-8")))
    return sorted(names)


def _drop_all(cur) -> None:
    for proc in _defined_procs():
        cur.execute(f"IF OBJECT_ID('dbo.{proc}', 'P') IS NOT NULL DROP PROCEDURE dbo.{proc}")
    cur.execute("IF OBJECT_ID('dbo.AttachmentBackfillEvidenceGroup') IS NOT NULL DROP FUNCTION dbo.AttachmentBackfillEvidenceGroup")
    # Any leftover FK into the tables we recreate (another suite's stub) would block the drops.
    cur.execute(
        """
        DECLARE @sql NVARCHAR(MAX) = N'';
        SELECT @sql = @sql + N'ALTER TABLE ' + QUOTENAME(SCHEMA_NAME(t.schema_id)) + N'.' + QUOTENAME(t.name)
                    + N' DROP CONSTRAINT ' + QUOTENAME(fk.name) + N';'
        FROM sys.foreign_keys fk JOIN sys.tables t ON t.object_id = fk.parent_object_id
        WHERE fk.referenced_object_id IN (OBJECT_ID('dbo.Attachment'), OBJECT_ID('dbo.AttachmentBackfillCandidate'));
        EXEC sp_executesql @sql;
        """
    )
    _drain(cur)
    for tbl in TABLES:
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")
    cur.execute("IF SCHEMA_ID('u535t') IS NOT NULL EXEC('DROP SCHEMA u535t')")


def _install_base(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Vendor (Id BIGINT NOT NULL PRIMARY KEY, Name NVARCHAR(255) NULL);
        CREATE TABLE dbo.[User] (Id BIGINT NOT NULL PRIMARY KEY, PublicId UNIQUEIDENTIFIER NOT NULL);
        CREATE TABLE dbo.Project (
            Id BIGINT NOT NULL PRIMARY KEY, PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(), Name NVARCHAR(50) NOT NULL
        );
        CREATE TABLE dbo.Bill (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            VendorId BIGINT NULL,
            BillDate DATETIME2(3) NOT NULL DEFAULT '2023-01-01',
            BillNumber NVARCHAR(50) NULL,
            TotalAmount DECIMAL(18,2) NULL,
            Status NVARCHAR(32) NOT NULL DEFAULT 'completed',
            IsDraft AS (CASE WHEN Status = 'draft' THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END) PERSISTED,
            CompanyId BIGINT NOT NULL DEFAULT 1
        );
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY, BillId BIGINT NOT NULL, ProjectId BIGINT NULL
        );
        CREATE TABLE dbo.Attachment (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            FileHash NVARCHAR(64) NULL,
            BlobUrl NVARCHAR(MAX) NOT NULL,
            ExtractedTextBlobUrl NVARCHAR(MAX) NULL,
            IsArchived BIT NOT NULL DEFAULT 0,
            Status NVARCHAR(20) NULL,
            ExpirationDate DATETIME2(3) NULL,
            ExtractionStatus NVARCHAR(20) NULL,
            QboId NVARCHAR(50) NULL
        );
        -- Loose pointers: an AttachmentId column with NO foreign key, in dbo and in another schema.
        CREATE TABLE dbo.U535LooseRef (Id BIGINT IDENTITY(1,1) PRIMARY KEY, AttachmentId BIGINT NULL);
        -- Like dbo.MsMessageAttachment: a string AttachmentId holding Graph ids (not a pointer to dbo.Attachment).
        CREATE TABLE dbo.U535MsMessageAttachment (Id BIGINT IDENTITY(1,1) PRIMARY KEY, AttachmentId NVARCHAR(500) NULL);
        CREATE TABLE dbo.BillLineItemAttachment (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            BillLineItemId BIGINT NULL,
            AttachmentId BIGINT NULL,
            CreatedByUserId BIGINT NOT NULL DEFAULT 17,
            CONSTRAINT FK_T_BLIA_Attachment FOREIGN KEY (AttachmentId) REFERENCES dbo.Attachment(Id)
        );
        CREATE TABLE dbo.ExpenseLineItemAttachment (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            AttachmentId BIGINT NULL,
            CONSTRAINT FK_T_ELIA_Attachment FOREIGN KEY (AttachmentId) REFERENCES dbo.Attachment(Id)
        );
        CREATE TABLE dbo.AttachmentTextTokenHash (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            AttachmentId BIGINT NOT NULL,
            CONSTRAINT FK_T_ATTH_Attachment FOREIGN KEY (AttachmentId) REFERENCES dbo.Attachment(Id)
        );
        """
    )
    _drain(cur)
    cur.execute("EXEC('CREATE SCHEMA u535t')")
    cur.execute("CREATE TABLE u535t.LooseRef (Id BIGINT IDENTITY(1,1) PRIMARY KEY, AttachmentId BIGINT NULL)")


class Db:
    def __init__(self, cur):
        self.cur = cur
        self.run_id = self.new_run("ready")

    def new_run(self, status: str) -> int:
        rid = self.cur.execute(
            "INSERT INTO dbo.AttachmentBackfillRun (Source, Status, CreatedByUserId) OUTPUT INSERTED.Id VALUES (N'box', ?, 17)",
            status,
        ).fetchval()
        _drain(self.cur)
        return rid

    def bill(self, bill_id: int, lines: int = 3, status: str = "completed") -> list[int]:
        self.cur.execute("INSERT INTO dbo.Bill (Id, VendorId, BillNumber, TotalAmount, Status) VALUES (?, 1, ?, 10, ?)",
                         bill_id, f"INV-{bill_id}", status)
        ids = []
        for _ in range(lines):
            ids.append(self.cur.execute(
                "INSERT INTO dbo.BillLineItem (BillId, ProjectId) OUTPUT INSERTED.Id VALUES (?, NULL)", bill_id
            ).fetchval())
            _drain(self.cur)
        return ids

    def attachment(self, sha: str = SHA, blob: str = "https://blob/x.pdf") -> int:
        aid = self.cur.execute(
            "INSERT INTO dbo.Attachment (FileHash, BlobUrl) OUTPUT INSERTED.Id VALUES (?, ?)", sha, blob
        ).fetchval()
        _drain(self.cur)
        return aid

    def candidate(self, bill_id: int, review: str = "approved", run_id: int | None = None, sha: str | None = SHA) -> int:
        cid = self.cur.execute(
            """
            INSERT INTO dbo.AttachmentBackfillCandidate
                (RunId, BillId, SourceFileId, SourceFileName, SourceFileSize, SourceSha256, MatchTier, CheckStatus,
                 ReviewStatus, ReviewedByUserId, ReviewedDatetime)
            OUTPUT INSERTED.Id VALUES (?, ?, ?, ?, 1000, ?, N'A', N'checked', ?, ?, ?)
            """,
            run_id or self.run_id, bill_id, f"f{bill_id}", f"n{bill_id}.pdf", sha, review,
            17 if review != "pending" else None, "2026-10-30" if review != "pending" else None,
        ).fetchval()
        _drain(self.cur)
        return cid

    def rv(self, cid: int) -> bytes:
        return bytes(self.cur.execute("SELECT RowVersion FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", cid).fetchval())

    def row(self, sql: str, *params) -> dict:
        self.cur.execute(sql, *params)
        cols = [c[0] for c in self.cur.description]
        r = self.cur.fetchone()
        _drain(self.cur)
        return dict(zip(cols, r)) if r else {}

    def apply(self, cid: int, aid: int, created: bool = True, rv: bytes | None = None) -> dict:
        return self.row(
            "EXEC dbo.ApplyAttachmentBackfillCandidateLink @CandidateId=?, @RowVersion=?, @AttachmentId=?, @AttachmentCreated=?",
            cid, rv if rv is not None else self.rv(cid), aid, 1 if created else 0,
        )

    def undo_link(self, cid: int) -> dict:
        return self.row("EXEC dbo.UndoAttachmentBackfillCandidateLink @CandidateId=?", cid)

    def register(self, aid: int, sha: str = SHA, cid: int | None = None, run_id: int | None = None) -> dict:
        return self.row(
            "EXEC dbo.RegisterAttachmentBackfillCreatedAttachment @RunId=?, @CandidateId=?, @AttachmentId=?, @FileHash=?, @BlobUrl=?",
            run_id or self.run_id, cid, aid, sha, "https://blob/x.pdf",
        )

    def delete_att(self, aid: int, sha: str = SHA) -> dict:
        return self.row("EXEC dbo.DeleteAttachmentBackfillCreatedAttachment @AttachmentId=?, @FileHash=?", aid, sha)

    def count(self, sql: str, *params) -> int:
        return int(self.cur.execute(sql, *params).fetchval())

    def cand(self, cid: int) -> dict:
        return self.row("SELECT * FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", cid)

    def blia_for_bill(self, bill_id: int) -> int:
        return self.count(
            "SELECT COUNT(*) FROM dbo.BillLineItemAttachment b JOIN dbo.BillLineItem li ON li.Id = b.BillLineItemId WHERE li.BillId = ?",
            bill_id,
        )


@pytest.fixture()
def db():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    setup = pyodbc.connect(conn_str, autocommit=True)
    cur = setup.cursor()
    name = cur.execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    _drop_all(cur)
    _install_base(cur)
    for path in (BASE_SQL, B4_SQL, BASE_SQL, B4_SQL):  # applied twice: both files are idempotent
        for batch in _batches(path.read_text(encoding="utf-8")):
            cur.execute(batch)
            _drain(cur)
    cur.execute(f"INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'Acme'); INSERT INTO dbo.[User] (Id, PublicId) VALUES (17, '{U_17}'), (33, '{U_33}');")
    _drain(cur)
    try:
        yield Db(cur)
    finally:
        _drop_all(cur)
        setup.close()


# -- apply ------------------------------------------------------------------------------------------ #


def test_apply_links_every_line_of_a_completed_bill(db):
    lines = db.bill(1, lines=3, status="completed")
    aid = db.attachment()
    cid = db.candidate(1)
    before = db.rv(cid)
    out = db.apply(cid, aid, created=True)
    assert out["RefusedReason"] is None and out["LinkCount"] == 3 and out["AttachmentId"] == aid
    rows = db.cur.execute(
        "SELECT BillLineItemId, AttachmentId, CreatedByUserId FROM dbo.BillLineItemAttachment ORDER BY BillLineItemId"
    ).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [(li, aid, 17) for li in lines]
    assert db.count("SELECT COUNT(*) FROM dbo.AttachmentBackfillCandidateLink WHERE CandidateId = ?", cid) == 3
    c = db.cand(cid)
    assert c["AttachmentId"] == aid and c["LinkedDatetime"] is not None and c["AttachmentCreatedByApply"] is True
    assert c["ReviewStatus"] == "approved" and bytes(c["RowVersion"]) != before
    assert db.row("SELECT Status FROM dbo.Bill WHERE Id = 1")["Status"] == "completed"


def test_apply_records_reused_attachment(db):
    db.bill(1)
    out = db.apply(db.candidate(1), db.attachment(), created=False)
    assert out["RefusedReason"] is None and out["AttachmentCreatedByApply"] is False


def _assert_untouched(db, cid, bill_id):
    c = db.cand(cid)
    assert c["AttachmentId"] is None and c["LinkedDatetime"] is None
    assert db.count("SELECT COUNT(*) FROM dbo.AttachmentBackfillCandidateLink WHERE CandidateId = ?", cid) == 0


def test_refusal_not_found(db):
    out = db.apply(999999, db.attachment(), rv=b"\x00" * 8)
    assert out["RefusedReason"] == "not_found" and out["LinkCount"] == 0


@pytest.mark.parametrize("status", ["generating", "applied", "cancelled"])
def test_refusal_run_not_ready(db, status):
    db.bill(1)
    run = db.new_run(status)
    cid = db.candidate(1, run_id=run)
    assert db.apply(cid, db.attachment())["RefusedReason"] == "run_not_ready"
    _assert_untouched(db, cid, 1)
    assert db.blia_for_bill(1) == 0


def test_refusal_linked_on_second_apply(db):
    db.bill(1)
    aid = db.attachment()
    cid = db.candidate(1)
    assert db.apply(cid, aid)["RefusedReason"] is None
    assert db.apply(cid, aid)["RefusedReason"] == "linked"
    assert db.blia_for_bill(1) == 3


@pytest.mark.parametrize("review", ["pending", "rejected"])
def test_refusal_not_approved(db, review):
    db.bill(1)
    cid = db.candidate(1, review=review)
    assert db.apply(cid, db.attachment())["RefusedReason"] == "not_approved"
    _assert_untouched(db, cid, 1)


def test_refusal_stale(db):
    db.bill(1)
    cid = db.candidate(1)
    assert db.apply(cid, db.attachment(), rv=b"\x00" * 8)["RefusedReason"] == "stale"
    _assert_untouched(db, cid, 1)


def test_refusal_bill_missing(db):
    cid = db.candidate(42)
    assert db.apply(cid, db.attachment())["RefusedReason"] == "bill_missing"


def test_refusal_no_line_items(db):
    db.bill(1, lines=0)
    cid = db.candidate(1)
    assert db.apply(cid, db.attachment())["RefusedReason"] == "no_line_items"


def test_refusal_bill_gained_a_pdf_since_generation(db):
    lines = db.bill(1)
    other = db.attachment(OTHER_SHA)
    db.cur.execute("INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (?, ?)", lines[1], other)
    cid = db.candidate(1)
    assert db.apply(cid, db.attachment())["RefusedReason"] == "bill_has_attachment"
    _assert_untouched(db, cid, 1)
    assert db.blia_for_bill(1) == 1


def test_refusal_attachment_missing(db):
    db.bill(1)
    cid = db.candidate(1)
    assert db.apply(cid, 987654)["RefusedReason"] == "attachment_missing"


def test_refusal_attachment_hash_mismatch(db):
    db.bill(1)
    cid = db.candidate(1)
    assert db.apply(cid, db.attachment(OTHER_SHA))["RefusedReason"] == "attachment_hash_mismatch"
    assert db.blia_for_bill(1) == 0


def test_hash_comparison_ignores_case(db):
    db.bill(1)
    assert db.apply(db.candidate(1), db.attachment(SHA.upper()))["RefusedReason"] is None


def test_refusal_order_run_before_review_before_bill(db):
    run = db.new_run("applied")
    cid = db.candidate(77, review="pending", run_id=run)  # also bill_missing / not_approved / stale
    assert db.apply(cid, 987654, rv=b"\x00" * 8)["RefusedReason"] == "run_not_ready"


# -- race vs the b2 review undo ---------------------------------------------------------------------- #


def test_b2_undo_after_apply_is_refused_linked(db):
    db.bill(1)
    cid = db.candidate(1)
    assert db.apply(cid, db.attachment())["RefusedReason"] is None
    public_id = db.cand(cid)["PublicId"]
    out = db.row("EXEC dbo.UndoAttachmentBackfillCandidateReview @PublicId=?, @RowVersion=?, @UndoneByUserId=33",
                 public_id, db.rv(cid))
    assert out["RefusedReason"] == "linked"
    assert db.cand(cid)["ReviewStatus"] == "approved" and db.cand(cid)["UndoneByUserId"] is None


def test_apply_after_b2_undo_loses_cleanly(db):
    db.bill(1)
    cid = db.candidate(1)
    seen = db.rv(cid)
    public_id = db.cand(cid)["PublicId"]
    out = db.row("EXEC dbo.UndoAttachmentBackfillCandidateReview @PublicId=?, @RowVersion=?, @UndoneByUserId=33",
                 public_id, seen)
    assert out["RefusedReason"] is None and out["ReviewStatus"] == "pending"
    assert db.apply(cid, db.attachment(), rv=seen)["RefusedReason"] == "not_approved"
    assert db.blia_for_bill(1) == 0


# -- undo audit (b2 follow-up) ----------------------------------------------------------------------- #


def test_review_undo_records_who_and_when(db):
    db.bill(1)
    cid = db.candidate(1, review="rejected")
    db.cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET RejectReason = N'other' WHERE Id = ?", cid)
    public_id = db.cand(cid)["PublicId"]
    out = db.row("EXEC dbo.UndoAttachmentBackfillCandidateReview @PublicId=?, @RowVersion=?, @UndoneByUserId=33",
                 public_id, db.rv(cid))
    assert out["RefusedReason"] is None
    c = db.cand(cid)
    assert c["UndoneByUserId"] == 33 and c["UndoneDatetime"] is not None
    assert c["ReviewedByUserId"] is None and c["ReviewStatus"] == "pending"


def test_review_undo_without_actor_still_works(db):
    db.bill(1)
    cid = db.candidate(1)
    out = db.row("EXEC dbo.UndoAttachmentBackfillCandidateReview @PublicId=?, @RowVersion=?",
                 db.cand(cid)["PublicId"], db.rv(cid))
    assert out["RefusedReason"] is None
    c = db.cand(cid)
    assert c["UndoneByUserId"] is None and c["UndoneDatetime"] is not None


# -- undo by run ---------------------------------------------------------------------------------- #


def test_undo_removes_only_apply_created_links(db):
    lines1 = db.bill(1)
    lines2 = db.bill(2, lines=1)
    aid = db.attachment()
    # Pre-existing link of the same attachment on ANOTHER bill (the reuse case).
    db.cur.execute("INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (?, ?)", lines2[0], aid)
    cid = db.candidate(1)
    assert db.apply(cid, aid, created=False)["LinkCount"] == 3
    # A link added by hand after the apply, on the same bill: not recorded, so it must survive too.
    db.cur.execute("INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (?, ?)", lines1[0], aid)
    out = db.undo_link(cid)
    assert out["RefusedReason"] is None and out["LinksDeleted"] == 3 and out["AttachmentCreatedByApply"] is False
    assert db.blia_for_bill(2) == 1 and db.blia_for_bill(1) == 1
    c = db.cand(cid)
    assert c["AttachmentId"] is None and c["LinkedDatetime"] is None and c["AttachmentCreatedByApply"] is None
    assert c["ReviewStatus"] == "approved"
    assert db.count("SELECT COUNT(*) FROM dbo.AttachmentBackfillCandidateLink") == 0
    assert db.count("SELECT COUNT(*) FROM dbo.Attachment WHERE Id = ?", aid) == 1


def test_undo_not_linked_and_not_found(db):
    db.bill(1)
    cid = db.candidate(1)
    assert db.undo_link(cid)["RefusedReason"] == "not_linked"
    assert db.undo_link(999999)["RefusedReason"] == "not_found"


def test_undo_then_reapply_in_a_ready_run(db):
    db.bill(1)
    cid = db.candidate(1)
    aid = db.attachment()
    db.apply(cid, aid)
    db.undo_link(cid)
    assert db.apply(cid, aid)["LinkCount"] == 3


# -- created-attachment delete --------------------------------------------------------------------- #


def test_delete_created_attachment_when_unreferenced(db):
    db.bill(1)
    cid = db.candidate(1)
    aid = db.attachment(blob="https://blob/a.pdf")
    db.cur.execute("UPDATE dbo.Attachment SET ExtractedTextBlobUrl = N'https://blob/a.json' WHERE Id = ?", aid)
    db.cur.execute("INSERT INTO dbo.AttachmentTextTokenHash (AttachmentId) VALUES (?), (?)", aid, aid)
    db.register(aid, cid=cid)
    db.apply(cid, aid, created=True)
    assert db.delete_att(aid)["RefusedReason"] == "referenced"  # still linked + candidate points at it
    assert db.undo_link(cid)["AttachmentCreatedByApply"] is True
    out = db.delete_att(aid)
    assert out["Deleted"] is True and out["BlobUrl"] == "https://blob/a.pdf"
    assert out["ExtractedTextBlobUrl"] == "https://blob/a.json"
    assert db.count("SELECT COUNT(*) FROM dbo.Attachment WHERE Id = ?", aid) == 0
    assert db.count("SELECT COUNT(*) FROM dbo.AttachmentTextTokenHash WHERE AttachmentId = ?", aid) == 0
    assert db.count("SELECT COUNT(*) FROM dbo.AttachmentBackfillCreatedAttachment WHERE AttachmentId = ? AND DeletedDatetime IS NOT NULL", aid) == 1


def test_delete_created_attachment_kept_when_referenced_elsewhere(db):
    aid = db.attachment()
    db.register(aid)
    db.cur.execute("INSERT INTO dbo.ExpenseLineItemAttachment (AttachmentId) VALUES (?)", aid)
    out = db.delete_att(aid)
    assert out["Deleted"] is False and out["RefusedReason"] == "referenced" and out["BlobUrl"] is None
    assert db.count("SELECT COUNT(*) FROM dbo.Attachment WHERE Id = ?", aid) == 1


def test_delete_created_attachment_kept_when_another_bill_links_it(db):
    lines = db.bill(5, lines=1)
    aid = db.attachment()
    db.register(aid)
    db.cur.execute("INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (?, ?)", lines[0], aid)
    assert db.delete_att(aid)["RefusedReason"] == "referenced"


def test_delete_created_attachment_requires_matching_hash(db):
    aid = db.attachment()
    db.register(aid)
    assert db.delete_att(aid, OTHER_SHA)["RefusedReason"] == "not_found"
    assert db.delete_att(424242)["RefusedReason"] == "not_registered"
    assert db.count("SELECT COUNT(*) FROM dbo.Attachment WHERE Id = ?", aid) == 1


# -- reads, closure, size ----------------------------------------------------------------------------- #


def test_read_for_apply_reports_db_facts(db):
    lines = db.bill(1)
    db.bill(2, lines=0)
    db.cur.execute("INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (?, ?)", lines[0],
                   db.attachment(OTHER_SHA))
    c1, c2, c3 = db.candidate(1), db.candidate(2), db.candidate(3)
    db.candidate(4, review="pending")
    db.bill(4)
    db.cur.execute("EXEC dbo.ReadAttachmentBackfillCandidatesForApply @RunId=?, @AfterId=0, @Top=10", db.run_id)
    cols = [c[0] for c in db.cur.description]
    rows = {r[0]: dict(zip(cols, r)) for r in db.cur.fetchall()}
    _drain(db.cur)
    assert set(rows) == {c1, c2, c3}
    assert (rows[c1]["BillExists"], rows[c1]["LineItemCount"], rows[c1]["BillAttachmentCount"]) == (True, 3, 1)
    assert (rows[c2]["BillExists"], rows[c2]["LineItemCount"]) == (True, 0)
    assert rows[c3]["BillExists"] is False
    db.cur.execute("EXEC dbo.ReadAttachmentBackfillCandidatesForApply @RunId=?, @AfterId=?, @Top=10", db.run_id, c1)
    assert {r[0] for r in db.cur.fetchall()} == {c2, c3}
    _drain(db.cur)


def test_read_linked_candidates(db):
    db.bill(1)
    cid = db.candidate(1)
    db.apply(cid, db.attachment())
    out = db.row("EXEC dbo.ReadAttachmentBackfillLinkedCandidates @RunId=?", db.run_id)
    assert out["Id"] == cid and out["RecordedLinkCount"] == 3 and out["AttachmentCreatedByApply"] is True
    assert out["SourceSha256"] == SHA


def test_close_run_only_when_nothing_left(db):
    db.bill(1)
    db.bill(2)
    c1 = db.candidate(1)
    c2 = db.candidate(2, review="pending")
    db.apply(c1, db.attachment())
    assert db.row("EXEC dbo.CloseAttachmentBackfillRunIfDone @RunId=?", db.run_id) == {}
    db.cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET ReviewStatus = N'rejected', RejectReason = N'other' WHERE Id = ?", c2)
    out = db.row("EXEC dbo.CloseAttachmentBackfillRunIfDone @RunId=?", db.run_id)
    assert out["Status"] == "applied"
    assert db.row("EXEC dbo.CloseAttachmentBackfillRunIfDone @RunId=?", db.run_id) == {}


def test_close_run_refused_while_an_approved_candidate_is_unlinked(db):
    db.bill(1)
    db.candidate(1)
    assert db.row("EXEC dbo.CloseAttachmentBackfillRunIfDone @RunId=?", db.run_id) == {}


def test_database_size_is_readable(db):
    out = db.row("EXEC dbo.ReadAttachmentBackfillDatabaseSize")
    assert int(out["UsedBytes"]) > 0
    assert out["MaxSizeBytes"] is None or int(out["MaxSizeBytes"]) > 0


# -- fix round 1 ------------------------------------------------------------------------------------ #


def _link_draft_then_complete(db, bill_id=1):
    db.bill(bill_id, status="draft")
    cid = db.candidate(bill_id)
    aid = db.attachment()
    assert db.apply(cid, aid)["RefusedReason"] is None
    return cid, aid


def test_r1_undo_refused_when_bill_completed_since_apply(db):
    cid, aid = _link_draft_then_complete(db)
    assert db.cand(cid)["LinkedBillStatus"] == "draft"
    db.cur.execute("UPDATE dbo.Bill SET Status = N'completed' WHERE Id = 1")
    out = db.undo_link(cid)
    assert out["RefusedReason"] == "bill_completed_since_apply" and out["LinksDeleted"] == 0
    assert db.blia_for_bill(1) == 3 and db.cand(cid)["AttachmentId"] == aid


def test_r1_undo_allowed_when_bill_was_already_completed_at_link(db):
    db.bill(1, status="completed")
    cid = db.candidate(1)
    db.apply(cid, db.attachment())
    assert db.cand(cid)["LinkedBillStatus"] == "completed"
    assert db.undo_link(cid)["LinksDeleted"] == 3


def test_r1_undo_refused_when_attachment_pushed_to_qbo(db):
    db.bill(1)
    cid = db.candidate(1)
    aid = db.attachment()
    db.apply(cid, aid)
    db.cur.execute("UPDATE dbo.Attachment SET QboId = N'5001' WHERE Id = ?", aid)
    assert db.undo_link(cid)["RefusedReason"] == "attachment_pushed"
    assert db.blia_for_bill(1) == 3


def test_r1_r2_delete_never_removes_a_qbo_pushed_attachment(db):
    aid = db.attachment()
    db.register(aid)
    db.cur.execute("UPDATE dbo.Attachment SET QboId = N'5001' WHERE Id = ?", aid)
    out = db.delete_att(aid)
    assert out["Deleted"] is False and out["RefusedReason"] == "attachment_pushed"
    assert db.count("SELECT COUNT(*) FROM dbo.Attachment WHERE Id = ?", aid) == 1


@pytest.mark.parametrize("table", ["dbo.U535LooseRef", "u535t.LooseRef"])
def test_r2_delete_counts_loose_attachmentid_pointers(db, table):
    aid = db.attachment()
    db.register(aid)
    db.cur.execute(f"INSERT INTO {table} (AttachmentId) VALUES (?)", aid)
    out = db.delete_att(aid)
    assert out["Deleted"] is False and out["RefusedReason"] == "referenced"
    assert db.count("SELECT COUNT(*) FROM dbo.Attachment WHERE Id = ?", aid) == 1


def test_r2_r3_delete_refuses_an_unregistered_attachment(db):
    aid = db.attachment()
    out = db.delete_att(aid)
    assert out["Deleted"] is False and out["RefusedReason"] == "not_registered"
    assert db.count("SELECT COUNT(*) FROM dbo.Attachment WHERE Id = ?", aid) == 1


def test_r2_delete_proc_uses_string_agg_and_doomed_txn_handling():
    text = B4_SQL.read_text(encoding="utf-8")
    body = text[text.index("CREATE OR ALTER PROCEDURE dbo.DeleteAttachmentBackfillCreatedAttachment"):]
    body = body[: body.index("\nGO")]
    assert "STRING_AGG" in body and "@Sql = @Sql +" not in body
    assert "XACT_STATE() = -1" in body and "sys.columns" in body


def test_r3_registry_sweep_lists_only_undeleted_rows_of_the_run(db):
    a1, a2 = db.attachment(), db.attachment()
    db.register(a1)
    db.register(a2)
    other_run = db.new_run("ready")
    a3 = db.attachment()
    db.register(a3, run_id=other_run)
    assert db.delete_att(a1)["Deleted"] is True
    db.cur.execute("EXEC dbo.ReadAttachmentBackfillCreatedAttachmentsToSweep @RunId=?", db.run_id)
    rows = [(r[0], r[1]) for r in db.cur.fetchall()]
    _drain(db.cur)
    assert rows == [(a2, SHA)]


def test_r3_delete_of_an_already_gone_attachment_closes_its_registry_row(db):
    aid = db.attachment()
    db.register(aid)
    db.cur.execute("DELETE FROM dbo.Attachment WHERE Id = ?", aid)
    assert db.delete_att(aid)["RefusedReason"] == "already_gone"
    assert db.count("SELECT COUNT(*) FROM dbo.AttachmentBackfillCreatedAttachment WHERE AttachmentId = ? AND DeletedDatetime IS NOT NULL", aid) == 1


def test_r3_shared_bytes_are_swept_after_both_candidates_unlink(db):
    db.bill(1)
    db.bill(2)
    c1, c2 = db.candidate(1), db.candidate(2)
    aid = db.attachment()
    db.register(aid, cid=c1)
    db.apply(c1, aid, created=True)
    db.apply(c2, aid, created=False)  # reuses the same bytes
    db.undo_link(c1)
    assert db.delete_att(aid)["RefusedReason"] == "referenced"  # c2 still links it
    db.undo_link(c2)
    assert db.delete_att(aid)["Deleted"] is True


def test_r5_read_attachments_by_hash_returns_all_rows_with_reuse_facts(db):
    a1 = db.attachment()
    a2 = db.attachment()
    db.cur.execute("UPDATE dbo.Attachment SET IsArchived = 1 WHERE Id = ?", a1)
    db.attachment(OTHER_SHA)
    db.cur.execute("EXEC dbo.ReadAttachmentBackfillAttachmentsByHash @FileHash=?", SHA.upper())
    cols = [c[0] for c in db.cur.description]
    rows = [dict(zip(cols, r)) for r in db.cur.fetchall()]
    _drain(db.cur)
    assert [r["Id"] for r in rows] == [a1, a2]
    assert {"IsArchived", "Status", "ExpirationDate", "ExtractionStatus", "QboId"} <= set(cols)


def test_r7_undo_in_an_applied_run_reopens_it(db):
    db.bill(1)
    cid = db.candidate(1)
    aid = db.attachment()
    db.apply(cid, aid)
    db.cur.execute("UPDATE dbo.AttachmentBackfillRun SET Status = N'applied' WHERE Id = ?", db.run_id)
    assert db.undo_link(cid)["LinksDeleted"] == 3
    assert db.row("SELECT Status FROM dbo.AttachmentBackfillRun WHERE Id = ?", db.run_id)["Status"] == "ready"
    assert db.apply(cid, aid)["LinkCount"] == 3


def test_r7_undo_leaves_a_cancelled_run_cancelled(db):
    db.bill(1)
    cid = db.candidate(1)
    db.apply(cid, db.attachment())
    db.cur.execute("UPDATE dbo.AttachmentBackfillRun SET Status = N'cancelled' WHERE Id = ?", db.run_id)
    db.undo_link(cid)
    assert db.row("SELECT Status FROM dbo.AttachmentBackfillRun WHERE Id = ?", db.run_id)["Status"] == "cancelled"


def _proc_body(path, name):
    text = path.read_text(encoding="utf-8")
    body = text[text.index(f"CREATE OR ALTER PROCEDURE dbo.{name}"):]
    return body[: body.index("\nGO")]


@pytest.mark.parametrize("name", ["UpdateAttachmentBackfillCandidateReview", "UndoAttachmentBackfillCandidateReview"])
def test_r8_review_procs_read_the_run_with_a_locking_read(name):
    body = _proc_body(BASE_SQL, name)
    update = body[body.index("UPDATE c"):]
    assert re.search(r"INNER JOIN dbo\.\[AttachmentBackfillRun\] AS r WITH \(UPDLOCK\)", update), name


def test_r9_close_locks_the_run_before_the_candidates():
    body = _proc_body(B4_SQL, "CloseAttachmentBackfillRunIfDone")
    assert "BEGIN TRANSACTION" in body and "COMMIT TRANSACTION" in body
    run_lock = body.index("FROM dbo.[AttachmentBackfillRun] WITH (UPDLOCK, HOLDLOCK)")
    assert run_lock < body.index("dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)")


# -- fix round 2 ------------------------------------------------------------------------------------ #


def test_s1_string_attachmentid_column_does_not_break_the_delete(db):
    db.cur.execute("INSERT INTO dbo.U535MsMessageAttachment (AttachmentId) VALUES (N'AAMkX'), (N'AAMkY==')")
    aid = db.attachment()
    db.register(aid)
    out = db.delete_att(aid)
    assert out["Deleted"] is True and out["RefusedReason"] is None


def test_s1_bigint_loose_pointer_still_refuses_alongside_a_string_column(db):
    db.cur.execute("INSERT INTO dbo.U535MsMessageAttachment (AttachmentId) VALUES (N'AAMkX')")
    aid = db.attachment()
    db.register(aid)
    db.cur.execute("INSERT INTO dbo.U535LooseRef (AttachmentId) VALUES (?)", aid)
    assert db.delete_att(aid)["RefusedReason"] == "referenced"


def test_s4_undo_link_header_lists_every_refusal():
    text = B4_SQL.read_text(encoding="utf-8")
    header = text[: text.index("CREATE OR ALTER PROCEDURE dbo.UndoAttachmentBackfillCandidateLink")]
    comment = header[header.rindex("\nGO") :]
    for code in ("not_found", "not_linked", "attachment_pushed", "bill_completed_since_apply"):
        assert code in comment, code
