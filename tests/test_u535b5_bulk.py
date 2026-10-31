"""U-535b b5: review sample + sample-gated bulk approval -- CLI, business and router (fakes only: no DB, no Box)."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from entities.attachment_backfill.business import bulk as bulk_mod
from entities.attachment_backfill.business.bulk import (
    MANIFEST_FIELDS,
    AttachmentBackfillBulkService,
    SecondReviewCsvError,
    manifest_member,
    parse_second_review_csv,
)
from entities.attachment_backfill.business.service import MAX_VERIFY_BYTES
from scripts import attachment_backfill_bulk as cli
from tests.test_u535b2_router import PREFIX, RUN, _as, client, fetch, ocr_calls, repo  # noqa: F401 (fixtures)

RUN_ID = 41
SAMPLE = "dddddddd-dddd-dddd-dddd-dddddddddddd"
BULK = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
PDF = b"%PDF-1.7\n" + b"1" * 64
M1, M2, M3 = (str(uuid.UUID(int=i)) for i in (1, 2, 3))
HUMAN_KEYS = {"review_status", "reviewstatus", "reviewer", "reviewed_by_user_id", "reject_reason", "second_review_verdict"}


def _member(pid: str, ordinal: int, **over) -> dict:
    row = {
        "SamplePublicId": uuid.UUID(SAMPLE), "Ordinal": ordinal, "CandidatePublicId": uuid.UUID(pid),
        "SourceFileId": f"box-{ordinal}", "SourceSha256": hashlib.sha256(PDF).hexdigest().upper(), "SourceFileSize": 1000,
        "BillNumber": f"INV-{ordinal}", "TotalAmount": Decimal("10.50"), "BillDate": datetime(2023, 1, 2),
        "VendorName": "Acme", "ProjectName": "Alpha", "LineCount": 2, "BillExists": True,
        "InvoiceSeenCount": 1, "AmountSeenCount": 1, "VendorSeen": True, "OtherInvoiceLabelCount": 0,
        "StatementLike": False, "UsedOcr": False,
        # A leak trap: even if a row carried the human decision, the manifest must not.
        "ReviewStatus": "approved", "ReviewedByUserId": 17, "RejectReason": None,
    }
    row.update(over)
    return row


class FakeRepo:
    WRITES = {"draw_live", "close_sample", "record_second_reviews", "bulk_live", "undo_live"}

    def __init__(self):
        self.calls: list[tuple] = []
        self.run = {"Id": RUN_ID, "PublicId": RUN, "Status": "ready"}
        self.members = [_member(M1, 1), _member(M2, 2), _member(M3, 3)]
        self.status = {"SamplePublicId": uuid.UUID(SAMPLE), "Status": "open", "Members": 3}
        self.draw_out = {"SamplePublicId": None, "Seed": "A1B2", "EligibleCount": 900, "Drawn": 0, "RefusedReason": None}
        self.bulk_plan = {"RefusedReason": None, "Remaining": 7, "Approved": 0, "ExcludedBillMissing": 1,
                          "ExcludedClaimedElsewhere": 2, "SampleMembers": 3}
        self.bulk_batches: list[dict] = []
        self.record_out = {"Recorded": 2, "SecondMissing": 1, "BadRow": None, "RefusedReason": None}
        self.undo_out = {"Reverted": 5, "KeptLinked": 0, "Completed": 1, "RefusedReason": None}

    def writes(self):
        return [c for c in self.calls if c[0] in self.WRITES]

    def read_run_by_public_id(self, public_id):
        self.calls.append(("read_run", public_id))
        return dict(self.run) if public_id == RUN and self.run else None

    def draw_sample(self, **kw):
        self.calls.append(("draw_dry" if kw["dry_run"] else "draw_live", kw))
        return dict(self.draw_out)

    def close_sample(self, **kw):
        self.calls.append(("close_sample", kw))
        return {"SamplePublicId": uuid.UUID(SAMPLE), "Closed": True, "RefusedReason": None}

    def read_sample_status(self, **kw):
        self.calls.append(("read_sample_status", kw))
        return dict(self.status) if self.status else None

    def read_sample_members(self, **kw):
        self.calls.append(("read_sample_members", kw))
        return [dict(m) for m in self.members]

    def record_second_reviews(self, **kw):
        self.calls.append(("record_second_reviews", kw))
        return dict(self.record_out)

    def bulk_approve(self, **kw):
        if kw["dry_run"]:
            self.calls.append(("bulk_dry", kw))
            return dict(self.bulk_plan)
        self.calls.append(("bulk_live", kw))
        return self.bulk_batches.pop(0)

    def undo_bulk_approval(self, **kw):
        self.calls.append(("undo_live" if not kw["dry_run"] else "undo_dry", kw))
        return dict(self.undo_out)


class Stop:
    def __init__(self, after: int | None = None):
        self.after, self.n = after, 0

    def __call__(self):
        self.n += 1
        return self.after is not None and self.n > self.after

    def handle(self, *_):
        pass


def _main(argv, repo, *, stop=None, fetch_fn=None):
    service = AttachmentBackfillBulkService(repo)
    factories = {}
    if fetch_fn is not None:
        factories["make_fetch"] = lambda: (fetch_fn, lambda: None)
    return cli.main(argv, service=service, stop=stop or Stop(), **factories)


@pytest.fixture
def frepo():
    return FakeRepo()


# -- CSV ------------------------------------------------------------------------------------------- #


def test_csv_valid_rows_are_canonicalised():
    text = f"candidate_public_id,verdict,reason\n{M1.upper()},APPROVE,\n{M2},reject, wrong bill \n\n"
    rows = parse_second_review_csv(text, {M1, M2, M3})
    assert [(r.candidate_public_id, r.verdict, r.reason) for r in rows] == [(M1, "approve", None), (M2, "reject", "wrong bill")]


@pytest.mark.parametrize("body,code", [
    ("nope,approve,\n", "bad_candidate_id"),
    (f"{uuid.uuid4()},approve,\n", "not_member"),
    (f"{M1},approve,\n{M1},approve,\n", "duplicate"),
    (f"{M1},maybe,\n", "bad_verdict"),
    (f"{M1},reject,\n", "reason_required"),
    (f"{M1},approve,{'x' * 201}\n", "reason_too_long"),
])
def test_csv_errors_are_reported_by_code(body, code):
    with pytest.raises(SecondReviewCsvError) as exc:
        parse_second_review_csv("candidate_public_id,verdict,reason\n" + body, {M1, M2})
    assert code in [c for _, c in exc.value.errors]


def test_csv_missing_columns_and_empty():
    with pytest.raises(SecondReviewCsvError) as exc:
        parse_second_review_csv("candidate_public_id,verdict\nx,approve\n", {M1})
    assert exc.value.errors == [(1, "missing_columns")]
    with pytest.raises(SecondReviewCsvError) as exc:
        parse_second_review_csv("candidate_public_id,verdict,reason\n", {M1})
    assert exc.value.errors == [(1, "empty")]


# -- manifest / export (blind) ---------------------------------------------------------------------- #


def test_manifest_member_is_whitelisted_and_blind():
    entry = manifest_member(_member(M1, 1), pdf_file="x.pdf", pdf_error=None, content_matches_check=True)
    assert tuple(entry) == MANIFEST_FIELDS
    assert not {k.lower() for k in entry} & HUMAN_KEYS
    assert "approved" not in json.dumps(entry)
    assert entry["bill_amount"] == "10.50" and entry["bill_date"] == "2023-01-02T00:00:00"


def test_export_writes_pdfs_marks_mismatches_and_skips_oversize(tmp_path, frepo):
    frepo.members = [
        _member(M1, 1),
        _member(M2, 2, SourceSha256="0" * 64),            # content changed since the check
        _member(M3, 3, SourceFileSize=MAX_VERIFY_BYTES + 1),  # no download at all
        _member(str(uuid.UUID(int=4)), 4, SourceFileId="bad"),
        _member(str(uuid.UUID(int=5)), 5, SourceFileId="html"),
    ]
    fetched = []

    def fetch(fid):
        fetched.append(fid)
        if fid == "bad":
            raise RuntimeError("secret detail")
        return b"<html>" if fid == "html" else PDF

    service = AttachmentBackfillBulkService(frepo, fetch_bytes=fetch)
    counts = Counter()
    out_dir = tmp_path / "export"
    assert service.export_sample(RUN_ID, out_dir, counts=counts) is None
    assert fetched == ["box-1", "box-2", "bad", "html"]
    assert sorted(p.name for p in out_dir.iterdir()) == sorted([f"{M1}.pdf", f"{M2}.pdf", "manifest.json"])
    assert (out_dir / f"{M1}.pdf").read_bytes() == PDF
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["sample_public_id"] == SAMPLE and len(manifest["members"]) == 5
    by = {m["candidate_public_id"]: m for m in manifest["members"]}
    assert by[M1]["content_matches_check"] is True and by[M2]["content_matches_check"] is False
    assert by[M3]["pdf_error"] == "too_large" and by[M3]["pdf_file"] is None
    assert by[str(uuid.UUID(int=4))]["pdf_error"] == "fetch_failed"
    assert by[str(uuid.UUID(int=5))]["pdf_error"] == "not_pdf"
    for m in manifest["members"]:
        assert tuple(m) == MANIFEST_FIELDS and not {k.lower() for k in m} & HUMAN_KEYS
    assert "approved" not in (out_dir / "manifest.json").read_text()
    assert counts == Counter(members=5, written=2, content_matches=1, content_changed=1, error_too_large=1,
                             error_fetch_failed=1, error_not_pdf=1)
    assert frepo.writes() == []


def test_export_interrupt_still_writes_the_manifest(tmp_path, frepo):
    service = AttachmentBackfillBulkService(frepo, fetch_bytes=lambda fid: PDF)
    service.stop_requested = Stop(after=1)
    counts = Counter()
    assert service.export_sample(RUN_ID, tmp_path / "e", counts=counts) == "interrupted"
    manifest = json.loads((tmp_path / "e" / "manifest.json").read_text())
    assert [m["pdf_error"] for m in manifest["members"]] == [None, "not_fetched", "not_fetched"]


def test_cli_export_refuses_a_dir_inside_the_repo(frepo):
    target = Path(cli.REPO_ROOT) / "tmp-u535b5-export"
    assert _main(["--run", RUN, "--export-sample", str(target)], frepo, fetch_fn=lambda f: PDF) == cli.EXIT_USAGE
    assert not target.exists() and frepo.writes() == []


def test_cli_export_refuses_a_non_empty_dir_and_succeeds_on_a_new_one(tmp_path, frepo, capsys):
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    assert _main(["--run", RUN, "--export-sample", str(tmp_path / "full")], frepo, fetch_fn=lambda f: PDF) == cli.EXIT_USAGE
    assert _main(["--run", RUN, "--export-sample", str(tmp_path / "new")], frepo, fetch_fn=lambda f: PDF) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "INV-1" not in out and "Acme" not in out  # counts only on stdout
    assert frepo.writes() == []


# -- CLI: draw / status / record / close ------------------------------------------------------------ #


def test_draw_dry_run_writes_nothing(frepo, capsys):
    assert _main(["--run", RUN, "--draw-sample"], frepo) == cli.EXIT_OK
    assert frepo.writes() == [] and frepo.calls[-1][0] == "draw_dry"
    assert frepo.calls[-1][1] == {"run_id": RUN_ID, "sample_size": 300, "created_by_user_id": 17, "dry_run": True}
    out = capsys.readouterr().out
    assert "eligible_clean: 900" in out and "DRY RUN" in out


def test_b2_draw_seed_is_never_caller_chosen(frepo, capsys):
    with pytest.raises(SystemExit):
        cli.parse_args(["--run", RUN, "--draw-sample", "--seed", "abc"])
    frepo.draw_out = {"SamplePublicId": uuid.UUID(SAMPLE), "Seed": "F00D" * 8, "EligibleCount": 900, "Drawn": 25,
                      "RefusedReason": None}
    assert _main(["--run", RUN, "--draw-sample", "--size", "25", "--apply"], frepo) == cli.EXIT_OK
    kw = frepo.calls[-1][1]
    assert frepo.calls[-1][0] == "draw_live" and kw["sample_size"] == 25 and "seed" not in kw
    out = capsys.readouterr().out
    assert f"seed: {'F00D' * 8} (server-generated)" in out and f"sample: {SAMPLE}" in out


def test_draw_refusal_and_non_ready_run(frepo):
    frepo.draw_out = {"EligibleCount": 3, "Drawn": 0, "RefusedReason": "too_few_clean"}
    assert _main(["--run", RUN, "--draw-sample", "--apply"], frepo) == cli.EXIT_REFUSED
    frepo.run["Status"] = "applied"
    assert _main(["--run", RUN, "--draw-sample", "--apply"], frepo) == cli.EXIT_USAGE
    assert _main(["--run", RUN, "--bulk-approve", "--apply"], frepo) == cli.EXIT_USAGE
    assert [c for c in frepo.calls if c[0].startswith(("draw", "bulk"))] == [("draw_live", frepo.calls[1][1])]


def test_unknown_run_and_arg_errors(frepo):
    assert _main(["--run", str(uuid.uuid4()), "--status"], frepo) == cli.EXIT_USAGE
    with pytest.raises(SystemExit):
        cli.parse_args(["--status"])  # --run required
    with pytest.raises(SystemExit):
        cli.parse_args(["--run", RUN, "--status", "--bulk-approve"])  # one action
    with pytest.raises(SystemExit):
        cli.parse_args(["--run", "nope", "--status"])


def test_status_prints_counts(frepo, capsys):
    frepo.status.update(SecondComplete=True, SecondMissing=0, HumanPending=1, Disagreements=0)
    assert _main(["--run", RUN, "--status"], frepo) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "members: 3" in out and "human_pending: 1" in out and frepo.writes() == []


def _csv(tmp_path, body):
    p = tmp_path / "v.csv"
    p.write_text("candidate_public_id,verdict,reason\n" + body)
    return str(p)


def test_record_dry_run_validates_and_writes_nothing(tmp_path, frepo, capsys):
    path = _csv(tmp_path, f"{M1},approve,\n{M2},reject,statement\n")
    assert _main(["--run", RUN, "--record-second-review", path], frepo) == cli.EXIT_OK
    assert frepo.writes() == []
    assert "approve 1, reject 1" in capsys.readouterr().out


def test_record_invalid_csv_writes_nothing(tmp_path, frepo):
    path = _csv(tmp_path, f"{M1},approve,\n{uuid.uuid4()},approve,\n")
    assert _main(["--run", RUN, "--record-second-review", path, "--apply"], frepo) == cli.EXIT_INVALID
    assert frepo.writes() == []


def test_b1_record_apply_is_one_all_or_nothing_call(tmp_path, frepo, capsys):
    path = _csv(tmp_path, f"{M1},approve,\n{M2},reject,statement\n")
    assert _main(["--run", RUN, "--record-second-review", path, "--apply", "--reviewer", "em"], frepo) == cli.EXIT_OK
    writes = frepo.writes()
    assert len(writes) == 1 and writes[0][0] == "record_second_reviews"
    kw = writes[0][1]
    assert kw["sample_public_id"] == SAMPLE and kw["reviewer"] == "em"
    assert [(r.candidate_public_id, r.verdict, r.reason) for r in kw["rows"]] == [(M1, "approve", None), (M2, "reject", "statement")]
    out = capsys.readouterr().out
    assert "recorded: 2" in out and "second_missing: 1" in out


def test_record_refused_by_db_is_exit_3_and_needs_open_sample(tmp_path, frepo):
    path = _csv(tmp_path, f"{M1},approve,\n")
    frepo.record_out = {"Recorded": 0, "SecondMissing": 0, "BadRow": None, "RefusedReason": "second_review_complete"}
    assert _main(["--run", RUN, "--record-second-review", path, "--apply"], frepo) == cli.EXIT_REFUSED
    frepo.status = {"SamplePublicId": uuid.UUID(SAMPLE), "Status": "closed"}
    before = len(frepo.writes())
    assert _main(["--run", RUN, "--record-second-review", path, "--apply"], frepo) == cli.EXIT_REFUSED
    assert len(frepo.writes()) == before


def test_close_sample_dry_run_and_apply(frepo):
    assert _main(["--run", RUN, "--close-sample"], frepo) == cli.EXIT_OK and frepo.writes() == []
    assert _main(["--run", RUN, "--close-sample", "--apply"], frepo) == cli.EXIT_OK
    assert [w[0] for w in frepo.writes()] == ["close_sample"]


# -- CLI: bulk / undo -------------------------------------------------------------------------------- #


def test_bulk_dry_run_reports_gate_and_counts_without_writing(frepo, capsys):
    assert _main(["--run", RUN, "--bulk-approve"], frepo) == cli.EXIT_OK
    assert frepo.writes() == []
    out = capsys.readouterr().out
    assert "gate: passed" in out and "would_approve: 7" in out and "excluded_claimed_elsewhere: 2" in out


@pytest.mark.parametrize("code", ["sample_incomplete", "sample_has_rejection", "no_open_sample"])
def test_bulk_gate_refusal_is_exit_3_with_no_write(frepo, capsys, code):
    frepo.bulk_plan["RefusedReason"] = code
    assert _main(["--run", RUN, "--bulk-approve", "--apply"], frepo) == cli.EXIT_REFUSED
    assert frepo.writes() == [] and f"REFUSED: {code}" in capsys.readouterr().out


def test_bulk_apply_loops_batches_on_one_bulk_approval(frepo, capsys):
    frepo.bulk_batches = [
        {"BulkApprovalPublicId": uuid.UUID(BULK), "Approved": 3, "Remaining": 4, "ExcludedBillMissing": 1, "ExcludedClaimedElsewhere": 2},
        {"BulkApprovalPublicId": uuid.UUID(BULK), "Approved": 3, "Remaining": 1, "ExcludedBillMissing": 1, "ExcludedClaimedElsewhere": 2},
        {"BulkApprovalPublicId": uuid.UUID(BULK), "Approved": 1, "Remaining": 0, "ExcludedBillMissing": 1, "ExcludedClaimedElsewhere": 2},
    ]
    assert _main(["--run", RUN, "--bulk-approve", "--batch", "3", "--apply", "--as-user-id", "33"], frepo) == cli.EXIT_OK
    live = [c[1] for c in frepo.calls if c[0] == "bulk_live"]
    assert [k["bulk_approval_public_id"] for k in live] == [None, BULK, BULK]
    assert {k["batch_size"] for k in live} == {3} and {k["approved_by_user_id"] for k in live} == {33}
    out = capsys.readouterr().out
    assert "approved: 7" in out and "batches: 3" in out and f"bulk_approval: {BULK}" in out


def test_bulk_refusal_mid_way_and_ctrl_c(frepo):
    frepo.bulk_batches = [
        {"BulkApprovalPublicId": uuid.UUID(BULK), "Approved": 3, "Remaining": 4},
        {"RefusedReason": "sample_has_rejection", "Approved": 0},
    ]
    assert _main(["--run", RUN, "--bulk-approve", "--apply"], frepo) == cli.EXIT_REFUSED
    frepo.calls.clear()
    frepo.bulk_batches = [{"BulkApprovalPublicId": uuid.UUID(BULK), "Approved": 3, "Remaining": 4}]
    assert _main(["--run", RUN, "--bulk-approve", "--apply"], frepo, stop=Stop(after=1)) == cli.EXIT_INTERRUPTED
    assert len([c for c in frepo.calls if c[0] == "bulk_live"]) == 1


def test_undo_bulk_dry_run_and_apply(frepo, capsys):
    assert _main(["--undo-bulk", BULK], frepo) == cli.EXIT_OK
    assert frepo.writes() == [] and frepo.calls[-1][1]["dry_run"] is True
    frepo.undo_out = {"Reverted": 4, "KeptLinked": 1, "Completed": 0, "RefusedReason": None}
    assert _main(["--undo-bulk", BULK, "--apply"], frepo) == cli.EXIT_OK
    assert frepo.writes()[-1][1] == {"bulk_approval_public_id": BULK, "undone_by_user_id": 17, "dry_run": False}
    out = capsys.readouterr().out
    assert "reverted: 4" in out and "kept_linked: 1" in out
    frepo.undo_out = {"RefusedReason": "already_undone"}
    assert _main(["--undo-bulk", BULK, "--apply"], frepo) == cli.EXIT_REFUSED


def test_db_error_is_exit_1_with_type_name_only(frepo, capsys):
    def boom(**kw):
        raise RuntimeError("SELECT secret FROM x")

    frepo.read_sample_status = boom
    assert _main(["--run", RUN, "--status"], frepo) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "FAILED: RuntimeError." in out and "secret" not in out


def test_bulk_module_never_imports_write_paths():
    from tests.test_u535b_service import _write_path_violations

    path = Path(bulk_mod.__file__)
    assert _write_path_violations(path.read_text(), path.name) == []


# -- router: the 'sample' group --------------------------------------------------------------------- #


def test_queue_accepts_the_sample_group(client, repo):  # noqa: F811
    _as(True)
    assert client.get(f"{PREFIX}/runs/{RUN}/queue", params={"group": "sample", "review_status": "all"}).status_code == 200
    kw = repo.calls[-1][1]
    assert kw["evidence_group"] == "sample" and kw["review_status"] is None


def test_facets_pass_sample_rows_through_in_the_same_shape(client, repo):  # noqa: F811
    _as(True)
    repo.read_run_facets = lambda *, run_public_id: [
        {"EvidenceGroup": "clean", "ProjectPublicId": None, "ProjectName": None, "BillYear": 2023, "ReviewStatus": "pending", "CandidateCount": 5},
        {"EvidenceGroup": "sample", "ProjectPublicId": None, "ProjectName": None, "BillYear": 2023, "ReviewStatus": "pending", "CandidateCount": 2},
    ]
    data = client.get(f"{PREFIX}/runs/{RUN}/facets").json()["data"]
    assert data[1] == {"evidence_group": "sample", "project_public_id": None, "project_name": None, "bill_year": 2023,
                       "review_status": "pending", "count": 2}


# -- fix round 1 ----------------------------------------------------------------------------------- #


def test_b1_status_prints_only_second_review_progress_until_complete(frepo, capsys):
    frepo.status.update(SecondComplete=False, SecondMissing=2, SecondApprove=1, SecondReject=0,
                        HumanPending=None, HumanApproved=None, HumanRejected=None, Disagreements=None, Linked=None)
    assert _main(["--run", RUN, "--status"], frepo) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "second_missing: 2" in out and "members: 3" in out
    for hidden in ("human_pending", "human_approved", "human_rejected", "disagreements", "linked"):
        assert hidden not in out
    assert "hidden until the second review is complete" in out
    frepo.status.update(SecondComplete=True, SecondMissing=0, HumanPending=0, HumanApproved=3, HumanRejected=0,
                        Disagreements=0, Linked=0)
    assert _main(["--run", RUN, "--status"], frepo) == cli.EXIT_OK
    assert "human_approved: 3" in capsys.readouterr().out


def test_b1_bulk_dry_run_gate_is_generic_until_second_review_complete(frepo, capsys):
    frepo.bulk_plan.update(RefusedReason="second_review_incomplete")
    assert _main(["--run", RUN, "--bulk-approve"], frepo) == cli.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "gate: not_passed (second review incomplete)" in out
    assert "would_approve" not in out and "sample_has_rejection" not in out


def test_b7_bulk_retries_a_deadlocked_batch_once(frepo, capsys):
    class Deadlock(Exception):
        pass

    batches = [Deadlock("Transaction (Process ID 61) was deadlocked ... (1205)"),
               {"BulkApprovalPublicId": uuid.UUID(BULK), "Approved": 2, "Remaining": 0}]

    def bulk_approve(**kw):
        if kw["dry_run"]:
            return dict(frepo.bulk_plan)
        frepo.calls.append(("bulk_live", kw))
        item = batches.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    frepo.bulk_approve = bulk_approve
    assert _main(["--run", RUN, "--bulk-approve", "--apply"], frepo) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "deadlock_retried: 1" in out and "approved: 2" in out
    # A second deadlock in a row is not retried again.
    batches[:] = [Deadlock("deadlocked 1205"), Deadlock("deadlocked 1205")]
    assert _main(["--run", RUN, "--bulk-approve", "--apply"], frepo) == cli.EXIT_FAILED


def test_b8_csv_reason_limit_counts_utf16_units():
    ok = "\U0001F600" * 100      # 200 UTF-16 units, 100 code points
    too_long = "\U0001F600" * 101  # 202 units, still only 101 code points
    assert parse_second_review_csv(f"candidate_public_id,verdict,reason\n{M1},reject,{ok}\n", {M1})[0].reason == ok
    with pytest.raises(SecondReviewCsvError) as exc:
        parse_second_review_csv(f"candidate_public_id,verdict,reason\n{M1},reject,{too_long}\n", {M1})
    assert exc.value.errors == [(2, "reason_too_long")]


def test_b9_export_refuses_any_dir_under_a_git_checkout_or_build_one(tmp_path, frepo):
    checkout = tmp_path / "somerepo"
    (checkout / ".git").mkdir(parents=True)
    umbrella = tmp_path / "build.one" / "notes"
    umbrella.mkdir(parents=True)
    for target in (checkout / "deep" / "export", checkout, umbrella / "export", tmp_path / "build.one"):
        assert _main(["--run", RUN, "--export-sample", str(target)], frepo, fetch_fn=lambda f: PDF) == cli.EXIT_USAGE
        assert not (target / "manifest.json").exists()
    assert bulk_mod.export_dir_refusal(tmp_path / "fine" / "export") is None
    assert bulk_mod.export_dir_refusal(checkout / "x") == "inside_git_checkout"
    assert bulk_mod.export_dir_refusal(umbrella / "x") == "inside_build_one"


def test_b10_export_with_content_changed_exits_4_with_a_warning(tmp_path, frepo, capsys):
    frepo.members = [_member(M1, 1), _member(M2, 2, SourceSha256="0" * 64)]
    assert _main(["--run", RUN, "--export-sample", str(tmp_path / "e")], frepo, fetch_fn=lambda f: PDF) == cli.EXIT_INVALID
    assert "WARN: 1 member(s) content_changed" in capsys.readouterr().out
    manifest = json.loads((tmp_path / "e" / "manifest.json").read_text())
    assert [m["content_matches_check"] for m in manifest["members"]] == [True, False]


def test_b11_no_undo_hint_without_a_bulk_id(frepo, capsys):
    frepo.bulk_batches = [{"RefusedReason": "sample_has_rejection", "Approved": 0}]
    assert _main(["--run", RUN, "--bulk-approve", "--apply"], frepo) == cli.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "--undo-bulk" not in out and "None" not in out.split("REFUSED", 1)[1]


# -- fix round 2 ------------------------------------------------------------------------------------ #


def test_c1b_close_dry_run_warns_when_the_sample_is_incomplete(frepo, capsys):
    frepo.status.update(SecondComplete=False, SecondMissing=1)
    assert _main(["--run", RUN, "--close-sample"], frepo) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "WARN: the sample is incomplete" in out and "prior_sample_incomplete" in out and frepo.writes() == []
    frepo.status.update(SecondComplete=True, SecondMissing=0, HumanPending=0)
    assert _main(["--run", RUN, "--close-sample"], frepo) == cli.EXIT_OK
    assert "WARN" not in capsys.readouterr().out


def test_c2_status_warns_when_the_gate_no_longer_holds_under_an_active_bulk(frepo, capsys):
    frepo.status.update(SecondComplete=True, SecondMissing=0, SecondApprove=3, SecondReject=0, HumanPending=1,
                        HumanApproved=2, HumanRejected=0, Disagreements=0, Linked=0,
                        BulkApprovalPublicId=uuid.UUID(BULK), BulkApprovedCount=10)
    assert _main(["--run", RUN, "--status"], frepo) == cli.EXIT_OK
    assert f"gate no longer holds; consider --undo-bulk {BULK}" in capsys.readouterr().out
    frepo.status.update(HumanPending=0, HumanApproved=3)
    assert _main(["--run", RUN, "--status"], frepo) == cli.EXIT_OK
    assert "gate no longer holds" not in capsys.readouterr().out


def test_c4_reviewer_length_counts_utf16_units():
    assert cli.parse_args(["--run", RUN, "--status", "--reviewer", "\U0001F600" * 25]).reviewer == "\U0001F600" * 25
    with pytest.raises(SystemExit):
        cli.parse_args(["--run", RUN, "--status", "--reviewer", "\U0001F600" * 26])  # 52 units, 26 code points


# -- round 3 ---------------------------------------------------------------------------------------- #


def test_d2_status_warns_on_a_sticky_rejection_even_when_all_are_approved_now(frepo, capsys):
    frepo.status.update(SecondComplete=True, SecondMissing=0, SecondApprove=3, SecondReject=0, HumanPending=0,
                        HumanApproved=3, HumanRejected=0, Disagreements=0, Linked=0, StickyRejections=1,
                        BulkApprovalPublicId=uuid.UUID(BULK), BulkApprovedCount=10)
    assert _main(["--run", RUN, "--status"], frepo) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "sticky_rejections: 1" in out and f"gate no longer holds; consider --undo-bulk {BULK}" in out
