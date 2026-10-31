"""U-535b b5: review sample + sample-gated bulk approval (b5_bulk.sql) and the queue/facets 'sample' group, against local
SQL Server (synthetic rows).

Shares the Docker test DB with the b1/b2/b4 SQL files: run ONE file at a time. Reuses the b4 file's stub schema; the
fixture drops everything it created (b5 objects first: they hold FKs into the run/candidate tables).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

import pyodbc
import pytest

from tests_sqlserver import test_u535b4_apply_sql as b4

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
B5_SQL = REPO_ROOT / "entities/attachment_backfill/sql/b5_bulk.sql"
FILES = (b4.BASE_SQL, b4.B4_SQL, B5_SQL)

# (InvoiceSeenCount, AmountSeenCount, OtherInvoiceLabelCount, StatementLike) per evidence group.
CLEAN = (1, 1, 0, 0)
FLAGGED = (1, 1, 1, 0)
NEITHER = (0, 0, 0, 0)

_drain = b4._drain


def _drop_b5(cur) -> None:
    for proc in re.findall(r"CREATE OR ALTER PROCEDURE dbo\.(\w+)", B5_SQL.read_text(encoding="utf-8")):
        cur.execute(f"IF OBJECT_ID('dbo.{proc}', 'P') IS NOT NULL DROP PROCEDURE dbo.{proc}")
    cur.execute(
        "IF OBJECT_ID('FK_AttachmentBackfillCandidate_BulkApproval', 'F') IS NOT NULL "
        "ALTER TABLE dbo.AttachmentBackfillCandidate DROP CONSTRAINT FK_AttachmentBackfillCandidate_BulkApproval"
    )
    for tbl in ("dbo.AttachmentBackfillBulkApproval", "dbo.AttachmentBackfillSampleMember",
                "dbo.AttachmentBackfillSamplePopulation", "dbo.AttachmentBackfillSample"):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


class Db:
    def __init__(self, cur):
        self.cur = cur
        self.run_id = self.new_run("ready")
        self._next_bill = 1

    def new_run(self, status: str) -> int:
        rid = self.cur.execute(
            "INSERT INTO dbo.AttachmentBackfillRun (Source, Status, CreatedByUserId) OUTPUT INSERTED.Id VALUES (N'box', ?, 17)",
            status,
        ).fetchval()
        _drain(self.cur)
        return rid

    def candidate(self, evidence=CLEAN, review: str = "pending", run_id: int | None = None, bill: int | None = None,
                  make_bill: bool = True, linked: bool = False, project: int | None = None) -> int:
        if bill is None:
            bill = self._next_bill
            self._next_bill += 1
        if make_bill and not self.count("SELECT COUNT(*) FROM dbo.Bill WHERE Id = ?", bill):
            self.cur.execute("INSERT INTO dbo.Bill (Id, VendorId, BillNumber, TotalAmount) VALUES (?, 1, ?, 10)", bill, f"INV-{bill}")
            self.cur.execute("INSERT INTO dbo.BillLineItem (BillId, ProjectId) VALUES (?, ?)", bill, project)
            _drain(self.cur)
        inv, amt, other, stmt = evidence
        cid = self.cur.execute(
            """
            INSERT INTO dbo.AttachmentBackfillCandidate
                (RunId, BillId, SourceFileId, SourceFileName, SourceFileSize, SourceSha256, MatchTier, CheckStatus,
                 InvoiceSeenCount, AmountSeenCount, OtherInvoiceLabelCount, StatementLike,
                 ReviewStatus, ReviewedByUserId, ReviewedDatetime, RejectReason, AttachmentId)
            OUTPUT INSERTED.Id VALUES (?, ?, ?, N'n.pdf', 1000, ?, N'A', N'checked', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            run_id or self.run_id, bill, f"f{uuid.uuid4().hex[:12]}", b4.SHA, inv, amt, other, stmt, review,
            17 if review != "pending" else None, "2026-10-30" if review != "pending" else None,
            "other" if review == "rejected" else None, 999 if linked else None,
        ).fetchval()
        _drain(self.cur)
        return cid

    def rows(self, sql: str, *params) -> list[dict]:
        self.cur.execute(sql, *params)
        cols = [c[0] for c in self.cur.description]
        out = [dict(zip(cols, r)) for r in self.cur.fetchall()]
        _drain(self.cur)
        return out

    def row(self, sql: str, *params) -> dict:
        rows = self.rows(sql, *params)
        return rows[0] if rows else {}

    def count(self, sql: str, *params) -> int:
        return int(self.cur.execute(sql, *params).fetchval())

    def cand(self, cid: int) -> dict:
        return self.row("SELECT * FROM dbo.AttachmentBackfillCandidate WHERE Id = ?", cid)

    def pid(self, cid: int) -> str:
        return str(self.cand(cid)["PublicId"])

    def draw(self, size: int, seed: str | None = "s1", dry: bool = False, run_id: int | None = None,
             user: int | None = 17) -> dict:
        # @Seed is a test-only path: the CLI never passes it (the server generates the seed).
        return self.row(
            "EXEC dbo.DrawAttachmentBackfillSample @RunId=?, @SampleSize=?, @Seed=?, @CreatedByUserId=?, @DryRun=?",
            run_id or self.run_id, size, seed, user, 1 if dry else 0,
        )

    def close(self, run_id: int | None = None) -> dict:
        return self.row("EXEC dbo.CloseAttachmentBackfillSample @RunId=?", run_id or self.run_id)

    def members(self, run_id: int | None = None) -> list[int]:
        return [r["CandidateId"] for r in self.rows(
            "SELECT m.CandidateId FROM dbo.AttachmentBackfillSampleMember m JOIN dbo.AttachmentBackfillSample s ON s.Id = m.SampleId "
            "WHERE s.RunId = ? AND s.Status = N'open' ORDER BY m.Ordinal", run_id or self.run_id)]

    def sample_pid(self) -> str:
        return str(self.cur.execute(
            "SELECT PublicId FROM dbo.AttachmentBackfillSample WHERE RunId = ? AND Status = N'open'", self.run_id
        ).fetchval())

    def seconds(self, rows: list[tuple], sample: str | None = None, raw: str | None = None) -> dict:
        """One all-or-nothing batch: rows of (candidate id, verdict, reason)."""
        payload = raw if raw is not None else json.dumps(
            [{"candidate_public_id": self.pid(c), "verdict": v, "reason": r} for c, v, r in rows])
        return self.row(
            "EXEC dbo.RecordAttachmentBackfillSampleSecondReviews @SamplePublicId=?, @RowsJson=?, @Reviewer=?",
            sample or self.sample_pid(), payload, "claude-em",
        )

    def second(self, cid: int, verdict: str, reason: str | None = None, sample: str | None = None) -> dict:
        return self.seconds([(cid, verdict, reason)], sample)

    def verdicts(self) -> dict:
        return {r["CandidateId"]: r["SecondReviewVerdict"] for r in self.rows(
            "SELECT CandidateId, SecondReviewVerdict FROM dbo.AttachmentBackfillSampleMember")}

    def status(self) -> dict:
        return self.row("EXEC dbo.ReadAttachmentBackfillSampleStatus @RunId=?", self.run_id)

    def set_review(self, cid: int, status: str) -> None:
        self.cur.execute(
            "UPDATE dbo.AttachmentBackfillCandidate SET ReviewStatus = ?, ReviewedByUserId = 17, RejectReason = ? WHERE Id = ?",
            status, "other" if status == "rejected" else None, cid,
        )

    def b2_review(self, cid: int, decision: str, reason: str | None = None) -> dict:
        return self.row(
            "EXEC dbo.UpdateAttachmentBackfillCandidateReview @PublicId=?, @Decision=?, @RejectReason=?, @RowVersion=?, "
            "@ReviewedByUserId=17", self.pid(cid), decision, reason, bytes(self.cand(cid)["RowVersion"]))

    def b2_undo(self, cid: int) -> dict:
        return self.row("EXEC dbo.UndoAttachmentBackfillCandidateReview @PublicId=?, @RowVersion=?, @UndoneByUserId=17",
                        self.pid(cid), bytes(self.cand(cid)["RowVersion"]))

    def pass_gate(self) -> None:
        for cid in self.members():
            self.set_review(cid, "approved")
        assert self.seconds([(c, "approve", None) for c in self.members()])["RefusedReason"] is None

    def bulk(self, batch: int = 500, dry: bool = False, bulk_pid: str | None = None, user: int | None = 33) -> dict:
        return self.row(
            "EXEC dbo.BulkApproveAttachmentBackfillCandidates @RunId=?, @ApprovedByUserId=?, @BatchSize=?, "
            "@BulkApprovalPublicId=?, @DryRun=?",
            self.run_id, user, batch, bulk_pid, 1 if dry else 0,
        )

    def undo_bulk(self, bulk_pid: str, dry: bool = False) -> dict:
        return self.row(
            "EXEC dbo.UndoAttachmentBackfillBulkApproval @BulkApprovalPublicId=?, @UndoneByUserId=?, @DryRun=?",
            bulk_pid, 33, 1 if dry else 0,
        )

    def queue(self, group: str | None, review_status: str | None = None) -> list[dict]:
        run_pid = str(self.cur.execute("SELECT PublicId FROM dbo.AttachmentBackfillRun WHERE Id = ?", self.run_id).fetchval())
        return self.rows(
            "EXEC dbo.ReadAttachmentBackfillQueue @RunPublicId=?, @EvidenceGroup=?, @ReviewStatus=?, @Top=?",
            run_pid, group, review_status, 1000,
        )

    def facets(self) -> list[tuple]:
        run_pid = str(self.cur.execute("SELECT PublicId FROM dbo.AttachmentBackfillRun WHERE Id = ?", self.run_id).fetchval())
        return [tuple(r.values()) for r in self.rows("EXEC dbo.ReadAttachmentBackfillRunFacets @RunPublicId=?", run_pid)]

    def snapshot(self) -> list[tuple]:
        return [tuple(r) for r in self.cur.execute(
            "SELECT Id, ReviewStatus, ReviewedByUserId, ApprovalMethod, BulkApprovalId, CAST(RowVersion AS BIGINT) "
            "FROM dbo.AttachmentBackfillCandidate ORDER BY Id").fetchall()]

    def b5_rows(self) -> tuple:
        return tuple(self.count(f"SELECT COUNT(*) FROM {t}") for t in (
            "dbo.AttachmentBackfillSample", "dbo.AttachmentBackfillSampleMember", "dbo.AttachmentBackfillBulkApproval"))

    def bulk_ids(self) -> list[int]:
        return [r["Id"] for r in self.rows("SELECT Id FROM dbo.AttachmentBackfillCandidate WHERE ApprovalMethod = N'bulk' ORDER BY Id")]


