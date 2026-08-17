"""
MS outbox Policy-C coalescing must discriminate per ATTACHMENT.

Regression cover for a live document-loss defect: `MsOutboxService.enqueue()`
coalesced `upload_sharepoint_file` on (EntityType, EntityPublicId, Kind) only,
so two attachments enqueued for the SAME bill inside one debounce window
collapsed into ONE outbox row — the first payload won and the second document
was never uploaded (no dead-letter, no ReconciliationIssue, no failure log).

The fix mirrors `integrations/box/outbox/business/service.py::_find_coalescible`:
coalesce only the pending row whose payload `attachment_id` matches, skip rows
whose payload is NULL/unparsable (they must never behave as a wildcard), and
refresh the payload on a coalesce hit so "latest target wins".

These tests drive the REAL `enqueue()` against an in-memory repo, so surviving
rows are actual rows, not mock call assertions.
"""

import json
from unittest.mock import patch

import pytest

from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.service import (
    KIND_UPLOAD_SHAREPOINT_FILE,
    MsOutboxService,
)

_ENTITY_TYPE = "Bill"
_ENTITY_PUBLIC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_DRIVE_ID = "drive-1"
_PARENT_ITEM_ID = "parent-1"


@pytest.fixture(autouse=True)
def _clear_disable_fanout_guards_env(monkeypatch):
    monkeypatch.delenv("DISABLE_FANOUT_IDEMPOTENCY_GUARDS", raising=False)


class _FakeMsOutboxRepo:
    """
    In-memory stand-in for `MsOutboxRepository` — just the surface `enqueue()` /
    `enqueue_sharepoint_upload()` touch. Row lookup mirrors the sprocs:
    pending/failed, newest first (ORDER BY Id DESC), ROWVERSION-guarded writes.
    """

    def __init__(self):
        self.rows = []
        self._next_id = 1
        self.ready_after_calls = []
        self.payload_update_calls = []

    # --- sproc-shaped reads ---

    def _matching(self, entity_type, entity_public_id, kind):
        return [
            row
            for row in reversed(self.rows)
            if row.entity_type == entity_type
            and row.entity_public_id == entity_public_id
            and row.kind == kind
        ]

    def read_pending_by_entity(self, entity_type, entity_public_id, kind):
        return [
            row
            for row in self._matching(entity_type, entity_public_id, kind)
            if row.status in ("pending", "failed")
        ]

    def read_completed_by_entity(self, entity_type, entity_public_id, kind):
        return [
            row
            for row in self._matching(entity_type, entity_public_id, kind)
            if row.status == "done"
        ]

    # --- writes ---

    def create(
        self,
        *,
        kind,
        entity_type,
        entity_public_id,
        tenant_id,
        request_id,
        payload=None,
        ready_after=None,
        correlation_id=None,
    ):
        row = MsOutbox(
            id=self._next_id,
            public_id=f"outbox-{self._next_id}",
            row_version=f"rv-{self._next_id}-0",
            kind=kind,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            tenant_id=tenant_id,
            request_id=request_id,
            payload=payload,
            status="pending",
            attempts=0,
            ready_after=ready_after,
            correlation_id=correlation_id,
        )
        self._next_id += 1
        self.rows.append(row)
        return row

    def _claim(self, id, row_version):
        """ROWVERSION guard: a stale row_version writes nothing (returns None)."""
        for row in self.rows:
            if row.id == id and row.row_version == row_version:
                row.row_version = f"rv-{row.id}-{self._next_id}"
                self._next_id += 1
                return row
        return None

    def update_payload(self, *, id, row_version, payload):
        self.payload_update_calls.append((id, payload))
        row = self._claim(id, row_version)
        if row is None:
            return None
        row.payload = payload
        return row

    def update_ready_after(self, *, id, row_version, ready_after):
        self.ready_after_calls.append((id, ready_after))
        row = self._claim(id, row_version)
        if row is None:
            return None
        row.ready_after = ready_after
        return row


@pytest.fixture
def svc_repo():
    repo = _FakeMsOutboxRepo()
    svc = MsOutboxService(repo=repo)
    with patch(
        "integrations.ms.outbox.business.service._resolve_tenant_id",
        return_value="tenant-abc",
    ), patch(
        "integrations.ms.outbox.business.service._writes_allowed",
        return_value=True,
    ):
        yield svc, repo


