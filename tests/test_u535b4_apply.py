"""U-535b b4: apply / undo business flow and CLI (fakes only: no DB, no Box, no blob storage)."""
from __future__ import annotations

import ast
import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import entities.attachment_backfill.business.apply as apply_mod
from entities.attachment_backfill.business.apply import AttachmentBackfillApplyService, DbSize, db_refusal
from scripts import attachment_backfill_apply as cli

PDF = b"%PDF-1.4 synthetic bill body SECRET-DOC-TEXT-5512"
SHA = hashlib.sha256(PDF).hexdigest()
FILE_NAME = "ABC1 - Acme - SECRETNAME-77 - 1-2-2023.pdf"
RUN = "0b2c1f5e-1111-4222-8333-444455556666"
GiB = 1024 ** 3


def _row(cid=1, **kw):
    row = {
        "Id": cid, "PublicId": f"p{cid}", "RowVersion": b"\x00" * 7 + bytes([cid]), "BillId": 100 + cid,
        "SourceFileId": f"box{cid}", "SourceFileName": FILE_NAME, "SourceFileSize": len(PDF), "SourceSha256": SHA,
        "BillExists": True, "LineItemCount": 2, "BillAttachmentCount": 0,
    }
    row.update(kw)
    return row


class FakeRepo:
    def __init__(self, rows=None, sizes=None):
        self.rows = list(rows or [_row()])
        self.sizes = list(sizes or [(1 * GiB, 2 * GiB)])
        self.calls: list[tuple] = []
        self.link_result = lambda **kw: {"CandidateId": kw["candidate_id"], "AttachmentId": kw["attachment_id"],
                                         "LinkCount": 2, "AttachmentCreatedByApply": kw["attachment_created"],
                                         "RefusedReason": None}
        self.delete_result = {"Deleted": True, "BlobUrl": "https://blob/new.pdf", "ExtractedTextBlobUrl": None,
                              "RefusedReason": None}
        self.linked_rows: list[dict] = []
        self.undo_result = lambda cid: {"CandidateId": cid, "AttachmentId": 9, "AttachmentCreatedByApply": True,
                                        "LinksDeleted": 2, "RefusedReason": None}
        self.run = {"Id": 5, "PublicId": RUN, "Status": "ready"}
        self.closed = {"Id": 5, "Status": "applied"}
        self.attachments_fake = None  # FakeAttachments whose `existing` stands for the hash lookup
        self.hash_rows = None  # explicit ReadAttachmentBackfillAttachmentsByHash rows (overrides attachments_fake)
        self.hash_error = None
        self.sweep_rows: list[dict] = []
        self.register_error = None

    WRITES = ("apply_candidate_link", "delete_created_attachment", "undo_candidate_link", "close_run_if_done",
              "register_created_attachment")

    def read_attachments_by_hash(self, *, file_hash):
        self.calls.append(("read_attachments_by_hash", file_hash))
        if self.hash_error:
            raise self.hash_error
        if self.hash_rows is not None:
            return [dict(r) for r in self.hash_rows]
        fake = self.attachments_fake
        if fake is not None:
            fake.hash_reads.append(file_hash)
        existing = fake.existing if fake is not None else None
        if existing is None:
            return []
        return [{"Id": existing.id, "IsArchived": False, "Status": None, "ExpirationDate": None,
                 "ExtractionStatus": getattr(existing, "extraction_status", None), "QboId": None}]

    def register_created_attachment(self, **kw):
        self.calls.append(("register_created_attachment", kw))
        if self.register_error:
            raise self.register_error
        return {"Id": 1, "AttachmentId": kw["attachment_id"]}

    def read_created_attachments_to_sweep(self, *, run_id):
        self.calls.append(("read_created_attachments_to_sweep", run_id))
        return [dict(r) for r in self.sweep_rows]

    def writes(self):
        return [c for c in self.calls if c[0] in self.WRITES]

    def read_run_by_public_id(self, public_id):
        return dict(self.run) if public_id == RUN else None

    def read_database_size(self):
        used, cap = self.sizes[0] if len(self.sizes) == 1 else self.sizes.pop(0)
        self.calls.append(("read_database_size",))
        return {"UsedBytes": used, "MaxSizeBytes": cap}

    def read_candidates_for_apply(self, *, run_id, after_id, top):
        return [r for r in self.rows if r["Id"] > after_id][:top]

    def apply_candidate_link(self, **kw):
        self.calls.append(("apply_candidate_link", kw))
        result = self.link_result(**kw)
        if isinstance(result, BaseException):
            raise result
        return result

    def delete_created_attachment(self, **kw):
        self.calls.append(("delete_created_attachment", kw))
        return dict(self.delete_result)

    def read_linked_candidates(self, *, run_id, after_id, top):
        return [r for r in self.linked_rows if r["Id"] > after_id][:top]

    def undo_candidate_link(self, *, candidate_id):
        self.calls.append(("undo_candidate_link", candidate_id))
        return self.undo_result(candidate_id)

    def close_run_if_done(self, *, run_id):
        self.calls.append(("close_run_if_done", run_id))
        return self.closed