@pytest.fixture()
def db():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    setup = pyodbc.connect(conn_str, autocommit=True)
    cur = setup.cursor()
    name = cur.execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    _drop_b5(cur)
    b4._drop_all(cur)
    b4._install_base(cur)
    for path in FILES + FILES:  # applied twice: every file is idempotent
        for batch in b4._batches(path.read_text(encoding="utf-8")):
            cur.execute(batch)
            _drain(cur)
    cur.execute(
        f"INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'Acme'); "
        f"INSERT INTO dbo.[User] (Id, PublicId) VALUES (17, '{b4.U_17}'), (33, '{b4.U_33}'); "
        f"INSERT INTO dbo.Project (Id, Name) VALUES (1, N'Alpha');"
    )
    _drain(cur)
    try:
        yield Db(cur)
    finally:
        _drop_b5(cur)
        b4._drop_all(cur)
        setup.close()


def _expected_order(db: Db, ids: list[int], seed: str) -> list[int]:
    """The draw order recomputed in Python: SHA-256 over the UTF-16LE of seed + upper-case PublicId, then Id."""
    def key(cid):
        text = seed + db.pid(cid).upper()
        return hashlib.sha256(text.encode("utf-16-le")).digest(), cid
    return sorted(ids, key=key)


# -- draw ------------------------------------------------------------------------------------------- #


