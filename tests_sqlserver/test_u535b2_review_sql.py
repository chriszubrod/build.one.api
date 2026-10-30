"""U-535b b2: review procs of dbo.attachment_backfill.sql against local SQL Server (synthetic rows only)."""
from __future__ import annotations

import os
import re
import uuid
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
SQL_FILE = REPO_ROOT / "entities/attachment_backfill/sql/dbo.attachment_backfill.sql"

PROCS = (
    "CreateAttachmentBackfillRun",
    "DeleteStaleAttachmentBackfillCandidates",
    "InsertAttachmentBackfillCandidate",
    "ReadAttachmentBackfillCandidateDetail",
    "ReadAttachmentBackfillCandidatesForVerification",
    "ReadAttachmentBackfillQueue",
    "ReadAttachmentBackfillRunByPublicId",
    "ReadAttachmentBackfillRunFacets",
    "ReadAttachmentBackfillRunsForReview",
    "ReadAttachmentBackfillRunSummary",
    "ReadBillsForAttachmentBackfill",
    "ReadOpenAttachmentBackfillRuns",
    "UndoAttachmentBackfillCandidateReview",
    "UpdateAttachmentBackfillCandidateReview",
    "UpdateAttachmentBackfillCandidateVerification",
    "UpdateAttachmentBackfillRunStatus",
)
FUNCTIONS = ("AttachmentBackfillEvidenceGroup",)
TABLES = (
    "dbo.AttachmentBackfillCandidate",
    "dbo.AttachmentBackfillRun",
    "dbo.BillLineItemAttachment",
    "dbo.BillLineItem",
    "dbo.Bill",
    "dbo.Vendor",
    "dbo.Project",
    "dbo.[User]",
)

P_ALPHA = "11111111-1111-1111-1111-111111111111"
P_BETA = "22222222-2222-2222-2222-222222222222"
U_17 = "17171717-1717-1717-1717-171717171717"
U_33 = "33333333-3333-3333-3333-333333333333"


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