def _upload(svc, *, attachment_id, filename="invoice.pdf", blob_path=None, parent_item_id=_PARENT_ITEM_ID):
    return svc.enqueue_sharepoint_upload(
        entity_type=_ENTITY_TYPE,
        entity_public_id=_ENTITY_PUBLIC_ID,
        drive_id=_DRIVE_ID,
        parent_item_id=parent_item_id,
        filename=filename,
        content_type="application/pdf",
        blob_path=blob_path or f"attachments/{filename}",
        attachment_id=attachment_id,
    )


def _seed_pending(repo, payload, *, status="pending"):
    """Put a pending upload row in the queue with an arbitrary raw payload."""
    row = repo.create(
        kind=KIND_UPLOAD_SHAREPOINT_FILE,
        entity_type=_ENTITY_TYPE,
        entity_public_id=_ENTITY_PUBLIC_ID,
        tenant_id="tenant-abc",
        request_id="req-seed",
        payload=payload,
    )
    row.status = status
    return row


def _payloads(repo):
    return [json.loads(r.payload) for r in repo.rows if r.payload]


# --- THE DEFECT: two attachments, one entity, one debounce window ---


def test_two_distinct_attachments_same_entity_both_survive(svc_repo):
    """The regression under test — neither document may be dropped."""
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42, filename="a.pdf", blob_path="attachments/a.pdf")
    second = _upload(svc, attachment_id=43, filename="b.pdf", blob_path="attachments/b.pdf")

    assert first is not None and second is not None
    assert first.id != second.id, "second attachment collapsed into the first row"
    assert len(repo.rows) == 2

    assert sorted(p["attachment_id"] for p in _payloads(repo)) == [42, 43]
    # The blob is the document. Both must still be reachable at drain time.
    assert {p["blob_path"] for p in _payloads(repo)} == {"attachments/a.pdf", "attachments/b.pdf"}
    assert {p["filename"] for p in _payloads(repo)} == {"a.pdf", "b.pdf"}


def test_three_distinct_attachments_all_survive(svc_repo):
    svc, repo = svc_repo

    for n in (1, 2, 3):
        _upload(svc, attachment_id=n, filename=f"{n}.pdf", blob_path=f"attachments/{n}.pdf")

    assert len(repo.rows) == 3
    assert sorted(p["attachment_id"] for p in _payloads(repo)) == [1, 2, 3]


def test_interleaved_attachments_coalesce_to_own_rows(svc_repo):
    """A→B→A→B stampede collapses to exactly two rows, one per attachment."""
    svc, repo = svc_repo

    a1 = _upload(svc, attachment_id=42, filename="a.pdf", blob_path="attachments/a.pdf")
    b1 = _upload(svc, attachment_id=43, filename="b.pdf", blob_path="attachments/b.pdf")
    a2 = _upload(svc, attachment_id=42, filename="a.pdf", blob_path="attachments/a.pdf")
    b2 = _upload(svc, attachment_id=43, filename="b.pdf", blob_path="attachments/b.pdf")

    assert len(repo.rows) == 2
    assert a2.id == a1.id
    assert b2.id == b1.id
    assert sorted(p["attachment_id"] for p in _payloads(repo)) == [42, 43]


# --- the debounce must NOT regress ---


def test_same_attachment_twice_still_coalesces(svc_repo):
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42)
    second = _upload(svc, attachment_id=42)

    assert len(repo.rows) == 1, "debounce regressed — duplicate row for one attachment"
    assert second.id == first.id
    assert repo.ready_after_calls, "ReadyAfter was not extended on the coalesce hit"
    assert repo.ready_after_calls[-1][0] == first.id


def test_same_attachment_two_destinations_both_survive(svc_repo):
    """The production fan-out case: ONE attachment, TWO SharePoint targets.

    `ExpenseService.complete()` enqueues the same attachment to the project module
    folder AND to the general receipts folder back-to-back, inside the debounce
    window. `complete_bill` does the same per project folder on a multi-project
    bill. These are DIFFERENT physical uploads that merely share an attachment_id,
    so coalescing them destroys one destination's copy.

    Keying the coalesce on attachment_id alone collapses these two into one row —
    and because the coalesce refreshes the payload ("latest target wins"), the
    FIRST destination is the one destroyed. That is a document loss, not a saving.
    """
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42, parent_item_id="project-module-folder")
    second = _upload(svc, attachment_id=42, parent_item_id="general-receipts-folder")

    assert second.id != first.id, (
        "same attachment to two DIFFERENT SharePoint targets must not coalesce"
    )
    assert len(repo.rows) == 2
    parents = sorted(json.loads(r.payload)["parent_item_id"] for r in repo.rows)
    assert parents == ["general-receipts-folder", "project-module-folder"], (
        "both destinations must survive with their own target intact"
    )