def test_draw_is_deterministic_by_seed_and_matches_the_documented_hash(db):
    clean = [db.candidate() for _ in range(12)]
    out = db.draw(5, seed="alpha")
    assert out["RefusedReason"] is None and out["Drawn"] == 5 and out["EligibleCount"] == 12 and out["SamplePublicId"]
    first = db.members()
    assert first == _expected_order(db, clean, "alpha")[:5]
    db.pass_gate()  # C1b: only a COMPLETE sample may be closed and redrawn
    db.close()
    db.draw(5, seed="alpha")
    assert db.members() == first
    db.pass_gate()  # C1b: only a COMPLETE sample may be closed and redrawn
    db.close()
    db.draw(5, seed="beta")
    assert db.members() != first and db.members() == _expected_order(db, clean, "beta")[:5]


def test_b2_seed_is_server_generated_when_not_given(db):
    for _ in range(8):
        db.candidate()
    out = db.draw(3, seed=None)
    assert out["RefusedReason"] is None and re.fullmatch(r"[0-9A-F]{32}", out["Seed"])
    stored = db.row("SELECT Seed FROM dbo.AttachmentBackfillSample WHERE Status = N'open'")["Seed"]
    assert stored == out["Seed"]
    db.pass_gate()  # C1b: only a COMPLETE sample may be closed and redrawn
    db.close()
    assert db.draw(3, seed="   ")["Seed"] not in (out["Seed"], "   ")  # blank = generate too


def test_b4_draw_population_is_clean_unlinked_unclaimed_with_a_bill_and_never_bulk(db):
    eligible = [db.candidate(review="approved"), db.candidate(review="rejected"), db.candidate()]
    db.candidate(linked=True, review="approved")            # linked: no
    db.candidate(make_bill=False, bill=7000)                 # bill missing: no
    claimed = db.candidate()                                  # claimed by another non-cancelled run: no
    db.candidate(run_id=db.new_run("ready"), bill=db.cand(claimed)["BillId"])
    bulk = db.candidate(review="approved")                    # bulk-approved: no (not a human review)
    db.cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET ApprovalMethod = N'bulk' WHERE Id = ?", bulk)
    for ev in (FLAGGED, NEITHER, (1, 0, 0, 0), (1, 1, None, None)):
        db.candidate(evidence=ev)
    db.candidate(run_id=db.new_run("ready"))
    assert db.draw(4, dry=True)["RefusedReason"] == "too_few_clean"
    out = db.draw(3)
    assert out["EligibleCount"] == 3 and sorted(db.members()) == sorted(eligible)
    pop = [r["CandidateId"] for r in db.rows("SELECT CandidateId FROM dbo.AttachmentBackfillSamplePopulation ORDER BY CandidateId")]
    assert pop == sorted(eligible)


def test_draw_refusals_and_dry_run_writes_nothing(db):
    for _ in range(3):
        db.candidate()
    assert db.draw(4)["RefusedReason"] == "too_few_clean"
    assert db.draw(0)["RefusedReason"] == "invalid"
    assert db.draw(2, user=None)["RefusedReason"] == "invalid"
    assert db.draw(2, run_id=987654)["RefusedReason"] == "not_found"
    for status in ("generating", "applied", "cancelled"):
        assert db.draw(1, run_id=db.new_run(status))["RefusedReason"] == "run_not_ready"
    dry = db.draw(2, dry=True, user=None)
    assert dry["RefusedReason"] is None and dry["Drawn"] == 0 and dry["SamplePublicId"] is None and dry["EligibleCount"] == 3
    assert db.b5_rows() == (0, 0, 0)
    assert db.draw(2)["RefusedReason"] is None
    assert db.draw(1)["RefusedReason"] == "sample_open"
    assert db.draw(1, dry=True)["RefusedReason"] == "sample_open"
    assert db.b5_rows() == (1, 2, 0)