class FakeAttachments:
    def __init__(self, existing=None):
        self.existing = existing
        self.created: list[dict] = []
        self.marked: list[int] = []
        self.hash_reads: list[str] = []
        self.fail_create = False

    def read_by_hash(self, sha):  # pragma: no cover - b4 must use the repo's all-rows read instead (R5)
        raise AssertionError("AttachmentService.read_by_hash is not used by b4")

    def create(self, **kw):
        if self.fail_create:
            raise RuntimeError("db down")
        self.created.append(kw)
        return SimpleNamespace(id=9, extraction_status=None)

    def mark_pending_extraction(self, attachment_id):
        self.marked.append(attachment_id)


class FakeBlobs:
    def __init__(self):
        self.stored: list[tuple] = []
        self.removed: list[str] = []

    def store(self, blob_name, content, content_type):
        self.stored.append((blob_name, content, content_type))
        return f"https://blob/{blob_name}"

    def remove(self, blob_url):
        self.removed.append(blob_url)


class Fetch:
    def __init__(self, content=PDF, exc=None, on_call=None):
        self.content, self.exc, self.calls, self.on_call = content, exc, [], on_call

    def __call__(self, file_id):
        self.calls.append(file_id)
        if self.on_call:
            self.on_call()
        if self.exc:
            raise self.exc
        return self.content


def _svc(repo=None, attachments=None, blobs=None, fetch=None, stop=None):
    repo = repo or FakeRepo()
    attachments = attachments or FakeAttachments()
    repo.attachments_fake = attachments
    return AttachmentBackfillApplyService(
        repo, attachments=attachments, blobs=blobs or FakeBlobs(), fetch_bytes=fetch or Fetch(), stop_requested=stop,
    )


# -- dry run ------------------------------------------------------------------------------------------ #


def test_dry_run_downloads_nothing_and_writes_nothing(capsys):
    repo = FakeRepo(rows=[_row(1), _row(2, BillAttachmentCount=1), _row(3, BillExists=False), _row(4, SourceSha256=None)])
    att = FakeAttachments()
    made = []
    code = cli.main(
        ["--run", RUN], service=AttachmentBackfillApplyService(repo, attachments=att),
        make_blobs=lambda: made.append("blobs"), make_fetch=lambda: made.append("fetch"),
    )
    out = capsys.readouterr().out
    assert code == 0 and made == [] and repo.writes() == [] and att.created == [] and att.marked == []
    assert "would_apply: 1" in out and "would_refuse_bill_has_attachment: 1" in out
    assert "would_refuse_bill_missing: 1" in out and "would_refuse_no_check_hash: 1" in out
    assert "would_create_attachment: 1" in out and "would_link_rows: 2" in out
    assert "DRY RUN" in out and FILE_NAME not in out and "SECRETNAME" not in out


def test_dry_run_reports_reuse_from_db_and_size_note(capsys):
    repo = FakeRepo(sizes=[(19 * GiB // 10, 2 * GiB)])
    repo.attachments_fake = FakeAttachments(existing=SimpleNamespace(id=3))
    svc = AttachmentBackfillApplyService(repo, attachments=repo.attachments_fake)
    assert cli.main(["--run", RUN], service=svc) == 0
    out = capsys.readouterr().out
    assert "would_reuse_attachment: 1" in out and "would be REFUSED" in out and "95.0%" in out
    assert repo.writes() == []


def test_dry_run_undo_writes_nothing(capsys):
    repo = FakeRepo()
    repo.linked_rows = [{"Id": 1, "AttachmentId": 9, "AttachmentCreatedByApply": True, "RecordedLinkCount": 2,
                         "SourceSha256": SHA}]
    made = []
    code = cli.main(["--run", RUN, "--undo"], service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()),
                    make_blobs=lambda: made.append("blobs"))
    out = capsys.readouterr().out
    assert code == 0 and made == [] and repo.writes() == []
    assert "linked_candidates: 1" in out and "recorded_links: 2" in out and "attachments_created_by_apply: 1" in out


def test_unknown_run_and_non_ready_run(capsys):
    svc = AttachmentBackfillApplyService(FakeRepo(), attachments=FakeAttachments())
    assert cli.main(["--run", "nope"], service=svc) == 2
    repo = FakeRepo()
    repo.run["Status"] = "applied"
    assert cli.main(["--run", RUN, "--apply"], service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments())) == 2
    assert repo.writes() == []


# -- apply: refusals -------------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"BillExists": False}, "bill_missing"),
        ({"LineItemCount": 0}, "no_line_items"),
        ({"BillAttachmentCount": 3}, "bill_has_attachment"),
        ({"SourceSha256": None}, "no_check_hash"),
        ({"SourceSha256": "  "}, "no_check_hash"),
        ({"SourceFileSize": 26 * 1024 * 1024}, "too_large"),
    ],
)
def test_db_refusals_never_download_or_write(overrides, code):
    fetch, att, blobs, repo = Fetch(), FakeAttachments(), FakeBlobs(), FakeRepo(rows=[_row(**overrides)])
    counts, stop = _svc(repo, att, blobs, fetch).apply(5)
    assert counts[f"refused_{code}"] == 1 and counts["applied"] == 0 and stop is None
    assert fetch.calls == [] and blobs.stored == [] and att.created == []
    assert not [c for c in repo.calls if c[0] == "apply_candidate_link"]