def test_same_attachment_two_drives_both_survive(svc_repo):
    """Same fan-out, discriminated by drive_id rather than parent_item_id."""
    svc, repo = svc_repo

    first = svc.enqueue_sharepoint_upload(
        entity_type=_ENTITY_TYPE, entity_public_id=_ENTITY_PUBLIC_ID,
        drive_id="drive-project", parent_item_id=_PARENT_ITEM_ID,
        filename="invoice.pdf", content_type="application/pdf",
        blob_path="attachments/invoice.pdf", attachment_id=42,
    )
    second = svc.enqueue_sharepoint_upload(
        entity_type=_ENTITY_TYPE, entity_public_id=_ENTITY_PUBLIC_ID,
        drive_id="drive-shared-documents", parent_item_id=_PARENT_ITEM_ID,
        filename="invoice.pdf", content_type="application/pdf",
        blob_path="attachments/invoice.pdf", attachment_id=42,
    )

    assert second.id != first.id
    assert len(repo.rows) == 2


def test_coalesce_extends_ready_after_forward(svc_repo):
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42)
    original_ready_after = first.ready_after
    _upload(svc, attachment_id=42)

    # `>=` would pass with debounce extension completely dead (ready_after simply
    # unchanged), so assert it STRICTLY moved forward, and that the extension was
    # actually issued against this row.
    assert len(repo.rows) == 1, "expected a coalesce, not a second row"
    assert repo.rows[0].ready_after > original_ready_after
    assert repo.ready_after_calls, "ReadyAfter was never extended"
    assert repo.ready_after_calls[-1][0] == first.id


def test_coalesce_preserves_request_id(svc_repo):
    """RequestId is the Graph dedup key — a coalesce must not mint a new one."""
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42)
    request_id = first.request_id
    second = _upload(svc, attachment_id=42)

    # Reading repo.rows[0] alone is vacuous: with coalescing disabled entirely a
    # SECOND row is appended and rows[0] still holds the original request_id, so
    # the assertion passes while the behaviour under test is dead. Pin that a
    # coalesce actually happened first.
    assert len(repo.rows) == 1, "expected a coalesce, not a second row"
    assert second.id == first.id
    assert repo.rows[0].request_id == request_id


# --- payload refresh on a coalesce hit ---


def test_target_change_does_not_coalesce_and_never_overwrites(svc_repo):
    """A DIFFERENT upload target is a different physical upload — never a merge.

    An earlier iteration of this fix coalesced on attachment_id alone and applied
    "latest target wins", on the theory that a re-enqueue with a new target is a
    CORRECTION. It cannot be: a correction and a legitimate second destination are
    byte-identical on the wire (same entity, kind and attachment_id, different
    drive/parent), and MS genuinely fans one attachment to several destinations.
    Coalescing therefore destroys the first destination's copy. The asymmetry
    decides it — a redundant PUT is recoverable, a lost document is not.
    """
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42, parent_item_id="parent-A")
    second = _upload(svc, attachment_id=42, parent_item_id="parent-B")

    assert second.id != first.id, "different targets must not coalesce"
    assert len(repo.rows) == 2
    assert sorted(p["parent_item_id"] for p in _payloads(repo)) == ["parent-A", "parent-B"]
    assert not repo.payload_update_calls, (
        "no payload may be rewritten — that is how the first destination got destroyed"
    )

def test_coalesce_preserves_upload_session_when_target_unchanged(svc_repo):
    """A half-uploaded large file must not restart from byte 0."""
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42)
    payload = json.loads(first.payload)
    payload.update(
        {
            "upload_session_url": "https://graph/session/abc",
            "completed_bytes": 5242880,
            "total_bytes": 10485760,
        }
    )
    first.payload = json.dumps(payload)
    first.status = "failed"

    _upload(svc, attachment_id=42)

    refreshed = _payloads(repo)[0]
    assert refreshed["upload_session_url"] == "https://graph/session/abc"
    assert refreshed["completed_bytes"] == 5242880
    assert refreshed["total_bytes"] == 10485760