def test_one_open_sample_per_run_is_also_an_index(db):
    db.candidate()
    db.draw(1)
    with pytest.raises(pyodbc.IntegrityError):
        db.cur.execute("INSERT INTO dbo.AttachmentBackfillSample (RunId, SampleSize, Seed, CreatedByUserId) VALUES (?, 1, N'x', 17)",
                       db.run_id)
    assert db.close()["Closed"] is True
    assert db.close()["RefusedReason"] == "no_open_sample"


@pytest.mark.parametrize("side", ["human", "second"])
def test_b2_no_redraw_after_a_sample_with_a_rejection(db, side):
    for _ in range(6):
        db.candidate()
    db.draw(2)
    m = db.members()[0]
    if side == "human":
        db.set_review(m, "rejected")
    else:
        db.second(m, "reject", "wrong bill")
    db.close()
    snap = db.row("SELECT HumanRejectedAtClose, SecondRejectedAtClose FROM dbo.AttachmentBackfillSample")
    assert (snap["HumanRejectedAtClose"], snap["SecondRejectedAtClose"]) == ((1, 0) if side == "human" else (0, 1))
    if side == "human":
        db.set_review(m, "pending")  # undoing the rejection after the close does not clear the record
    assert db.draw(2)["RefusedReason"] == "prior_sample_rejection"
    assert db.draw(2, dry=True)["RefusedReason"] == "prior_sample_rejection"


def test_b2_redraw_allowed_after_a_clean_close(db):
    for _ in range(6):
        db.candidate()
    db.draw(2)
    db.pass_gate()  # C1b: only a COMPLETE sample may be closed and redrawn
    db.close()
    assert db.draw(2)["RefusedReason"] is None


# -- second review ---------------------------------------------------------------------------------- #


def test_second_review_only_for_members_of_the_open_sample_and_all_or_nothing(db):
    for _ in range(5):
        db.candidate()
    db.draw(3)
    a, b, c = db.members()
    outsider = next(x for x in range(1, 100) if x not in (a, b, c) and db.cand(x))
    out = db.second(a, "approve")
    assert out["RefusedReason"] is None and out["Recorded"] == 1 and out["SecondMissing"] == 2
    assert db.second(a, "reject", "wrong bill")["RefusedReason"] is None  # overwrite while incomplete
    m = db.row("SELECT * FROM dbo.AttachmentBackfillSampleMember WHERE CandidateId = ?", a)
    assert m["SecondReviewVerdict"] == "reject" and m["SecondReviewReason"] == "wrong bill" and m["SecondReviewer"] == "claude-em"
    before = db.verdicts()
    out = db.seconds([(b, "approve", None), (outsider, "approve", None)])
    assert out["RefusedReason"] == "not_member" and out["BadRow"] == 2 and db.verdicts() == before
    for rows in ([(b, "maybe", None)], [(b, "reject", None)], [(b, "approve", "x" * 201)], [(b, "approve", None), (b, "approve", None)]):
        assert db.seconds(rows)["RefusedReason"] == "invalid" and db.verdicts() == before
    assert db.seconds([], raw="not json")["RefusedReason"] == "invalid"
    assert db.seconds([], raw="[]")["RefusedReason"] == "invalid"
    assert db.second(b, "approve", sample=str(uuid.uuid4()))["RefusedReason"] == "sample_not_found"
    sample = db.sample_pid()
    db.close()
    assert db.second(b, "approve", sample=sample)["RefusedReason"] == "sample_closed"


def test_b8_reason_limit_is_200_utf16_units(db):
    for _ in range(2):
        db.candidate()
    db.draw(2)
    a, b = db.members()
    assert db.second(a, "reject", "\U0001F600" * 100)["RefusedReason"] is None   # 200 units
    assert db.second(b, "reject", "\U0001F600" * 101)["RefusedReason"] == "invalid"  # 202 units


def test_b1_verdicts_freeze_once_complete(db):
    for _ in range(3):
        db.candidate()
    db.draw(2)
    a, b = db.members()
    assert db.seconds([(a, "approve", None), (b, "approve", None)])["SecondMissing"] == 0
    out = db.second(a, "reject", "changed my mind")
    assert out["RefusedReason"] == "second_review_complete" and db.verdicts()[a] == "approve"


def test_b1_status_hides_the_human_side_until_the_second_review_is_complete(db):
    for _ in range(5):
        db.candidate()
    db.draw(5)
    a, b, c, d, e = db.members()
    db.set_review(a, "approved")
    db.set_review(b, "approved")
    db.set_review(c, "rejected")
    db.set_review(d, "rejected")
    db.seconds([(a, "approve", None), (b, "reject", "statement"), (c, "approve", None)])
    s = db.status()
    assert (s["Members"], s["SecondMissing"], s["SecondApprove"], s["SecondReject"], s["SecondComplete"]) == (5, 2, 2, 1, False)
    for hidden in ("HumanPending", "HumanApproved", "HumanRejected", "Disagreements", "Linked"):
        assert s[hidden] is None, hidden
    db.seconds([(d, "approve", None), (e, "approve", None)])
    s = db.status()
    assert s["SecondComplete"] is True
    assert (s["HumanPending"], s["HumanApproved"], s["HumanRejected"]) == (1, 2, 2)
    assert (s["SecondMissing"], s["SecondApprove"], s["SecondReject"], s["Disagreements"]) == (0, 4, 1, 3)
    assert s["BulkApprovalPublicId"] is None