def test_db_refusal_order():
    assert db_refusal(_row(BillExists=False, LineItemCount=0, BillAttachmentCount=1)) == "bill_missing"
    assert db_refusal(_row(LineItemCount=0, BillAttachmentCount=1, SourceSha256=None)) == "no_line_items"
    assert db_refusal(_row(BillAttachmentCount=1, SourceSha256=None)) == "bill_has_attachment"
    assert db_refusal(_row()) is None


@pytest.mark.parametrize(
    "fetch,code",
    [
        (Fetch(exc=RuntimeError("box 500")), "fetch_failed"),
        (Fetch(content=b"%PDF-" + b"x" * (25 * 1024 * 1024)), "too_large"),
        (Fetch(content=b"<html>not a pdf</html>"), "not_pdf"),
        (Fetch(content=b"%PDF-1.4 a different file"), "content_changed"),
    ],
)
def test_content_refusals_write_nothing(fetch, code):
    att, blobs, repo = FakeAttachments(), FakeBlobs(), FakeRepo()
    counts, _ = _svc(repo, att, blobs, fetch).apply(5)
    assert counts[f"refused_{code}"] == 1 and counts["applied"] == 0
    assert att.hash_reads == [] and blobs.stored == [] and att.created == []
    assert not [c for c in repo.calls if c[0] == "apply_candidate_link"]


def test_stored_hash_compare_is_case_insensitive():
    counts, _ = _svc(FakeRepo(rows=[_row(SourceSha256=SHA.upper())])).apply(5)
    assert counts["applied"] == 1


# -- apply: store + link ------------------------------------------------------------------------------ #


def test_creates_attachment_and_links():
    att, blobs, repo, fetch = FakeAttachments(), FakeBlobs(), FakeRepo(), Fetch()
    counts, stop = _svc(repo, att, blobs, fetch).apply(5)
    assert stop is None and fetch.calls == ["box1"]
    assert counts["applied"] == 1 and counts["created_attachment"] == 1 and counts["links_created"] == 2
    assert counts["run_closed"] == 1 and counts["marked_pending_extraction"] == 1
    (name, content, ctype), = blobs.stored
    assert content == PDF and ctype == "application/pdf" and name.endswith(".pdf")
    created = att.created[0]
    assert created["file_hash"] == SHA and created["category"] == "bill_line_item"
    assert created["blob_url"] == f"https://blob/{name}" and created["file_size"] == len(PDF)
    link = next(c for c in repo.calls if c[0] == "apply_candidate_link")[1]
    assert link == {"candidate_id": 1, "row_version": _row()["RowVersion"], "attachment_id": 9, "attachment_created": True}
    assert att.marked == [9]


def test_reuses_existing_attachment_by_hash():
    existing = SimpleNamespace(id=3, extraction_status="completed")
    att, blobs, repo = FakeAttachments(existing=existing), FakeBlobs(), FakeRepo()
    counts, _ = _svc(repo, att, blobs).apply(5)
    assert counts["applied"] == 1 and counts["reused_attachment"] == 1 and counts["created_attachment"] == 0
    assert att.hash_reads == [SHA] and blobs.stored == [] and att.created == []
    link = next(c for c in repo.calls if c[0] == "apply_candidate_link")[1]
    assert link["attachment_id"] == 3 and link["attachment_created"] is False
    assert att.marked == []  # already extracted


def test_reused_attachment_without_extraction_is_marked_pending():
    att = FakeAttachments(existing=SimpleNamespace(id=3, extraction_status=None))
    _svc(attachments=att).apply(5)
    assert att.marked == [3]


def test_mark_pending_failure_does_not_undo_the_link():
    att = FakeAttachments()
    att.mark_pending_extraction = lambda _id: (_ for _ in ()).throw(RuntimeError("x"))
    counts, _ = _svc(attachments=att).apply(5)
    assert counts["applied"] == 1 and counts["mark_pending_extraction_failed"] == 1