def test_target_change_leaves_the_original_upload_session_intact(svc_repo):
    """An uploadUrl is bound to (drive, parent, filename), so it must never be
    resumed against a different target.

    Under attachment-only coalescing this needed an explicit "drop the session on
    target change" rule. With the destination in the coalesce key the situation is
    unreachable by construction: a different target gets its OWN row, so the
    in-flight session on the original row is untouched rather than rewritten.
    """
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42, parent_item_id="parent-A")
    payload = json.loads(first.payload)
    payload.update({"upload_session_url": "https://graph/session/abc", "completed_bytes": 5242880})
    first.payload = json.dumps(payload)
    first.status = "failed"

    _upload(svc, attachment_id=42, parent_item_id="parent-B")

    assert len(repo.rows) == 2
    original = json.loads(repo.rows[0].payload)
    assert original["parent_item_id"] == "parent-A"
    assert original["upload_session_url"] == "https://graph/session/abc", (
        "the in-flight session on the untouched row must survive"
    )
    assert original["completed_bytes"] == 5242880

def test_coalesce_no_payload_write_when_nothing_changed(svc_repo):
    svc, repo = svc_repo

    _upload(svc, attachment_id=42)
    _upload(svc, attachment_id=42)

    assert repo.payload_update_calls == []


def test_coalesce_payload_refresh_failure_is_logged(svc_repo, caplog):
    """ROWVERSION lost to a concurrent claim must never be silent.

    Exercised on a SAME-target coalesce (the only kind that exists now), with a
    non-target payload field changing so a refresh is actually attempted.
    """
    svc, repo = svc_repo

    def _enq(content_type):
        return svc.enqueue_sharepoint_upload(
            entity_type=_ENTITY_TYPE, entity_public_id=_ENTITY_PUBLIC_ID,
            drive_id=_DRIVE_ID, parent_item_id=_PARENT_ITEM_ID,
            filename="invoice.pdf", content_type=content_type,
            blob_path="attachments/invoice.pdf", attachment_id=42,
        )

    first = _enq("application/octet-stream")

    # Identical TARGET (so it coalesces) but a changed non-target field, so a
    # payload refresh is genuinely attempted. A worker claiming the row between
    # our read and our write advances its ROWVERSION, so UpdateMsOutboxPayload
    # matches nothing and returns None.
    with patch.object(repo, "update_payload", return_value=None), caplog.at_level("WARNING"):
        result = _enq("application/pdf")

    assert result.id == first.id
    assert len(repo.rows) == 1
    record = next(
        (r for r in caplog.records if r.getMessage() == "ms.outbox.coalesce_payload_refresh_failed"),
        None,
    )
    assert record is not None
    assert record.outbox_public_id == first.public_id
    assert record.attachment_id == 42

def test_null_payload_candidate_never_coalesces(svc_repo):
    svc, repo = svc_repo

    seeded = _seed_pending(repo, None)

    result = _upload(svc, attachment_id=42)

    assert result.id != seeded.id
    assert len(repo.rows) == 2
    assert seeded.payload is None, "the NULL-payload row was overwritten"


def test_empty_string_payload_candidate_never_coalesces(svc_repo):
    svc, repo = svc_repo

    seeded = _seed_pending(repo, "")

    result = _upload(svc, attachment_id=42)

    assert result.id != seeded.id
    assert len(repo.rows) == 2


def test_unparsable_payload_candidate_never_coalesces(svc_repo):
    svc, repo = svc_repo

    seeded = _seed_pending(repo, "{not-json")

    result = _upload(svc, attachment_id=42)

    assert result.id != seeded.id
    assert len(repo.rows) == 2
    assert seeded.payload == "{not-json"


def test_non_dict_payload_candidate_never_coalesces(svc_repo):
    svc, repo = svc_repo

    seeded = _seed_pending(repo, json.dumps(["not", "a", "dict"]))

    result = _upload(svc, attachment_id=42)

    assert result.id != seeded.id
    assert len(repo.rows) == 2