def test_members_export_proc_is_blind(db):
    db.candidate(review="approved", project=1)
    db.draw(1)
    rows = db.rows("EXEC dbo.ReadAttachmentBackfillSampleMembers @RunId=?", db.run_id)
    assert len(rows) == 1
    keys = set(rows[0])
    assert not keys & {"ReviewStatus", "ReviewedByUserId", "ReviewedByUserPublicId", "RejectReason", "SecondReviewVerdict",
                       "ApprovalMethod"}
    assert rows[0]["ProjectName"] == "Alpha" and rows[0]["LineCount"] == 1 and rows[0]["BillNumber"] == "INV-1"


# -- bulk gate -------------------------------------------------------------------------------------- #


def _setup_sample(db, members=3, extra=0):
    for _ in range(members + extra):
        db.candidate()
    db.draw(members)


def test_bulk_refused_without_an_open_sample_or_ready_run(db):
    db.candidate()
    assert db.bulk()["RefusedReason"] == "no_open_sample"
    assert db.bulk(batch=0)["RefusedReason"] == "invalid"
    assert db.bulk(user=None)["RefusedReason"] == "invalid"
    db.cur.execute("UPDATE dbo.AttachmentBackfillRun SET Status = N'applied' WHERE Id = ?", db.run_id)
    assert db.bulk()["RefusedReason"] == "run_not_ready"


@pytest.mark.parametrize("breaker,code", [
    ("human_pending", "sample_incomplete"),
    ("human_bulk", "sample_incomplete"),
    ("human_rejected", "sample_has_rejection"),
    ("second_reject", "sample_has_rejection"),
    ("member_lost", "sample_size_mismatch"),
])
def test_bulk_gate_refusals_write_nothing(db, breaker, code):
    _setup_sample(db, members=3, extra=4)
    m = db.members()[0]
    for cid in db.members():
        db.set_review(cid, "approved")
    db.seconds([(c, "reject" if (breaker == "second_reject" and c == m) else "approve",
                 "wrong" if (breaker == "second_reject" and c == m) else None) for c in db.members()])
    if breaker == "human_pending":
        db.set_review(m, "pending")
    elif breaker == "human_bulk":
        db.cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET ApprovalMethod = N'bulk' WHERE Id = ?", m)
    elif breaker == "human_rejected":
        db.set_review(m, "rejected")
    elif breaker == "member_lost":
        db.cur.execute("DELETE FROM dbo.AttachmentBackfillSampleMember WHERE CandidateId = ?", m)
    before = db.snapshot()
    out = db.bulk()
    assert out["RefusedReason"] == code and out["Approved"] == 0 and out["BulkApprovalPublicId"] is None
    assert db.snapshot() == before and db.b5_rows()[2] == 0
    assert db.bulk(dry=True)["RefusedReason"] == code


def test_b1_gate_code_is_generic_until_the_second_review_is_complete(db):
    _setup_sample(db, members=2, extra=2)
    a, b = db.members()
    db.set_review(a, "rejected")
    db.second(a, "approve")  # b has no second verdict yet
    assert db.bulk(dry=True)["RefusedReason"] == "second_review_incomplete"
    assert db.bulk()["RefusedReason"] == "second_review_incomplete"
    db.second(b, "approve")
    assert db.bulk(dry=True)["RefusedReason"] == "sample_has_rejection"


def test_b2_bulk_refused_when_an_earlier_sample_has_a_rejection(db):
    _setup_sample(db, members=2, extra=4)
    first = db.members()
    db.pass_gate()  # C1b: only a COMPLETE sample may be closed and redrawn
    db.close()
    db.draw(2)
    db.pass_gate()
    assert db.bulk(dry=True)["RefusedReason"] is None
    db.set_review(first[0], "rejected")  # a member of the CLOSED sample, rejected after the close
    assert db.bulk()["RefusedReason"] == "prior_sample_rejection"
    db.set_review(first[0], "pending")
    db.cur.execute("UPDATE dbo.AttachmentBackfillSample SET SecondRejectedAtClose = 1 WHERE Status = N'closed'")
    assert db.bulk()["RefusedReason"] == "prior_sample_rejection"