def test_attachment_create_failure_removes_the_blob():
    att, blobs, repo = FakeAttachments(), FakeBlobs(), FakeRepo()
    att.fail_create = True
    counts, _ = _svc(repo, att, blobs).apply(5)
    assert counts["error_attachment_create"] == 1 and counts["applied"] == 0
    assert len(blobs.stored) == 1 and blobs.removed == [f"https://blob/{blobs.stored[0][0]}"]
    assert not [c for c in repo.calls if c[0] == "apply_candidate_link"]


# -- orphan cleanup ------------------------------------------------------------------------------------ #


@pytest.mark.parametrize("refusal", ["stale", "not_approved", "bill_has_attachment", "linked"])
def test_refused_link_deletes_the_new_attachment_then_its_blob(refusal):
    att, blobs, repo = FakeAttachments(), FakeBlobs(), FakeRepo()
    repo.link_result = lambda **kw: {"CandidateId": 1, "AttachmentId": None, "LinkCount": 0, "RefusedReason": refusal}
    counts, _ = _svc(repo, att, blobs).apply(5)
    assert counts[f"refused_{refusal}"] == 1 and counts["applied"] == 0
    assert counts["orphan_attachment_deleted"] == 1
    names = [c[0] for c in repo.calls]
    assert names.index("apply_candidate_link") < names.index("delete_created_attachment")
    assert next(c for c in repo.calls if c[0] == "delete_created_attachment")[1] == {"attachment_id": 9, "file_hash": SHA}
    assert blobs.removed == ["https://blob/new.pdf"] and att.marked == []


def test_link_error_also_cleans_up_the_new_attachment():
    repo = FakeRepo()
    repo.link_result = lambda **kw: RuntimeError("deadlock")
    counts, _ = _svc(repo).apply(5)
    assert counts["error_link"] == 1 and counts["orphan_attachment_deleted"] == 1


def test_orphan_kept_when_referenced_and_its_blob_survives():
    blobs, repo = FakeBlobs(), FakeRepo()
    repo.link_result = lambda **kw: {"LinkCount": 0, "RefusedReason": "stale"}
    repo.delete_result = {"Deleted": False, "BlobUrl": None, "ExtractedTextBlobUrl": None, "RefusedReason": "referenced"}
    counts, _ = _svc(repo, blobs=blobs).apply(5)
    assert counts["orphan_attachment_kept_referenced"] == 1 and blobs.removed == []


def test_refused_link_never_deletes_a_reused_attachment():
    repo, blobs = FakeRepo(), FakeBlobs()
    repo.link_result = lambda **kw: {"LinkCount": 0, "RefusedReason": "stale"}
    _svc(repo, FakeAttachments(existing=SimpleNamespace(id=3, extraction_status=None)), blobs).apply(5)
    assert not [c for c in repo.calls if c[0] == "delete_created_attachment"] and blobs.removed == []


def test_hard_interrupt_during_link_still_cleans_up_the_new_attachment():
    repo = FakeRepo()
    repo.link_result = lambda **kw: KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        _svc(repo).apply(5)
    assert [c[0] for c in repo.calls][-1] == "delete_created_attachment"


# -- database size ----------------------------------------------------------------------------------- #


def test_db_size_math_and_fail_closed():
    assert DbSize(1 * GiB, 2 * GiB).percent == 50.0 and DbSize(1 * GiB, 2 * GiB).allows(85)
    assert not DbSize(85, 100).allows(85) and DbSize(84, 100).allows(85)  # exactly 85 % refuses
    assert not DbSize(None, 2 * GiB).allows(85) and not DbSize(1, None).allows(85)