def test_non_numeric_attachment_id_candidate_never_coalesces(svc_repo):
    """`same_attachment_id` raises on non-numeric input — that must fall
    through to a NEW row, not to a match."""
    svc, repo = svc_repo

    seeded = _seed_pending(
        repo,
        json.dumps(
            {
                "drive_id": _DRIVE_ID,
                "parent_item_id": _PARENT_ITEM_ID,
                "filename": "invoice.pdf",
                "blob_path": "attachments/invoice.pdf",
                "attachment_id": "not-an-int",
            }
        ),
    )

    result = _upload(svc, attachment_id=42)

    assert result.id != seeded.id
    assert len(repo.rows) == 2


def test_null_attachment_id_is_not_a_wildcard(svc_repo):
    """Two attachment_id-less uploads with different targets stay two rows."""
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=None, filename="a.pdf", blob_path="attachments/a.pdf")
    second = _upload(svc, attachment_id=None, filename="b.pdf", blob_path="attachments/b.pdf")

    assert first.id != second.id
    assert len(repo.rows) == 2
    assert {p["blob_path"] for p in _payloads(repo)} == {"attachments/a.pdf", "attachments/b.pdf"}


def test_null_attachment_id_identical_target_still_coalesces(svc_repo):
    """...but an identical re-enqueue still debounces."""
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=None, filename="a.pdf", blob_path="attachments/a.pdf")
    second = _upload(svc, attachment_id=None, filename="a.pdf", blob_path="attachments/a.pdf")

    assert second.id == first.id
    assert len(repo.rows) == 1


def test_null_attachment_id_does_not_match_numbered_row(svc_repo):
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42, filename="a.pdf", blob_path="attachments/a.pdf")
    second = _upload(svc, attachment_id=None, filename="a.pdf", blob_path="attachments/a.pdf")

    assert first.id != second.id
    assert len(repo.rows) == 2


# --- status / kind scoping ---


def test_failed_row_for_same_attachment_coalesces(svc_repo):
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42)
    first.status = "failed"

    second = _upload(svc, attachment_id=42)

    assert second.id == first.id
    assert len(repo.rows) == 1


@pytest.mark.parametrize("status", ["in_progress", "dead_letter"])
def test_non_coalescible_status_creates_new_row(svc_repo, status):
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42)
    first.status = status

    second = _upload(svc, attachment_id=42)

    assert second.id != first.id
    assert len(repo.rows) == 2


def test_done_row_does_not_coalesce_but_guard_may_skip(svc_repo):
    """A completed row is not a coalesce candidate; the U-221 guard owns that
    case and returns the prior row without enqueueing."""
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42)
    first.status = "done"

    second = _upload(svc, attachment_id=42)

    assert second.id == first.id
    assert len(repo.rows) == 1
    assert repo.ready_after_calls == []


def test_excel_kind_never_coalesces(svc_repo):
    """Only upload_sharepoint_file is in _COALESCING_KINDS."""
    svc, repo = svc_repo

    first = svc.enqueue_excel_append(
        entity_type=_ENTITY_TYPE,
        entity_public_id=_ENTITY_PUBLIC_ID,
        drive_id=_DRIVE_ID,
        item_id="item-1",
        worksheet_name="DETAILS",
        values=[["a"]],
    )
    second = svc.enqueue_excel_append(
        entity_type=_ENTITY_TYPE,
        entity_public_id=_ENTITY_PUBLIC_ID,
        drive_id=_DRIVE_ID,
        item_id="item-1",
        worksheet_name="DETAILS",
        values=[["b"]],
    )

    assert first.id != second.id
    assert len(repo.rows) == 2


def test_different_entities_never_share_a_row(svc_repo):
    svc, repo = svc_repo

    first = _upload(svc, attachment_id=42)
    second = svc.enqueue_sharepoint_upload(
        entity_type=_ENTITY_TYPE,
        entity_public_id="ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee",
        drive_id=_DRIVE_ID,
        parent_item_id=_PARENT_ITEM_ID,
        filename="invoice.pdf",
        content_type="application/pdf",
        blob_path="attachments/invoice.pdf",
        attachment_id=42,
    )

    assert first.id != second.id
    assert len(repo.rows) == 2


# --- direct unit cover on the predicate ---


def test_find_coalescible_returns_none_without_dict_payload(svc_repo):
    svc, repo = svc_repo
    _upload(svc, attachment_id=42)

    assert (
        svc._find_coalescible(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            kind=KIND_UPLOAD_SHAREPOINT_FILE,
            payload=None,
        )
        is None
    )