def test_bulk_targets_and_exclusions(db):
    clean = [db.candidate() for _ in range(6)]
    db.candidate(evidence=FLAGGED)
    db.candidate(evidence=NEITHER)
    approved = db.candidate(review="approved")
    rejected = db.candidate(review="rejected")
    db.candidate(linked=True)
    cancelled_claim = db.candidate()
    db.candidate(run_id=db.new_run("cancelled"), bill=db.cand(cancelled_claim)["BillId"])  # cancelled run: no claim
    db.draw(2, seed="m")
    members = db.members()
    pending_pop = [c for c in clean + [cancelled_claim] if c not in members]
    db.pass_gate()
    # After the draw: one population row's bill goes missing, another's bill gets claimed by another run.
    missing, claimed = pending_pop[0], pending_pop[1]
    db.cur.execute("DELETE FROM dbo.Bill WHERE Id = ?", db.cand(missing)["BillId"])
    db.candidate(run_id=db.new_run("ready"), bill=db.cand(claimed)["BillId"])
    expected = sorted(pending_pop[2:])

    dry = db.bulk(dry=True, user=None)
    assert dry["RefusedReason"] is None and dry["Remaining"] == len(expected) and dry["Approved"] == 0
    assert (dry["ExcludedBillMissing"], dry["ExcludedClaimedElsewhere"], dry["SampleMembers"]) == (1, 1, 2)
    assert db.b5_rows()[2] == 0

    before = {c: bytes(db.cand(c)["RowVersion"]) for c in expected}
    out = db.bulk()
    assert out["RefusedReason"] is None and out["Approved"] == len(expected) and out["Remaining"] == 0
    bulk_id = db.count("SELECT Id FROM dbo.AttachmentBackfillBulkApproval WHERE PublicId = ?", out["BulkApprovalPublicId"])
    assert db.bulk_ids() == sorted(expected)
    for c in expected:
        row = db.cand(c)
        assert (row["ReviewStatus"], row["ReviewedByUserId"], row["ApprovalMethod"], row["BulkApprovalId"]) == ("approved", 33, "bulk", bulk_id)
        assert bytes(row["RowVersion"]) != before[c]
    assert db.cand(missing)["ReviewStatus"] == "pending" and db.cand(claimed)["ReviewStatus"] == "pending"
    if rejected not in members:  # a drawn member is set approved by pass_gate
        assert db.cand(rejected)["ReviewStatus"] == "rejected"
    ba = db.row("SELECT * FROM dbo.AttachmentBackfillBulkApproval")
    assert ba["ApprovedCount"] == len(expected) and ba["ApprovedByUserId"] == 33 and ba["UndoneDatetime"] is None
    assert db.status()["BulkApprovalPublicId"] == ba["PublicId"]
    assert db.second(db.members()[0], "reject", "late")["RefusedReason"] in ("bulk_approved", "second_review_complete")


def test_b3_candidates_clean_after_the_draw_are_never_bulk_approved(db):
    _setup_sample(db, members=1, extra=2)
    late_clean = db.candidate(evidence=NEITHER)  # not clean at draw time
    db.pass_gate()
    db.cur.execute(
        "UPDATE dbo.AttachmentBackfillCandidate SET InvoiceSeenCount = 1, AmountSeenCount = 1 WHERE Id = ?", late_clean)
    new = db.candidate()  # created after the draw
    out = db.bulk()
    assert out["Approved"] == 2 and late_clean not in db.bulk_ids() and new not in db.bulk_ids()
    # A population row that stopped being clean is not approved either.
    assert db.bulk()["Approved"] == 0


def test_bulk_batches_continue_one_bulk_approval(db):
    _setup_sample(db, members=1, extra=5)
    db.pass_gate()
    first = db.bulk(batch=2)
    assert (first["Approved"], first["Remaining"]) == (2, 3)
    second = db.bulk(batch=2)
    assert second["BulkApprovalPublicId"] == first["BulkApprovalPublicId"] and (second["Approved"], second["Remaining"]) == (2, 1)
    third = db.bulk(batch=2, bulk_pid=str(first["BulkApprovalPublicId"]))
    assert (third["Approved"], third["Remaining"]) == (1, 0)
    assert db.bulk(bulk_pid=str(uuid.uuid4()))["RefusedReason"] == "bulk_not_found"
    assert db.count("SELECT COUNT(*) FROM dbo.AttachmentBackfillBulkApproval") == 1
    assert db.row("SELECT ApprovedCount FROM dbo.AttachmentBackfillBulkApproval")["ApprovedCount"] == 5
    done = db.bulk()
    assert done["Approved"] == 0 and done["Remaining"] == 0


# -- undo ------------------------------------------------------------------------------------------- #