@pytest.mark.parametrize("size", [(19 * GiB // 10, 2 * GiB), (1 * GiB, None), (1 * GiB, 0)])
def test_apply_refused_at_threshold_or_unknown_size(capsys, size):
    repo = FakeRepo(sizes=[size])
    made = []
    svc = AttachmentBackfillApplyService(repo, attachments=FakeAttachments())
    code = cli.main(["--run", RUN, "--apply"], service=svc, make_blobs=lambda: made.append("b"),
                    make_fetch=lambda: made.append("f"))
    out = capsys.readouterr().out
    assert code == 3 and made == [] and repo.writes() == []
    assert "REFUSED" in out and "database: used" in out


def test_apply_prints_used_cap_percent(capsys):
    repo = FakeRepo(sizes=[(1324 * 1024 * 1024, 2 * GiB)])
    fetch = Fetch()
    code = cli.main(["--run", RUN, "--apply"], service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()),
                    make_blobs=FakeBlobs, make_fetch=lambda: (fetch, lambda: None))
    out = capsys.readouterr().out
    assert code == 0 and "used 1,324.0 MB / cap 2,048.0 MB = 64.6% (threshold 85%)" in out
    assert "applied: 1" in out and FILE_NAME not in out and "SECRET-DOC-TEXT" not in out


def test_mid_run_size_check_stops_between_candidates():
    rows = [_row(i) for i in range(1, 6)]
    repo = FakeRepo(rows=rows, sizes=[(19 * GiB // 10, 2 * GiB)])  # the apply's own re-check, after 2 candidates
    counts, stop = _svc(repo).apply(5, size_check_every=2)
    assert stop == apply_mod.STOP_DB_SIZE and counts["processed"] == 2 and counts["applied"] == 2
    assert "run_closed" not in counts and not [c for c in repo.calls if c[0] == "close_run_if_done"]


def test_mid_run_stop_exit_code(capsys):
    rows = [_row(i) for i in range(1, 4)]
    repo = FakeRepo(rows=rows, sizes=[(1 * GiB, 2 * GiB), (1 * GiB, 2 * GiB), (19 * GiB // 10, 2 * GiB),
                                      (19 * GiB // 10, 2 * GiB)])
    fetch = Fetch()
    code = cli.main(["--run", RUN, "--apply", "--size-check-every", "1"],
                    service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()),
                    make_blobs=FakeBlobs, make_fetch=lambda: (fetch, lambda: None))
    assert code == 3 and "STOPPED" in capsys.readouterr().out


def test_limit_stops_without_closing_the_run():
    repo = FakeRepo(rows=[_row(1), _row(2)])
    counts, stop = _svc(repo).apply(5, limit=1)
    assert stop == apply_mod.STOP_LIMIT and counts["applied"] == 1
    assert not [c for c in repo.calls if c[0] == "close_run_if_done"]


def test_run_not_closed_reported():
    repo = FakeRepo()
    repo.closed = None
    counts, _ = _svc(repo).apply(5)
    assert counts["run_closed"] == 0


# -- Ctrl-C ---------------------------------------------------------------------------------------- #


def test_ctrl_c_finishes_the_current_candidate_then_exits_130(capsys):
    stop = cli.StopFlag()
    repo = FakeRepo(rows=[_row(1), _row(2), _row(3)])
    fetch = Fetch(on_call=lambda: stop.handle(2, None) if not stop.requested else None)
    code = cli.main(["--run", RUN, "--apply"], service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()),
                    stop=stop, make_blobs=FakeBlobs, make_fetch=lambda: (fetch, lambda: None))
    out = capsys.readouterr().out
    assert code == 130 and fetch.calls == ["box1"] and "applied: 1" in out and "Interrupted" in out
    assert not [c for c in repo.calls if c[0] == "close_run_if_done"]


def test_second_ctrl_c_aborts_with_130(capsys):
    stop = cli.StopFlag()
    stop.handle(2, None)
    with pytest.raises(KeyboardInterrupt):
        stop.handle(2, None)
    repo = FakeRepo()
    repo.link_result = lambda **kw: KeyboardInterrupt()
    code = cli.main(["--run", RUN, "--apply"], service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()),
                    make_blobs=FakeBlobs, make_fetch=lambda: (Fetch(), lambda: None))
    assert code == 130 and [c[0] for c in repo.calls][-1] == "delete_created_attachment"


def test_sigint_handler_restored():
    import signal

    before = signal.getsignal(signal.SIGINT)
    cli.main(["--run", RUN], service=AttachmentBackfillApplyService(FakeRepo(), attachments=FakeAttachments()))
    assert signal.getsignal(signal.SIGINT) is before


# -- undo -------------------------------------------------------------------------------------------- #


def test_undo_unlinks_then_sweeps_the_runs_created_attachments():
    repo, blobs = FakeRepo(), FakeBlobs()
    repo.linked_rows = [
        {"Id": 1, "AttachmentId": 9, "AttachmentCreatedByApply": True, "RecordedLinkCount": 2, "SourceSha256": SHA},
        {"Id": 2, "AttachmentId": 3, "AttachmentCreatedByApply": False, "RecordedLinkCount": 1, "SourceSha256": SHA},
    ]
    results = {
        1: {"CandidateId": 1, "AttachmentId": 9, "AttachmentCreatedByApply": True, "LinksDeleted": 2, "RefusedReason": None},
        2: {"CandidateId": 2, "AttachmentId": 3, "AttachmentCreatedByApply": False, "LinksDeleted": 1, "RefusedReason": None},
    }
    repo.undo_result = results.__getitem__
    repo.sweep_rows = [{"AttachmentId": 9, "FileHash": SHA}]  # the registry: only what the apply created
    counts, stop = _svc(repo, blobs=blobs).undo(5)
    assert stop is None and counts["undone"] == 2 and counts["links_deleted"] == 3
    assert counts["swept_deleted"] == 1
    names = [c[0] for c in repo.calls]
    assert names.index("read_created_attachments_to_sweep") > max(i for i, n in enumerate(names) if n == "undo_candidate_link")
    deletes = [c[1] for c in repo.calls if c[0] == "delete_created_attachment"]
    assert deletes == [{"attachment_id": 9, "file_hash": SHA}] and blobs.removed == ["https://blob/new.pdf"]


def test_undo_refusal_and_cli_apply(capsys):
    repo = FakeRepo()
    repo.linked_rows = [{"Id": 1, "AttachmentId": 9, "AttachmentCreatedByApply": True, "RecordedLinkCount": 2,
                         "SourceSha256": SHA}]
    repo.undo_result = lambda cid: {"RefusedReason": "bill_completed_since_apply"}
    blobs = FakeBlobs()
    code = cli.main(["--run", RUN, "--undo", "--apply"],
                    service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()), make_blobs=lambda: blobs)
    assert code == 0 and "refused_bill_completed_since_apply: 1" in capsys.readouterr().out
    assert blobs.removed == []


def test_undo_sweep_kept_when_referenced():
    repo, blobs = FakeRepo(), FakeBlobs()
    repo.sweep_rows = [{"AttachmentId": 9, "FileHash": SHA}]
    repo.delete_result = {"Deleted": False, "RefusedReason": "referenced"}
    counts, _ = _svc(repo, blobs=blobs).undo(5)
    assert counts["swept_kept_referenced"] == 1 and blobs.removed == []


# -- no outbound side effects ------------------------------------------------------------------------ #


def test_apply_and_undo_make_no_outbound_writes(monkeypatch):
    from integrations.box.base.client import BoxHttpClient
    from integrations.box.outbox.business.service import BoxOutboxService
    from integrations.intuit.qbo.outbox.business.service import QboOutboxService
    from integrations.ms.outbox.business.service import MsOutboxService

    hits = []

    def boom(name):
        def _f(*a, **k):
            hits.append(name)
            raise AssertionError(f"outbound write: {name}")
        return _f

    for cls in (BoxOutboxService, QboOutboxService, MsOutboxService):
        for attr in dir(cls):
            if attr.startswith("enqueue"):
                monkeypatch.setattr(cls, attr, boom(f"{cls.__name__}.{attr}"))
    for attr in ("upload_file", "upload_file_version", "create_folder", "post", "put", "delete"):
        if hasattr(BoxHttpClient, attr):
            monkeypatch.setattr(BoxHttpClient, attr, boom(f"BoxHttpClient.{attr}"))
    import shared.database as database

    monkeypatch.setattr(database, "get_connection", boom("get_connection"))

    repo = FakeRepo(rows=[_row(1), _row(2)])
    repo.linked_rows = [{"Id": 1, "AttachmentId": 9, "AttachmentCreatedByApply": True, "RecordedLinkCount": 2,
                         "SourceSha256": SHA}]
    svc = _svc(repo)
    counts, _ = svc.apply(5)
    assert counts["applied"] == 2
    svc.undo(5)
    assert hits == []


def test_apply_module_imports_no_outbound_writer():
    for path in (Path(apply_mod.__file__), Path(cli.__file__)):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        for mod in modules:
            assert not any(word in mod for word in ("outbox", "sharepoint", "intuit", "graph", "excel", "mail")), (path, mod)


# -- b2 undo audit: the repo threads the actor ----------------------------------------------------------- #


def test_review_undo_passes_the_request_user_as_undone_by(monkeypatch):
    import entities.attachment_backfill.persistence.repo as repo_mod
    from shared.authz import current_user_id

    sent = []

    class Cursor:
        description = [("Id",), ("RefusedReason",)]

        def fetchone(self):
            return (1, None)

    class Conn:
        def cursor(self):
            return Cursor()

    @contextmanager
    def fake_conn():
        yield Conn()

    monkeypatch.setattr(repo_mod, "get_connection", fake_conn)
    monkeypatch.setattr(repo_mod, "call_procedure", lambda cursor, name, params: sent.append((name, params)))
    token = current_user_id.set(33)
    try:
        repo_mod.AttachmentBackfillRepository().undo_candidate_review(public_id="p", row_version=b"\x00" * 8)
        repo_mod.AttachmentBackfillRepository().undo_candidate_review(
            public_id="p", row_version=b"\x00" * 8, undone_by_user_id=17
        )
    finally:
        current_user_id.reset(token)
    assert sent[0] == ("UndoAttachmentBackfillCandidateReview",
                       {"PublicId": "p", "RowVersion": b"\x00" * 8, "UndoneByUserId": 33})
    assert sent[1][1]["UndoneByUserId"] == 17


# -- fix round 1 (EM) ------------------------------------------------------------------------------------ #


def test_r3_created_attachment_is_registered_right_after_create():
    repo = FakeRepo()
    counts, _ = _svc(repo).apply(5)
    names = [c[0] for c in repo.calls]
    assert names.index("register_created_attachment") < names.index("apply_candidate_link")
    reg = next(c[1] for c in repo.calls if c[0] == "register_created_attachment")
    assert reg["run_id"] == 5 and reg["candidate_id"] == 1 and reg["attachment_id"] == 9 and reg["file_hash"] == SHA
    assert reg["blob_url"].startswith("https://blob/") and counts["applied"] == 1


def test_r3_reused_attachment_is_not_registered():
    repo = FakeRepo()
    _svc(repo, FakeAttachments(existing=SimpleNamespace(id=3, extraction_status="completed"))).apply(5)
    assert not [c for c in repo.calls if c[0] == "register_created_attachment"]


def test_r3_registry_failure_skips_the_link():
    repo = FakeRepo()
    repo.register_error = RuntimeError("db")
    counts, _ = _svc(repo).apply(5)
    assert counts["error_register"] == 1 and counts["applied"] == 0
    assert not [c for c in repo.calls if c[0] == "apply_candidate_link"]


def test_r3_interrupted_undo_does_not_sweep():
    repo = FakeRepo()
    repo.linked_rows = [{"Id": 1, "AttachmentId": 9, "AttachmentCreatedByApply": True, "RecordedLinkCount": 2,
                         "SourceSha256": SHA}]
    repo.sweep_rows = [{"AttachmentId": 9, "FileHash": SHA}]
    counts, stop = _svc(repo, stop=lambda: True).undo(5)
    assert stop == apply_mod.STOP_INTERRUPTED
    assert not [c for c in repo.calls if c[0] == "read_created_attachments_to_sweep"]


def test_r4_ctrl_c_during_create_leaves_the_blob():
    att, blobs = FakeAttachments(), FakeBlobs()

    def interrupted(**kw):
        raise KeyboardInterrupt

    att.create = interrupted
    with pytest.raises(KeyboardInterrupt):
        _svc(attachments=att, blobs=blobs).apply(5)
    assert len(blobs.stored) == 1 and blobs.removed == []


def _hash_row(aid, **kw):
    row = {"Id": aid, "IsArchived": False, "Status": None, "ExpirationDate": None, "ExtractionStatus": "completed",
           "QboId": None}
    row.update(kw)
    return row


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([_hash_row(7, IsArchived=True)], None),
        ([_hash_row(7, Status="archived")], None),
        ([_hash_row(7, ExpirationDate="2001-01-01 00:00:00")], None),
        ([_hash_row(8), _hash_row(7, IsArchived=True), _hash_row(6)], 6),
        ([_hash_row(9, Status="active"), _hash_row(4, ExpirationDate="2999-01-01 00:00:00")], 4),
    ],
)
def test_r5_reuse_skips_archived_inactive_expired_and_prefers_lowest_id(rows, expected):
    repo, att = FakeRepo(), FakeAttachments()
    repo.hash_rows = rows
    counts, _ = _svc(repo, att).apply(5)
    link = next(c[1] for c in repo.calls if c[0] == "apply_candidate_link")
    if expected is None:
        assert counts["created_attachment"] == 1 and link["attachment_id"] == 9 and len(att.created) == 1
    else:
        assert counts["reused_attachment"] == 1 and link["attachment_id"] == expected and att.created == []


def test_r5_dry_run_uses_the_same_reuse_filter(capsys):
    repo = FakeRepo()
    repo.hash_rows = [_hash_row(7, IsArchived=True)]
    assert cli.main(["--run", RUN], service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments())) == 0
    assert "would_create_attachment: 1" in capsys.readouterr().out


def test_r6_hash_read_error_counts_and_continues():
    repo = FakeRepo(rows=[_row(1), _row(2)])
    repo.hash_error = RuntimeError("timeout")
    counts, stop = _svc(repo).apply(5)
    assert counts["error_read"] == 2 and counts["processed"] == 2 and stop is None


def test_r6_counts_printed_when_the_db_fails_mid_run(capsys):
    repo = FakeRepo(rows=[_row(1), _row(2)])
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("db gone")  # not a per-candidate error: escapes the loop
        return [r for r in repo.rows if r["Id"] > kw["after_id"]][:1]

    repo.read_candidates_for_apply = flaky
    code = cli.main(["--run", RUN, "--apply"], service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()),
                    make_blobs=FakeBlobs, make_fetch=lambda: (Fetch(), lambda: None))
    out = capsys.readouterr().out
    assert code == 1 and "applied: 1" in out and "ConnectionError" in out and "db gone" not in out


def test_r6_counts_printed_on_second_ctrl_c(capsys):
    repo = FakeRepo(rows=[_row(1), _row(2)])
    seen = {"n": 0}

    def link(**kw):
        seen["n"] += 1
        if seen["n"] == 2:
            return KeyboardInterrupt()
        return {"LinkCount": 2, "RefusedReason": None}

    repo.link_result = link
    code = cli.main(["--run", RUN, "--apply"], service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()),
                    make_blobs=FakeBlobs, make_fetch=lambda: (Fetch(), lambda: None))
    out = capsys.readouterr().out
    assert code == 130 and "applied: 1" in out