def _drop_all(cur) -> None:
    for proc in PROCS:
        cur.execute(f"IF OBJECT_ID('dbo.{proc}', 'P') IS NOT NULL DROP PROCEDURE dbo.{proc}")
    for fn in FUNCTIONS:
        cur.execute(f"IF OBJECT_ID('dbo.{fn}') IS NOT NULL DROP FUNCTION dbo.{fn}")
    for tbl in TABLES:
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_base(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Vendor (Id BIGINT NOT NULL PRIMARY KEY, Name NVARCHAR(255) NULL);
        CREATE TABLE dbo.[User] (Id BIGINT NOT NULL PRIMARY KEY, PublicId UNIQUEIDENTIFIER NOT NULL);
        CREATE TABLE dbo.Project (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            Name NVARCHAR(50) NOT NULL
        );
        CREATE TABLE dbo.Bill (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            VendorId BIGINT NULL,
            BillDate DATETIME2(3) NOT NULL,
            BillNumber NVARCHAR(50) NULL,
            TotalAmount DECIMAL(18,2) NULL,
            Status NVARCHAR(32) NOT NULL DEFAULT 'completed',
            IsDraft AS (CASE WHEN Status = 'draft' THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END) PERSISTED,
            CompanyId BIGINT NOT NULL DEFAULT 1
        );
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BillId BIGINT NOT NULL,
            ProjectId BIGINT NULL
        );
        CREATE TABLE dbo.BillLineItemAttachment (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BillLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        """
    )
    _drain(cur)


# (bill id, project ids, bill date, (InvoiceSeen, AmountSeen, OtherLabel, StatementLike, CheckStatus), expected group)
CASES = [
    (1, (1,), "2022-03-01", (1, 1, 0, 0, "checked"), "clean"),
    (2, (1,), "2022-04-01", (2, 3, 0, 0, "checked"), "clean"),
    (3, (1,), "2023-01-05", (1, 1, 1, 0, "checked"), "flagged"),
    (4, (2,), "2023-02-01", (0, 0, 0, 1, "checked"), "flagged"),
    (5, (2,), "2023-02-02", (1, 0, 0, 0, "checked"), "invoice_only"),
    (6, (2,), "2023-02-03", (1, None, 0, 0, "checked"), "invoice_only"),
    (7, (1, 2), "2023-02-04", (0, 1, 0, 0, "checked"), "amount_only"),
    (8, (), "2023-02-05", (None, 2, 0, 0, "checked"), "amount_only"),
    (9, (), "2024-01-01", (0, 0, 0, 0, "checked"), "neither"),
    (10, (2,), "2024-01-02", (None, None, None, None, "unreadable"), "neither"),
    (11, (2,), "2024-01-03", (None, None, None, None, "error"), "neither"),
    (12, (1,), "2024-01-04", (None, None, None, None, None), "neither"),
    (13, (1,), "2024-01-05", (1, 1, None, None, "checked"), "neither"),  # NULL label/statement: not clean
]


@pytest.fixture()
def db():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    setup = pyodbc.connect(conn_str, autocommit=True)
    setup.autocommit = True
    cur = setup.cursor()
    name = cur.execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    _drop_all(cur)
    _install_base(cur)
    for batch in _batches(SQL_FILE.read_text(encoding="utf-8")):
        cur.execute(batch)
    cur.execute(
        f"""
        INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'Acme Lumber');
        INSERT INTO dbo.[User] (Id, PublicId) VALUES (17, '{U_17}'), (33, '{U_33}');
        INSERT INTO dbo.Project (Id, PublicId, Name) VALUES (1, '{P_ALPHA}', N'Alpha'), (2, '{P_BETA}', N'Beta');
        """
    )
    _drain(cur)
    run_id = cur.execute(
        "INSERT INTO dbo.AttachmentBackfillRun (Source, Status, CreatedByUserId) OUTPUT INSERTED.Id VALUES (N'box', N'ready', 17)"
    ).fetchval()
    _drain(cur)
    ids = {}
    for bill_id, projects, bill_date, (inv, amt, other, stmt, check), _group in CASES:
        cur.execute(
            "INSERT INTO dbo.Bill (Id, VendorId, BillDate, BillNumber, TotalAmount) VALUES (?, 1, ?, ?, ?)",
            bill_id, bill_date, f"INV-{bill_id}", 100 + bill_id,
        )
        for project_id in projects:
            cur.execute("INSERT INTO dbo.BillLineItem (BillId, ProjectId) VALUES (?, ?)", bill_id, project_id)
            cur.execute("INSERT INTO dbo.BillLineItem (BillId, ProjectId) VALUES (?, ?)", bill_id, project_id)
        cand_id = cur.execute(
            """
            INSERT INTO dbo.AttachmentBackfillCandidate
                (RunId, BillId, SourceFileId, SourceFileName, SourceFileSize, MatchTier, CheckStatus,
                 InvoiceSeenCount, AmountSeenCount, OtherInvoiceLabelCount, StatementLike, VendorSeen, UsedOcr)
            OUTPUT INSERTED.Id
            VALUES (?, ?, ?, ?, 1000, N'A', ?, ?, ?, ?, ?, 1, 0)
            """,
            run_id, bill_id, f"f{bill_id}", f"name-{bill_id}.pdf", check, inv, amt, other, stmt,
        ).fetchval()
        _drain(cur)
        ids[bill_id] = cand_id
    run_public_id = str(cur.execute("SELECT PublicId FROM dbo.AttachmentBackfillRun WHERE Id = ?", run_id).fetchval())
    app = pyodbc.connect(conn_str, autocommit=False)
    app.autocommit = False
    yield {"setup": cur, "app": app, "run_id": run_id, "run_public_id": run_public_id, "cand_ids": ids}
    app.rollback()
    app.close()
    _drop_all(cur)
    setup.close()


def _call(app, sql: str, *params) -> list:
    cur = app.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall() if cur.description else []
    _drain(cur)
    app.commit()
    return rows


def _queue(app, run_public_id, *, group=None, project=None, no_project=0, year=None, status=None, after=None, top=50):
    return _call(
        app,
        "EXEC dbo.ReadAttachmentBackfillQueue @RunPublicId=?, @EvidenceGroup=?, @ProjectPublicId=?, @NoProject=?, "
        "@BillYear=?, @ReviewStatus=?, @AfterPublicId=?, @Top=?",
        run_public_id, group, project, no_project, year, status, after, top,
    )


def _facets(app, run_public_id):
    return _call(app, "EXEC dbo.ReadAttachmentBackfillRunFacets @RunPublicId=?", run_public_id)


def _cand(db, bill_id):
    cur = db["setup"]
    row = cur.execute(
        "SELECT PublicId, RowVersion FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", db["cand_ids"][bill_id]
    ).fetchone()
    _drain(cur)
    return str(row.PublicId), bytes(row.RowVersion)


def _review(app, public_id, decision, reason, row_version, user_id=17):
    return _call(
        app,
        "EXEC dbo.UpdateAttachmentBackfillCandidateReview @PublicId=?, @Decision=?, @RejectReason=?, @RowVersion=?, "
        "@ReviewedByUserId=?",
        public_id, decision, reason, row_version, user_id,
    )[0]


def _undo(app, public_id, row_version):
    return _call(app, "EXEC dbo.UndoAttachmentBackfillCandidateReview @PublicId=?, @RowVersion=?", public_id, row_version)[0]


# -- groups / facets / paging ---------------------------------------------------------------------------- #


def test_evidence_group_semantics_for_all_five_groups_including_nulls(db):
    rows = _queue(db["app"], db["run_public_id"], top=100)
    got = {r.BillNumber: r.EvidenceGroup for r in rows}
    assert got == {f"INV-{bill_id}": group for bill_id, *_rest, group in CASES}


@pytest.mark.parametrize("group", ["clean", "flagged", "invoice_only", "amount_only", "neither"])
def test_group_filter_returns_exactly_that_group(db, group):
    rows = _queue(db["app"], db["run_public_id"], group=group, top=100)
    expected = sorted(f"INV-{b}" for b, *_r, g in CASES if g == group)
    assert sorted(r.BillNumber for r in rows) == expected
    assert all(r.EvidenceGroup == group for r in rows)


def test_facet_totals_equal_queue_totals(db):
    app, run = db["app"], db["run_public_id"]
    facets = _facets(app, run)
    assert sum(f.CandidateCount for f in facets) == len(CASES)
    for f in facets:
        no_project = 1 if f.ProjectPublicId is None else 0
        rows = _queue(
            app, run, group=f.EvidenceGroup, project=None if no_project else str(f.ProjectPublicId),
            no_project=no_project, year=f.BillYear, status=f.ReviewStatus, top=100,
        )
        assert len(rows) == f.CandidateCount, (f.EvidenceGroup, f.ProjectName, f.BillYear)


def test_primary_project_is_lowest_name_and_project_names_lists_all(db):
    rows = {r.BillNumber: r for r in _queue(db["app"], db["run_public_id"], top=100)}
    assert rows["INV-7"].ProjectName == "Alpha" and rows["INV-7"].ProjectNames == "Alpha, Beta"
    assert rows["INV-8"].ProjectName is None and rows["INV-8"].ProjectPublicId is None


def test_queue_order_project_then_date_then_no_project_last(db):
    rows = _queue(db["app"], db["run_public_id"], top=100)
    names = [r.ProjectName for r in rows]
    assert names == sorted(names, key=lambda n: (n is None, n or ""))
    alpha_dates = [r.BillDate for r in rows if r.ProjectName == "Alpha"]
    assert alpha_dates == sorted(alpha_dates)


@pytest.mark.parametrize("page_size", [1, 2, 3, 5])
def test_keyset_paging_has_no_duplicates_and_no_gaps(db, page_size):
    app, run = db["app"], db["run_public_id"]
    full = [str(r.PublicId) for r in _queue(app, run, top=100)]
    seen, after = [], None
    for _ in range(100):
        page = _queue(app, run, after=after, top=page_size)
        if not page:
            break
        seen.extend(str(r.PublicId) for r in page)
        after = str(page[-1].PublicId)
    assert seen == full and len(set(seen)) == len(CASES)


def test_cursor_stays_valid_when_the_cursor_row_leaves_the_filter(db):
    app, run = db["app"], db["run_public_id"]
    first = _queue(app, run, status="pending", top=3)
    pid, rv = str(first[-1].PublicId), bytes(first[-1].RowVersion)
    assert _review(app, pid, "approved", None, rv).RefusedReason is None
    rest = _queue(app, run, status="pending", after=pid, top=100)
    full = [str(r.PublicId) for r in _queue(app, run, status="pending", top=100)]
    assert [str(r.PublicId) for r in first[:2]] + [str(r.PublicId) for r in rest] == full


def test_filters_year_project_and_no_project(db):
    app, run = db["app"], db["run_public_id"]
    assert {r.BillNumber for r in _queue(app, run, year=2022)} == {"INV-1", "INV-2"}
    assert {r.BillNumber for r in _queue(app, run, project=P_BETA, top=100)} == {"INV-4", "INV-5", "INV-6", "INV-10", "INV-11"}
    assert {r.BillNumber for r in _queue(app, run, no_project=1)} == {"INV-8", "INV-9"}


def test_unknown_run_returns_nothing(db):
    assert _queue(db["app"], str(uuid.uuid4())) == []
    assert _facets(db["app"], str(uuid.uuid4())) == []


def test_runs_for_review_lists_open_run_with_counts(db):
    rows = _call(db["app"], "EXEC dbo.ReadAttachmentBackfillRunsForReview")
    row = next(r for r in rows if str(r.PublicId) == db["run_public_id"])
    assert row.CandidateCount == len(CASES) and row.Pending == len(CASES) and row.Unchecked == 1


def test_detail_returns_source_file_id_and_queue_fields(db):
    pid, _rv = _cand(db, 5)
    row = _call(db["app"], "EXEC dbo.ReadAttachmentBackfillCandidateDetail @PublicId=?", pid)[0]
    assert row.SourceFileId == "f5" and row.EvidenceGroup == "invoice_only" and row.ProjectName == "Beta"
    assert _call(db["app"], "EXEC dbo.ReadAttachmentBackfillCandidateDetail @PublicId=?", str(uuid.uuid4())) == []


# -- review / undo ---------------------------------------------------------------------------------------- #


def test_review_approve_happy_path(db):
    pid, rv = _cand(db, 1)
    row = _review(db["app"], pid, "approved", None, rv, user_id=33)
    assert row.RefusedReason is None and row.ReviewStatus == "approved"
    assert str(row.ReviewedByUserPublicId).lower() == U_33 and not hasattr(row, "ReviewedByUserId")
    assert row.RejectReason is None and row.ReviewedDatetime is not None and bytes(row.RowVersion) != rv
    stored = db["setup"].execute(
        "SELECT ReviewStatus, AttachmentId FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", db["cand_ids"][1]
    ).fetchone()
    assert stored.ReviewStatus == "approved" and stored.AttachmentId is None


def test_review_reject_happy_path(db):
    pid, rv = _cand(db, 3)
    row = _review(db["app"], pid, "rejected", "statement", rv)
    assert row.RefusedReason is None and row.ReviewStatus == "rejected" and row.RejectReason == "statement"


@pytest.mark.parametrize(
    "decision,reason",
    [("approved", "other"), ("rejected", None), ("rejected", "nope"), ("maybe", None)],
)
def test_review_invalid_inputs_refused(db, decision, reason):
    pid, rv = _cand(db, 1)
    assert _review(db["app"], pid, decision, reason, rv).RefusedReason == "invalid"


def test_review_without_reviewer_is_invalid(db):
    pid, rv = _cand(db, 1)
    assert _review(db["app"], pid, "approved", None, rv, user_id=None).RefusedReason == "invalid"


def test_review_not_found(db):
    assert _review(db["app"], str(uuid.uuid4()), "approved", None, b"\x00" * 8).RefusedReason == "not_found"


@pytest.mark.parametrize("status", ["generating", "applied", "cancelled"])
def test_review_run_not_ready(db, status):
    db["setup"].execute("UPDATE dbo.AttachmentBackfillRun SET Status = ? WHERE Id = ?", status, db["run_id"])
    pid, rv = _cand(db, 1)
    assert _review(db["app"], pid, "approved", None, rv).RefusedReason == "run_not_ready"


def test_review_linked(db):
    db["setup"].execute("UPDATE dbo.AttachmentBackfillCandidate SET AttachmentId = 5 WHERE Id = ?", db["cand_ids"][1])
    pid, rv = _cand(db, 1)
    assert _review(db["app"], pid, "approved", None, rv).RefusedReason == "linked"


def test_review_not_pending(db):
    pid, rv = _cand(db, 1)
    new_rv = bytes(_review(db["app"], pid, "approved", None, rv).RowVersion)
    assert _review(db["app"], pid, "rejected", "other", new_rv).RefusedReason == "not_pending"


def test_second_review_with_old_row_version_is_stale(db):
    pid, rv = _cand(db, 1)
    db["setup"].execute(
        "UPDATE dbo.AttachmentBackfillCandidate SET ModifiedDatetime = SYSUTCDATETIME() WHERE Id = ?", db["cand_ids"][1]
    )
    assert _review(db["app"], pid, "approved", None, rv).RefusedReason == "stale"


def test_review_after_undo_with_old_row_version_is_stale(db):
    pid, rv = _cand(db, 1)
    rv2 = bytes(_review(db["app"], pid, "approved", None, rv).RowVersion)
    assert _undo(db["app"], pid, rv2).RefusedReason is None
    assert _review(db["app"], pid, "approved", None, rv).RefusedReason == "stale"


def test_undo_happy_path_clears_review(db):
    pid, rv = _cand(db, 3)
    rv2 = bytes(_review(db["app"], pid, "rejected", "wrong_bill", rv).RowVersion)
    row = _undo(db["app"], pid, rv2)
    assert row.RefusedReason is None and row.ReviewStatus == "pending"
    assert row.RejectReason is None and row.ReviewedByUserPublicId is None and row.ReviewedDatetime is None


def test_undo_refusals(db):
    app = db["app"]
    pid, rv = _cand(db, 1)
    assert _undo(app, str(uuid.uuid4()), rv).RefusedReason == "not_found"
    assert _undo(app, pid, rv).RefusedReason == "not_reviewed"
    rv2 = bytes(_review(app, pid, "approved", None, rv).RowVersion)
    assert _undo(app, pid, rv).RefusedReason == "stale"
    db["setup"].execute("UPDATE dbo.AttachmentBackfillCandidate SET AttachmentId = 5 WHERE Id = ?", db["cand_ids"][1])
    _pid, rv3 = _cand(db, 1)
    assert _undo(app, pid, rv3).RefusedReason == "linked"
    db["setup"].execute("UPDATE dbo.AttachmentBackfillRun SET Status = N'applied' WHERE Id = ?", db["run_id"])
    assert _undo(app, pid, rv3).RefusedReason == "run_not_ready"
    assert rv2 != rv3


# -- stale candidate (bill deleted) ---------------------------------------------------------------------- #


def _delete_bill(db, bill_id):
    db["setup"].execute("DELETE FROM dbo.BillLineItem WHERE BillId = ?", bill_id)
    db["setup"].execute("DELETE FROM dbo.Bill WHERE Id = ?", bill_id)


def test_approve_of_stale_candidate_is_bill_missing_and_writes_nothing(db):
    _delete_bill(db, 1)
    pid, rv = _cand(db, 1)
    assert _review(db["app"], pid, "approved", None, rv).RefusedReason == "bill_missing"
    stored = db["setup"].execute(
        "SELECT ReviewStatus, RowVersion FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", db["cand_ids"][1]
    ).fetchone()
    assert stored.ReviewStatus == "pending" and bytes(stored.RowVersion) == rv


def test_bill_missing_is_checked_before_stale(db):
    _delete_bill(db, 1)
    pid, _rv = _cand(db, 1)
    assert _review(db["app"], pid, "approved", None, b"\x00" * 8).RefusedReason == "bill_missing"


def test_reject_of_stale_candidate_is_allowed(db):
    _delete_bill(db, 1)
    pid, rv = _cand(db, 1)
    row = _review(db["app"], pid, "rejected", "wrong_bill", rv)
    assert row.RefusedReason is None and row.ReviewStatus == "rejected"
    assert _undo(db["app"], pid, bytes(row.RowVersion)).RefusedReason is None


# -- fix round 1 ---------------------------------------------------------------------------------------- #


def _other_run(db, status="ready"):
    cur = db["setup"]
    run_id = cur.execute(
        "INSERT INTO dbo.AttachmentBackfillRun (Source, Status, CreatedByUserId) OUTPUT INSERTED.Id VALUES (N'qbo', ?, 17)",
        status,
    ).fetchval()
    _drain(cur)
    return run_id


def _raw_candidate(db, run_id, bill_id, file_id, review_status="pending"):
    cur = db["setup"]
    cand_id = cur.execute(
        "INSERT INTO dbo.AttachmentBackfillCandidate (RunId, BillId, SourceFileId, SourceFileName, MatchTier, ReviewStatus) "
        "OUTPUT INSERTED.Id VALUES (?, ?, ?, N'x.pdf', N'A', ?)",
        run_id, bill_id, file_id, review_status,
    ).fetchval()
    _drain(cur)
    row = cur.execute("SELECT PublicId, RowVersion FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", cand_id).fetchone()
    _drain(cur)
    return cand_id, str(row.PublicId), bytes(row.RowVersion)


def test_double_claim_via_undo_is_refused(db):
    """Reject in run A -> run B inserts the same bill -> undo A is refused; B can still be approved once."""
    app = db["app"]
    pid_a, rv_a = _cand(db, 1)
    rv_a = bytes(_review(app, pid_a, "rejected", "wrong_bill", rv_a).RowVersion)
    run_b = _other_run(db, status="generating")
    ins = _call(
        app,
        "EXEC dbo.InsertAttachmentBackfillCandidate @RunId=?, @BillId=?, @SourceFileId=?, @SourceFileName=?, "
        "@SourceFolderId=?, @MatchTier=?",
        run_b, 1, "other-file", "o.pdf", None, "A",
    )[0]
    assert ins.Inserted and ins.RefusedReason is None
    db["setup"].execute("UPDATE dbo.AttachmentBackfillRun SET Status = N'ready' WHERE Id = ?", run_b)
    assert _undo(app, pid_a, rv_a).RefusedReason == "bill_claimed_elsewhere"
    pid_b = str(ins.PublicId)
    rv_b = bytes(db["setup"].execute("SELECT RowVersion FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", ins.Id).fetchval())
    assert _review(app, pid_b, "approved", None, rv_b).RefusedReason is None
    approved = db["setup"].execute(
        "SELECT COUNT(*) FROM dbo.AttachmentBackfillCandidate WHERE BillId = 1 AND ReviewStatus = N'approved'"
    ).fetchval()
    assert approved == 1


@pytest.mark.parametrize("other_status", ["pending", "approved"])
def test_approve_refused_when_bill_claimed_in_another_open_run(db, other_status):
    run_b = _other_run(db)
    _raw_candidate(db, run_b, 1, "other-file", other_status)
    pid, rv = _cand(db, 1)
    assert _review(db["app"], pid, "approved", None, rv).RefusedReason == "bill_claimed_elsewhere"
    # reject stays allowed
    assert _review(db["app"], pid, "rejected", "duplicate", rv).RefusedReason is None


def test_claim_in_cancelled_run_or_rejected_elsewhere_does_not_block(db):
    _raw_candidate(db, _other_run(db, status="cancelled"), 1, "f-cancelled", "pending")
    _raw_candidate(db, _other_run(db), 1, "f-rejected", "rejected")
    pid, rv = _cand(db, 1)
    assert _review(db["app"], pid, "approved", None, rv).RefusedReason is None


def test_undo_of_approved_row_is_unaffected_by_claims_elsewhere(db):
    pid, rv = _cand(db, 1)
    rv2 = bytes(_review(db["app"], pid, "approved", None, rv).RowVersion)
    _raw_candidate(db, _other_run(db), 1, "other-file", "rejected")
    db["setup"].execute(
        "UPDATE dbo.AttachmentBackfillCandidate SET ReviewStatus = N'pending' WHERE SourceFileId = N'other-file'"
    )
    assert _undo(db["app"], pid, rv2).RefusedReason is None


def test_bill_claimed_elsewhere_order_after_bill_missing_before_linked(db):
    _raw_candidate(db, _other_run(db), 1, "other-file", "pending")
    db["setup"].execute("UPDATE dbo.AttachmentBackfillCandidate SET AttachmentId = 5 WHERE Id = ?", db["cand_ids"][1])
    pid, rv = _cand(db, 1)
    assert _review(db["app"], pid, "approved", None, rv).RefusedReason == "bill_claimed_elsewhere"
    _delete_bill(db, 1)
    assert _review(db["app"], pid, "approved", None, rv).RefusedReason == "bill_missing"


def test_queue_and_detail_carry_reviewer_public_id_not_int(db):
    pid, rv = _cand(db, 2)
    _review(db["app"], pid, "approved", None, rv, user_id=33)
    row = next(r for r in _queue(db["app"], db["run_public_id"], status="approved"))
    assert str(row.ReviewedByUserPublicId).lower() == U_33 and not hasattr(row, "ReviewedByUserId")
    det = _call(db["app"], "EXEC dbo.ReadAttachmentBackfillCandidateDetail @PublicId=?", pid)[0]
    assert str(det.ReviewedByUserPublicId).lower() == U_33 and not hasattr(det, "ReviewedByUserId")


def test_year_none_isolates_stale_rows_and_facets_reproduce(db):
    _delete_bill(db, 4)
    _delete_bill(db, 9)
    app, run = db["app"], db["run_public_id"]
    stale = _call(
        app, "EXEC dbo.ReadAttachmentBackfillQueue @RunPublicId=?, @NoBillYear=?, @Top=?", run, 1, 100
    )
    assert {r.BillNumber for r in stale} == {None} and len(stale) == 2
    facets = _facets(app, run)
    assert sum(f.CandidateCount for f in facets) == len(CASES)
    for f in facets:
        rows = _call(
            app,
            "EXEC dbo.ReadAttachmentBackfillQueue @RunPublicId=?, @EvidenceGroup=?, @ProjectPublicId=?, @NoProject=?, "
            "@BillYear=?, @NoBillYear=?, @ReviewStatus=?, @Top=?",
            run, f.EvidenceGroup, None if f.ProjectPublicId is None else str(f.ProjectPublicId),
            1 if f.ProjectPublicId is None else 0, f.BillYear, 1 if f.BillYear is None else 0, f.ReviewStatus, 100,
        )
        assert len(rows) == f.CandidateCount, (f.EvidenceGroup, f.ProjectName, f.BillYear)


# -- mutation pins ----------------------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["applied", "cancelled", "generating"])
def test_undo_refused_when_run_left_ready(db, status):
    """X5: undo checks the run status, not just RowVersion/review state."""
    pid, rv = _cand(db, 1)
    approved = _review(db["app"], pid, "approved", None, rv)
    assert approved.RefusedReason is None
    db["setup"].execute("UPDATE dbo.AttachmentBackfillRun SET Status = ? WHERE Id = ?", status, db["run_id"])
    _pid, current_rv = _cand(db, 1)
    assert current_rv == bytes(approved.RowVersion)
    assert _undo(db["app"], pid, current_rv).RefusedReason == "run_not_ready"
    stored = db["setup"].execute(
        "SELECT ReviewStatus, RowVersion FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", db["cand_ids"][1]
    ).fetchone()
    _drain(db["setup"])
    assert stored.ReviewStatus == "approved" and bytes(stored.RowVersion) == current_rv


def test_approve_succeeds_when_only_claim_is_the_candidate_itself(db):
    """X2: the claim check excludes the candidate's own run."""
    count = db["setup"].execute("SELECT COUNT(*) FROM dbo.AttachmentBackfillCandidate WHERE BillId = 2").fetchval()
    _drain(db["setup"])
    assert count == 1
    pid, rv = _cand(db, 2)
    row = _review(db["app"], pid, "approved", None, rv)
    assert row.RefusedReason is None and row.ReviewStatus == "approved"