def test_undo_bulk_reverts_only_unlinked_and_completes_after_link_undo(db):
    _setup_sample(db, members=1, extra=3)
    db.pass_gate()
    bulk_pid = str(db.bulk()["BulkApprovalPublicId"])
    approved = db.bulk_ids()
    linked = approved[0]
    db.cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET AttachmentId = 999 WHERE Id = ?", linked)
    dry = db.undo_bulk(bulk_pid, dry=True)
    assert (dry["Reverted"], dry["KeptLinked"], dry["Completed"], dry["RefusedReason"]) == (2, 1, 0, None)
    assert all(db.cand(c)["ReviewStatus"] == "approved" for c in approved)
    res = db.undo_bulk(bulk_pid)
    assert (res["Reverted"], res["KeptLinked"], res["Completed"], res["PartiallyUndone"]) == (2, 1, 0, 1)
    for c in approved[1:]:
        row = db.cand(c)
        assert (row["ReviewStatus"], row["ReviewedByUserId"], row["ApprovalMethod"], row["BulkApprovalId"]) == ("pending", None, None, None)
        assert row["UndoneByUserId"] == 33 and row["UndoneDatetime"] is not None
    assert db.cand(linked)["ReviewStatus"] == "approved" and db.cand(linked)["ApprovalMethod"] == "bulk"
    ba = db.row("SELECT ApprovedCount, PartiallyUndone, UndoneDatetime FROM dbo.AttachmentBackfillBulkApproval")
    assert (ba["ApprovedCount"], ba["PartiallyUndone"], ba["UndoneDatetime"]) == (1, True, None)
    # B5: no silent re-approve of the reverted rows while the approval is partially undone.
    assert db.bulk()["RefusedReason"] == "bulk_partially_undone"
    assert db.bulk(dry=True)["RefusedReason"] == "bulk_partially_undone"
    db.cur.execute("UPDATE dbo.AttachmentBackfillCandidate SET AttachmentId = NULL WHERE Id = ?", linked)  # b4 undo
    res = db.undo_bulk(bulk_pid)
    assert (res["Reverted"], res["KeptLinked"], res["Completed"]) == (1, 0, 1)
    ba = db.row("SELECT ApprovedCount, UndoneByUserId FROM dbo.AttachmentBackfillBulkApproval")
    assert (ba["ApprovedCount"], ba["UndoneByUserId"]) == (0, 33)
    assert db.undo_bulk(bulk_pid)["RefusedReason"] == "already_undone"
    assert db.undo_bulk(str(uuid.uuid4()))["RefusedReason"] == "not_found"
    assert db.bulk()["Approved"] == 3  # fully undone: a new bulk approval may be made


def test_undo_bulk_refused_when_run_not_ready(db):
    _setup_sample(db, members=1, extra=1)
    db.pass_gate()
    bulk_pid = str(db.bulk()["BulkApprovalPublicId"])
    db.cur.execute("UPDATE dbo.AttachmentBackfillRun SET Status = N'cancelled' WHERE Id = ?", db.run_id)
    assert db.undo_bulk(bulk_pid)["RefusedReason"] == "run_not_ready"


def test_individual_undo_and_reapprove_detaches_from_the_bulk(db):
    _setup_sample(db, members=1, extra=1)
    db.pass_gate()
    bulk_pid = str(db.bulk()["BulkApprovalPublicId"])
    target = db.bulk_ids()[0]
    pid = db.pid(target)
    out = db.row("EXEC dbo.UndoAttachmentBackfillCandidateReview @PublicId=?, @RowVersion=?, @UndoneByUserId=17",
                 pid, bytes(db.cand(target)["RowVersion"]))
    assert out["RefusedReason"] is None
    assert (db.cand(target)["ApprovalMethod"], db.cand(target)["BulkApprovalId"]) == (None, None)
    out = db.row("EXEC dbo.UpdateAttachmentBackfillCandidateReview @PublicId=?, @Decision=N'approved', @RowVersion=?, @ReviewedByUserId=17",
                 pid, bytes(db.cand(target)["RowVersion"]))
    assert out["RefusedReason"] is None
    res = db.undo_bulk(bulk_pid)
    assert (res["Reverted"], res["Completed"]) == (0, 1)
    assert db.cand(target)["ReviewStatus"] == "approved" and db.cand(target)["ReviewedByUserId"] == 17


# -- queue / facets --------------------------------------------------------------------------------- #


def test_queue_and_facets_sample_group(db):
    for _ in range(4):
        db.candidate(project=1)
    db.candidate(evidence=FLAGGED, project=1)
    db.candidate(evidence=NEITHER)
    groups = ("clean", "flagged", "invoice_only", "amount_only", "neither", None)
    before_queue = {g: db.queue(g, None) for g in groups}
    before_facets = db.facets()
    assert db.queue("sample", None) == []
    db.draw(3)
    members = db.members()
    db.set_review(members[0], "approved")
    sample_rows = db.queue("sample", None)
    assert sorted(r["Id"] for r in sample_rows) == sorted(members)
    assert {r["EvidenceGroup"] for r in sample_rows} == {"clean"}
    assert [r["Id"] for r in db.queue("sample", "pending")] == [r["Id"] for r in sample_rows if r["Id"] != members[0]]
    after_facets = db.facets()
    sample_facets = [f for f in after_facets if f[0] == "sample"]
    assert sum(f[-1] for f in sample_facets) == 3
    assert after_facets[-len(sample_facets):] == sample_facets
    db.set_review(members[0], "pending")
    assert [f for f in db.facets() if f[0] != "sample"] == before_facets
    for g in groups:
        assert [(r["Id"], r["ReviewStatus"]) for r in db.queue(g, None)] == [(r["Id"], r["ReviewStatus"]) for r in before_queue[g]]
    db.close()
    assert db.queue("sample", None) == [] and db.facets() == before_facets


# -- fix round 2 ------------------------------------------------------------------------------------ #