@pytest.mark.parametrize(
    "raw_payload",
    [None, "", "   ", "{not-json", "null", "[1,2]", '"a string"', "123"],
)
def test_find_coalescible_unusable_payload_is_never_a_wildcard(svc_repo, raw_payload):
    """
    The load-bearing case for the NULL/unparsable-payload guard, isolated.

    Degrading an unusable payload to `{}` instead of SKIPPING the row would let
    it match a degenerate incoming payload on every field at once (all keys
    absent on both sides), i.e. a garbage row would swallow a real enqueue.
    Skip is the only safe reading of "we cannot prove what this row is".
    """
    svc, repo = svc_repo
    _seed_pending(repo, raw_payload)

    assert (
        svc._find_coalescible(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            kind=KIND_UPLOAD_SHAREPOINT_FILE,
            payload={},
        )
        is None
    )


def test_unusable_payload_row_does_not_swallow_degenerate_enqueue(svc_repo):
    """End-to-end shape of the same hazard, through the public enqueue()."""
    svc, repo = svc_repo
    seeded = _seed_pending(repo, "{not-json")

    created = svc.enqueue(
        kind=KIND_UPLOAD_SHAREPOINT_FILE,
        entity_type=_ENTITY_TYPE,
        entity_public_id=_ENTITY_PUBLIC_ID,
        tenant_id="tenant-abc",
        payload={},
    )

    assert created.id != seeded.id
    assert len(repo.rows) == 2
    assert seeded.payload == "{not-json", "the garbage row was overwritten"


def test_find_coalescible_picks_matching_row_among_many(svc_repo):
    svc, repo = svc_repo
    for n in (1, 2, 3):
        _upload(svc, attachment_id=n, filename=f"{n}.pdf", blob_path=f"attachments/{n}.pdf")

    match = svc._find_coalescible(
        entity_type=_ENTITY_TYPE,
        entity_public_id=_ENTITY_PUBLIC_ID,
        kind=KIND_UPLOAD_SHAREPOINT_FILE,
        payload={
            "drive_id": _DRIVE_ID,
            "parent_item_id": _PARENT_ITEM_ID,
            "filename": "2.pdf",
            "blob_path": "attachments/2.pdf",
            "attachment_id": 2,
        },
    )

    assert match is not None
    assert json.loads(match.payload)["attachment_id"] == 2


def test_find_coalescible_string_attachment_id_matches_int(svc_repo):
    """`same_attachment_id` int()-coerces; "42" and 42 are one attachment."""
    svc, repo = svc_repo
    first = _upload(svc, attachment_id=42)

    match = svc._find_coalescible(
        entity_type=_ENTITY_TYPE,
        entity_public_id=_ENTITY_PUBLIC_ID,
        kind=KIND_UPLOAD_SHAREPOINT_FILE,
        payload={
            "drive_id": _DRIVE_ID,
            "parent_item_id": _PARENT_ITEM_ID,
            "filename": "invoice.pdf",
            "blob_path": "attachments/invoice.pdf",
            "attachment_id": "42",
        },
    )

    assert match is not None
    assert match.id == first.id


def test_merge_coalesced_payload_new_fields_win():
    merged = MsOutboxService._merge_coalesced_payload(
        {"drive_id": "d", "parent_item_id": "OLD", "filename": "f", "blob_path": "b"},
        {"drive_id": "d", "parent_item_id": "NEW", "filename": "f", "blob_path": "b"},
    )
    assert merged["parent_item_id"] == "NEW"


def test_merge_coalesced_payload_drops_stale_keys_not_in_new_payload():
    """A key the new enqueue doesn't carry (and that isn't worker session
    state) must not survive — the new payload is the source of truth."""
    merged = MsOutboxService._merge_coalesced_payload(
        {"drive_id": "d", "parent_item_id": "p", "filename": "f", "blob_path": "b", "junk": 1},
        {"drive_id": "d", "parent_item_id": "p", "filename": "f", "blob_path": "b"},
    )
    assert "junk" not in merged


def test_parse_payload_handles_garbage():
    assert MsOutboxService._parse_payload(None) == {}
    assert MsOutboxService._parse_payload("") == {}
    assert MsOutboxService._parse_payload("{not-json") == {}
    assert MsOutboxService._parse_payload("[1,2]") == {}
    assert MsOutboxService._parse_payload('{"a": 1}') == {"a": 1}