@pytest.mark.parametrize("argv", [["--run", RUN], ["--run", RUN, "--undo"]])
def test_r6_dry_runs_honour_ctrl_c(capsys, argv):
    stop = cli.StopFlag()
    stop.requested = True
    repo = FakeRepo(rows=[_row(1), _row(2)])
    repo.linked_rows = [{"Id": 1, "AttachmentId": 9, "AttachmentCreatedByApply": True, "RecordedLinkCount": 2,
                         "SourceSha256": SHA}]
    code = cli.main(argv, service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()), stop=stop)
    assert code == 130 and repo.writes() == []


# -- fix round 2 (EM) ------------------------------------------------------------------------------------ #


def test_s3_close_error_is_counted_not_raised():
    repo = FakeRepo()

    def boom(**kw):
        raise RuntimeError("deadlock")

    repo.close_run_if_done = boom
    counts, stop = _svc(repo).apply(5)
    assert stop is None and counts["applied"] == 1 and counts["close_error"] == 1


def test_p1_register_is_retried_once():
    repo = FakeRepo()
    real = repo.register_created_attachment
    tries = {"n": 0}

    def flaky(**kw):
        tries["n"] += 1
        if tries["n"] == 1:
            raise RuntimeError("blip")
        return real(**kw)

    repo.register_created_attachment = flaky
    counts, _ = _svc(repo).apply(5)
    assert tries["n"] == 2 and counts["applied"] == 1 and counts.get("error_register", 0) == 0