def test_c1a_human_reject_then_b2_undo_is_sticky_for_the_gate_and_the_redraw(db):
    _setup_sample(db, members=2, extra=4)
    a, b = db.members()
    assert db.b2_review(a, "rejected", "wrong_bill")["RefusedReason"] is None
    assert db.b2_undo(a)["RefusedReason"] is None
    assert db.b2_review(a, "approved")["RefusedReason"] is None
    db.set_review(b, "approved")
    db.seconds([(a, "approve", None), (b, "approve", None)])
    assert db.cand(a)["RejectCount"] == 1 and db.cand(a)["ReviewStatus"] == "approved"
    m = db.row("SELECT RejectCountAtDraw, EverRejectedSecond FROM dbo.AttachmentBackfillSampleMember WHERE CandidateId = ?", a)
    assert (m["RejectCountAtDraw"], m["EverRejectedSecond"]) == (0, False)
    assert db.bulk(dry=True)["RefusedReason"] == "sample_has_rejection"
    db.close()
    assert db.draw(2)["RefusedReason"] == "prior_sample_rejection"


def test_c1a_second_reject_overwritten_before_the_freeze_is_sticky(db):
    _setup_sample(db, members=2, extra=4)
    a, b = db.members()
    db.second(a, "reject", "statement")
    for c in (a, b):
        db.set_review(c, "approved")
    assert db.seconds([(a, "approve", None), (b, "approve", None)])["RefusedReason"] is None  # overwrite + complete
    assert db.row("SELECT EverRejectedSecond FROM dbo.AttachmentBackfillSampleMember WHERE CandidateId = ?", a)["EverRejectedSecond"] is True
    assert db.bulk()["RefusedReason"] == "sample_has_rejection"
    db.close()
    assert db.draw(2)["RefusedReason"] == "prior_sample_rejection"


def test_c1a_a_reject_before_the_draw_or_of_a_non_member_does_not_block(db):
    early = db.candidate()
    db.b2_review(early, "rejected", "other")
    db.b2_undo(early)  # rejected and undone BEFORE any sample: RejectCount 1, captured at draw
    for _ in range(3):
        db.candidate()
    db.draw(4)  # every population row is a member, `early` included
    assert db.row("SELECT RejectCountAtDraw FROM dbo.AttachmentBackfillSampleMember WHERE CandidateId = ?", early)["RejectCountAtDraw"] == 1
    outsider = db.candidate()  # not in the population
    assert db.b2_review(outsider, "rejected", "other")["RefusedReason"] is None
    db.pass_gate()
    assert db.bulk(dry=True)["RefusedReason"] is None


@pytest.mark.parametrize("gap", ["human_pending", "second_missing"])
def test_c1b_closing_an_incomplete_sample_blocks_the_redraw(db, gap):
    _setup_sample(db, members=2, extra=4)
    a, b = db.members()
    if gap == "human_pending":
        db.set_review(a, "approved")
        db.seconds([(a, "approve", None), (b, "approve", None)])
    else:
        db.set_review(a, "approved")
        db.set_review(b, "approved")
        db.second(a, "approve")
    out = db.close()
    assert (out["HumanPendingAtClose"], out["SecondMissingAtClose"]) == ((1, 0) if gap == "human_pending" else (0, 1))
    assert db.draw(2)["RefusedReason"] == "prior_sample_incomplete"
    assert db.draw(2, dry=True)["RefusedReason"] == "prior_sample_incomplete"


def test_c3_status_bulk_count_is_live(db):
    _setup_sample(db, members=1, extra=3)
    db.pass_gate()
    db.bulk()
    target = db.bulk_ids()[0]
    assert db.b2_undo(target)["RefusedReason"] is None  # leaves the bulk approval
    s = db.status()
    assert s["BulkApprovedCount"] == 2
    assert db.row("SELECT ApprovedCount FROM dbo.AttachmentBackfillBulkApproval")["ApprovedCount"] == 3


# -- round 3 ---------------------------------------------------------------------------------------- #


def test_d1_a_member_rejected_at_draw_counts_as_rejected_during_the_sample(db):
    early = db.candidate()
    assert db.b2_review(early, "rejected", "other")["RefusedReason"] is None  # rejected BEFORE the draw
    for _ in range(2):
        db.candidate()
    db.draw(3)  # the whole population: `early` is a member, still rejected
    assert db.b2_undo(early)["RefusedReason"] is None
    assert db.b2_review(early, "approved")["RefusedReason"] is None
    db.pass_gate()
    assert db.bulk()["RefusedReason"] == "sample_has_rejection"
    db.close()
    assert db.draw(1)["RefusedReason"] == "prior_sample_rejection"


def test_d1_pre_migration_rejected_row_with_zero_reject_count(db):
    old = db.candidate(review="rejected")  # RejectCount 0: rejected before the column existed
    assert db.cand(old)["RejectCount"] == 0
    db.candidate()
    db.draw(2)
    db.pass_gate()  # sets `old` approved directly (no review proc: RejectCount stays 0)
    assert db.bulk(dry=True)["RefusedReason"] == "sample_has_rejection"


def test_d2_status_returns_a_masked_sticky_rejection_count(db):
    _setup_sample(db, members=2, extra=2)
    a, b = db.members()
    db.b2_review(a, "rejected", "other")
    db.b2_undo(a)
    db.b2_review(a, "approved")
    db.set_review(b, "approved")
    db.second(a, "approve")
    assert db.status()["StickyRejections"] is None  # hidden until the second review is complete
    db.second(b, "approve")
    s = db.status()
    assert s["StickyRejections"] == 1 and s["HumanRejected"] == 0