def test_p1_register_failure_logs_attachment_id_and_blob_name(caplog):
    repo, blobs = FakeRepo(), FakeBlobs()
    repo.register_error = RuntimeError("down")
    with caplog.at_level("WARNING"):
        counts, _ = _svc(repo, blobs=blobs).apply(5)
    blob_name = blobs.stored[0][0]
    assert counts["error_register"] == 1
    assert any("attachment_id=9" in r.getMessage() and blob_name in r.getMessage() for r in caplog.records)


def test_p1_ctrl_c_between_create_and_register_logs_both(caplog):
    repo, blobs = FakeRepo(), FakeBlobs()
    repo.register_error = KeyboardInterrupt()
    with caplog.at_level("WARNING"), pytest.raises(KeyboardInterrupt):
        _svc(repo, blobs=blobs).apply(5)
    blob_name = blobs.stored[0][0]
    assert any("attachment_id=9" in r.getMessage() and blob_name in r.getMessage() for r in caplog.records)
    assert blobs.removed == []


def test_p2_docstring_says_the_sweep_is_per_run():
    doc = apply_mod.__doc__
    assert "per run" in doc and "re-run" in doc


def test_p3_dry_run_counts_would_apply_only_after_the_hash_read():
    repo = FakeRepo(rows=[_row(1)])
    repo.hash_error = RuntimeError("timeout")
    counts, _ = AttachmentBackfillApplyService(repo, attachments=FakeAttachments()).plan(5)
    assert counts["error_read"] == 1 and counts["would_apply"] == 0 and counts["would_link_rows"] == 0


@pytest.mark.parametrize(
    "setup",
    [
        lambda repo: setattr(repo, "hash_error", RuntimeError("x")),                     # error_read
        lambda repo: setattr(repo, "close_run_if_done", lambda **kw: (_ for _ in ()).throw(RuntimeError())),  # close_error
        lambda repo: (setattr(repo, "link_result", lambda **kw: {"LinkCount": 0, "RefusedReason": "stale"}),
                      setattr(repo, "delete_created_attachment", lambda **kw: (_ for _ in ()).throw(RuntimeError()))),
    ],
)
def test_p4_exit_4_when_any_error_count(capsys, setup):
    repo = FakeRepo()
    setup(repo)
    code = cli.main(["--run", RUN, "--apply"], service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()),
                    make_blobs=FakeBlobs, make_fetch=lambda: (Fetch(), lambda: None))
    out = capsys.readouterr().out
    assert code == 4 and "applied:" in out and "ERRORS" in out


def test_p4_blob_remove_failure_exits_4(capsys):
    class FailingBlobs(FakeBlobs):
        def remove(self, url):
            raise RuntimeError("blob")

    repo = FakeRepo()
    repo.sweep_rows = [{"AttachmentId": 9, "FileHash": SHA}]
    code = cli.main(["--run", RUN, "--undo", "--apply"],
                    service=AttachmentBackfillApplyService(repo, attachments=FakeAttachments()), make_blobs=FailingBlobs)
    assert code == 4 and "blob_remove_failed: 1" in capsys.readouterr().out


def test_p4_clean_apply_still_exits_0():
    code = cli.main(["--run", RUN, "--apply"],
                    service=AttachmentBackfillApplyService(FakeRepo(), attachments=FakeAttachments()),
                    make_blobs=FakeBlobs, make_fetch=lambda: (Fetch(), lambda: None))
    assert code == 0
