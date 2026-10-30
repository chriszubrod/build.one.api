"""U-535b b4: apply APPROVED backfill candidates (Bill -> Box PDF links), and undo a run's links.

Per approved, unlinked candidate of a 'ready' run (apply-as-you-go: the run stays 'ready' and review continues):

  1. DB pre-checks (also all the dry run does): bill exists, has >= 1 line, has NO BillLineItemAttachment on any line
     (``bill_has_attachment``), the check stored a SourceSha256 (``no_check_hash``), the stored size is <= 25 MB.
  2. Download from Box by the STORED SourceFileId only; refuse ``fetch_failed`` / ``too_large`` (> 25 MB) / ``not_pdf``
     (no ``%PDF-`` in the first 1 KB) / ``content_changed`` (SHA-256 of these bytes <> the SourceSha256 the reviewer saw).
  3. Attachment: an existing usable row with the same FileHash is REUSED (``reused_attachment``) -- not archived, Status
     NULL/'active', not expired; lowest Id wins -- never duplicated; else the bytes (unmodified -- no PDF compaction, so
     the hash stays the reviewed one) go to a new blob and a new Attachment row with the bill-PDF upload's fields
     (category ``bill_line_item``) (``created_attachment``), registered at once in AttachmentBackfillCreatedAttachment.
     Reuse assumes ONE company (dbo.Attachment has no CompanyId); see TODO for multi-tenant.
  4. ONE atomic proc (ApplyAttachmentBackfillCandidateLink) re-verifies everything under locks and writes one
     BillLineItemAttachment per bill line, recording each link id for undo.
  5. After the commit, the attachment is marked pending extraction when it has no extraction status yet.
  6. If step 4 refuses (or fails) after step 3 CREATED an attachment, that orphan is deleted at once (row via
     DeleteAttachmentBackfillCreatedAttachment, which deletes only registered rows and refuses if anything references
     them; blob only after the row delete commits). A reused attachment is never deleted.
  Undo unlinks candidate by candidate, then SWEEPS the run's registry: every attachment THIS run created and not yet
  deleted is retried (shared bytes within the run: creator A is kept 'referenced' while B still links it; B's unlink then
  frees it). The sweep is per run: when a candidate of ANOTHER run reused this run's attachment, undoing that other run
  does not delete it -- re-run this (creating) run's undo afterwards and its sweep frees it.

Nothing here enqueues an outbox row or writes SharePoint, Box, Excel, QBO or email. Blob I/O and the Box download are
injected by the CLI (scripts/attachment_backfill_apply.py), which is the composition root. Output is counts only: no
file names, no document text.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

from entities.attachment_backfill.business.service import MAX_VERIFY_BYTES, PDF_CONTENT_TYPE
from entities.attachment_backfill.persistence.repo import AttachmentBackfillRepository

logger = logging.getLogger(__name__)

ATTACHMENT_CATEGORY = "bill_line_item"
DEFAULT_MAX_DB_PERCENT = 85.0
DEFAULT_SIZE_CHECK_EVERY = 25
PAGE_SIZE = 100

# Refusal order. From the DB alone (db_refusal; the dry run reports exactly these): bill_missing, no_line_items,
# bill_has_attachment, no_check_hash, too_large. After the download (_fetch_checked): fetch_failed, too_large, not_pdf,
# content_changed.

STOP_DB_SIZE = "db_size"
STOP_INTERRUPTED = "interrupted"
STOP_LIMIT = "limit"

FetchBytes = Callable[[str], bytes]


class BlobStore(Protocol):
    """Injected by the CLI: ``store`` returns the blob URL; ``remove`` deletes one URL."""

    def store(self, blob_name: str, content: bytes, content_type: str) -> str: ...

    def remove(self, blob_url: str) -> None: ...


@dataclass(frozen=True)
class DbSize:
    used_bytes: Optional[int]
    max_bytes: Optional[int]

    @property
    def used_mb(self) -> Optional[float]:
        return None if self.used_bytes is None else self.used_bytes / (1024 * 1024)

    @property
    def max_mb(self) -> Optional[float]:
        return None if self.max_bytes is None else self.max_bytes / (1024 * 1024)

    @property
    def percent(self) -> Optional[float]:
        if self.used_bytes is None or not self.max_bytes:
            return None
        return 100.0 * self.used_bytes / self.max_bytes

    def allows(self, max_percent: float) -> bool:
        """False when unknown or at/over the threshold (fail closed)."""
        pct = self.percent
        return pct is not None and pct < max_percent


def db_refusal(row: dict) -> Optional[str]:
    """The first refusal the DB facts alone decide, in the order above; None = proceed."""
    if not row.get("BillExists"):
        return "bill_missing"
    if not int(row.get("LineItemCount") or 0):
        return "no_line_items"
    if int(row.get("BillAttachmentCount") or 0):
        return "bill_has_attachment"
    if not (row.get("SourceSha256") or "").strip():
        return "no_check_hash"
    size = row.get("SourceFileSize")
    if size is not None and int(size) > MAX_VERIFY_BYTES:
        return "too_large"
    return None


def _norm_sha(value: Any) -> str:
    return str(value or "").strip().lower()


def _extension(file_name: Optional[str]) -> str:
    name = (file_name or "").strip()
    if "." in name:
        ext = name.rsplit(".", 1)[1].strip()
        if ext and len(ext) <= 10 and ext.isalnum():
            return ext
    return "pdf"


_USABLE_STATUSES = ("", "active")


def _seeded(counts: Optional[Counter], keys: tuple[str, ...]) -> Counter:
    """The caller's Counter (mutated in place) or a new one, with `keys` present at 0."""
    counts = counts if counts is not None else Counter()
    for key in keys:
        counts.setdefault(key, 0)
    return counts


def _expired(value: Any) -> bool:
    """True when ExpirationDate is in the past; an unparseable value counts as expired (do not reuse)."""
    if value is None or value == "":
        return False
    try:
        when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    except ValueError:
        return True
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when < datetime.utcnow()


def pick_reusable(rows: list[dict]) -> Optional[dict]:
    """The attachment to reuse among same-hash rows: not archived, Status NULL/'active', not expired; lowest Id."""
    usable = [
        r for r in rows
        if not r.get("IsArchived")
        and str(r.get("Status") or "").strip().lower() in _USABLE_STATUSES
        and not _expired(r.get("ExpirationDate"))
    ]
    return min(usable, key=lambda r: int(r["Id"])) if usable else None


class AttachmentBackfillApplyService:
    def __init__(
        self,
        repo: Optional[AttachmentBackfillRepository] = None,
        *,
        attachments: Any = None,
        blobs: Optional[BlobStore] = None,
        fetch_bytes: Optional[FetchBytes] = None,
        stop_requested: Optional[Callable[[], bool]] = None,
    ):
        self.repo = repo or AttachmentBackfillRepository()
        if attachments is None:
            from entities.attachment.business.service import AttachmentService

            attachments = AttachmentService()
        self.attachments = attachments
        self.blobs = blobs
        self.fetch_bytes = fetch_bytes
        self.stop_requested = stop_requested or (lambda: False)

    # -- database size --------------------------------------------------------------------------- #

    def db_size(self) -> DbSize:
        row = self.repo.read_database_size() or {}
        used, cap = row.get("UsedBytes"), row.get("MaxSizeBytes")
        return DbSize(
            used_bytes=None if used is None else int(used),
            max_bytes=None if cap is None or int(cap) <= 0 else int(cap),
        )

    # -- paging ---------------------------------------------------------------------------------- #

    @staticmethod
    def _paged(read: Callable[..., list[dict]], run_id: int):
        """Keyset pages of `read` (rows arrive ORDER BY Id), one row at a time."""
        after = 0
        while True:
            rows = read(run_id=run_id, after_id=after, top=PAGE_SIZE)
            if not rows:
                return
            yield from rows
            after = int(rows[-1]["Id"])

    def _approved_unlinked(self, run_id: int):
        return self._paged(self.repo.read_candidates_for_apply, run_id)

    def _linked(self, run_id: int):
        return self._paged(self.repo.read_linked_candidates, run_id)

    def _stop_before(self, counts: Counter, limit: Optional[int]) -> Optional[str]:
        """The stop reason to honour before the next candidate: Ctrl-C first, then --limit."""
        if self.stop_requested():
            return STOP_INTERRUPTED
        if limit is not None and counts["processed"] >= limit:
            return STOP_LIMIT
        return None

    def _reusable(self, sha: str) -> Optional[dict]:
        return pick_reusable(self.repo.read_attachments_by_hash(file_hash=sha))

    # -- dry run --------------------------------------------------------------------------------- #

    def plan(self, run_id: int, *, limit: Optional[int] = None,
             counts: Optional[Counter] = None) -> tuple[dict, Optional[str]]:
        """DB state only: zero downloads, zero writes. (counts, stop_reason); `counts` is filled in place."""
        counts = _seeded(counts, ("approved_unlinked", "would_apply", "would_reuse_attachment",
                                  "would_create_attachment", "would_link_rows"))
        for row in self._approved_unlinked(run_id):
            if self.stop_requested():
                return dict(counts), STOP_INTERRUPTED
            if limit is not None and counts["approved_unlinked"] >= limit:
                break
            counts["approved_unlinked"] += 1
            reason = db_refusal(row)
            if reason:
                counts[f"would_refuse_{reason}"] += 1
                continue
            try:
                existing = self._reusable(_norm_sha(row.get("SourceSha256")))
            except Exception as exc:
                logger.warning("attachment_backfill.plan.read_error candidate_id=%s exc=%s", row.get("Id"),
                               type(exc).__name__)
                counts["error_read"] += 1
                continue
            counts["would_apply"] += 1
            counts["would_link_rows"] += int(row.get("LineItemCount") or 0)
            counts["would_reuse_attachment" if existing else "would_create_attachment"] += 1
        return dict(counts), None

    # -- apply ----------------------------------------------------------------------------------- #

    def apply(
        self,
        run_id: int,
        *,
        limit: Optional[int] = None,
        max_db_percent: float = DEFAULT_MAX_DB_PERCENT,
        size_check_every: int = DEFAULT_SIZE_CHECK_EVERY,
        counts: Optional[Counter] = None,
    ) -> tuple[dict, Optional[str]]:
        """(counts, stop_reason). stop_reason: None (all done), 'limit', 'db_size' or 'interrupted'.

        `counts` is filled in place, so a caller still has them when this raises. The caller has already checked the
        database size once; it is re-checked every `size_check_every` candidates and the pass stops (cleanly, between
        candidates) at the threshold.
        """
        if self.blobs is None or self.fetch_bytes is None:
            raise RuntimeError("apply needs an injected blob store and Box fetch")
        counts = _seeded(counts, ("processed", "applied", "reused_attachment", "created_attachment", "links_created"))
        size_check_every = max(1, int(size_check_every))
        stop: Optional[str] = None
        for row in self._approved_unlinked(run_id):
            stop = self._stop_before(counts, limit)
            if stop:
                break
            if counts["processed"] and counts["processed"] % size_check_every == 0:
                if not self.db_size().allows(max_db_percent):
                    stop = STOP_DB_SIZE
                    break
            counts["processed"] += 1
            self._apply_one(run_id, row, counts)
        if stop is None:
            try:
                closed = self.repo.close_run_if_done(run_id=run_id)
                counts["run_closed"] = 1 if closed else 0
            except Exception as exc:
                # The links are committed; a failed closure (deadlock, DB blip) is counted, never raised. A re-run closes it.
                logger.warning("attachment_backfill.apply.close_error run_id=%s exc=%s", run_id, type(exc).__name__)
                counts["close_error"] += 1
        return dict(counts), stop

    def _fetch_checked(self, row: dict) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
        """(content, sha256, None) or (None, None, refusal code)."""
        try:
            content = self.fetch_bytes(str(row["SourceFileId"]))
        except Exception as exc:
            logger.warning("attachment_backfill.apply.fetch_error candidate_id=%s exc=%s", row.get("Id"),
                           type(exc).__name__)
            return None, None, "fetch_failed"
        if len(content) > MAX_VERIFY_BYTES:
            return None, None, "too_large"
        if b"%PDF-" not in content[:1024]:
            return None, None, "not_pdf"
        sha = hashlib.sha256(content).hexdigest()
        if sha != _norm_sha(row.get("SourceSha256")):
            return None, None, "content_changed"
        return content, sha, None

    def _create_attachment(self, row: dict, content: bytes, sha: str):
        """(attachment, blob_url). On an error the new blob is removed; on Ctrl-C it is LEFT (the row may have
        committed and must never point at a deleted blob) and only its blob name is logged."""
        ext = _extension(row.get("SourceFileName"))
        blob_name = f"{uuid.uuid4()}.{ext}"
        blob_url = self.blobs.store(blob_name, content, PDF_CONTENT_TYPE)
        try:
            attachment = self.attachments.create(
                filename=row.get("SourceFileName") or blob_name,
                original_filename=row.get("SourceFileName") or blob_name,
                file_extension=ext,
                content_type=PDF_CONTENT_TYPE,
                file_size=len(content),
                file_hash=sha,
                blob_url=blob_url,
                description=None,
                category=ATTACHMENT_CATEGORY,
                tags=None,
                is_archived=False,
                status=None,
                expiration_date=None,
                storage_tier="Hot",
            )
            if attachment is None or getattr(attachment, "id", None) is None:
                raise RuntimeError("attachment create returned no row")
        except Exception:
            self._remove_blob(blob_url)
            raise
        except BaseException:
            logger.warning("attachment_backfill.apply.blob_left_on_interrupt blob_name=%s", blob_name)
            raise
        return attachment, blob_url

    def _remove_blob(self, url: Optional[str]) -> bool:
        if not url:
            return True
        try:
            self.blobs.remove(url)
            return True
        except Exception as exc:
            logger.warning("attachment_backfill.apply.blob_remove_error exc=%s", type(exc).__name__)
            return False

    def _delete_created_attachment(self, attachment_id: int, sha: str, counts: Counter, prefix: str) -> None:
        """Delete a REGISTERED attachment when nothing references it; blobs only after the row delete commits."""
        try:
            result = self.repo.delete_created_attachment(attachment_id=attachment_id, file_hash=sha) or {}
        except Exception as exc:
            logger.warning("attachment_backfill.apply.attachment_delete_error attachment_id=%s exc=%s",
                           attachment_id, type(exc).__name__)
            counts[f"{prefix}_kept_error"] += 1
            return
        if not result.get("Deleted"):
            # Kept (still in the registry unless already gone): the undo sweep retries it. Logged by id only.
            logger.warning("attachment_backfill.apply.attachment_kept attachment_id=%s reason=%s",
                           attachment_id, result.get("RefusedReason"))
            counts[f"{prefix}_kept_{result.get('RefusedReason') or 'unknown'}"] += 1
            return
        counts[f"{prefix}_deleted"] += 1
        for url in (result.get("BlobUrl"), result.get("ExtractedTextBlobUrl")):
            if url and not self._remove_blob(url):
                counts["blob_remove_failed"] += 1

    def _register(self, run_id: int, row: dict, attachment_id: int, sha: str, blob_url: str, counts: Counter) -> bool:
        """Register a just-created attachment; the proc is idempotent, so one retry is safe. On failure (or Ctrl-C) the
        attachment id AND blob name are logged, so an unregistered orphan is always identifiable (no file names)."""
        blob_name = str(blob_url or "").rsplit("/", 1)[-1]
        last: Optional[Exception] = None
        try:
            for _attempt in range(2):
                try:
                    self.repo.register_created_attachment(
                        run_id=run_id, candidate_id=int(row["Id"]), attachment_id=attachment_id, file_hash=sha,
                        blob_url=blob_url,
                    )
                    return True
                except Exception as exc:
                    last = exc
        except BaseException:
            logger.warning("attachment_backfill.apply.unregistered_on_interrupt attachment_id=%s blob_name=%s",
                           attachment_id, blob_name)
            raise
        # Not registered => the delete proc will never remove it, so do not link it either; leave it for a human.
        logger.warning("attachment_backfill.apply.register_error attachment_id=%s blob_name=%s exc=%s", attachment_id,
                       blob_name, type(last).__name__)
        counts["error_register"] += 1
        return False

    def _apply_one(self, run_id: int, row: dict, counts: Counter) -> None:
        reason = db_refusal(row)
        if reason:
            counts[f"refused_{reason}"] += 1
            return
        content, sha, reason = self._fetch_checked(row)
        if reason:
            counts[f"refused_{reason}"] += 1
            return

        try:
            existing = self._reusable(sha)
        except Exception as exc:
            logger.warning("attachment_backfill.apply.read_error candidate_id=%s exc=%s", row.get("Id"),
                           type(exc).__name__)
            counts["error_read"] += 1
            return

        created = existing is None
        if existing is not None:
            attachment_id, extraction_status = int(existing["Id"]), existing.get("ExtractionStatus")
        else:
            try:
                attachment, blob_url = self._create_attachment(row, content, sha)
            except Exception as exc:
                logger.warning("attachment_backfill.apply.attachment_create_error candidate_id=%s exc=%s",
                               row.get("Id"), type(exc).__name__)
                counts["error_attachment_create"] += 1
                return
            attachment_id, extraction_status = int(attachment.id), getattr(attachment, "extraction_status", None)
            if not self._register(run_id, row, attachment_id, sha, blob_url, counts):
                return

        linked = False
        result: Optional[dict] = None
        try:
            result = self.repo.apply_candidate_link(
                candidate_id=int(row["Id"]),
                row_version=bytes(row["RowVersion"]),
                attachment_id=attachment_id,
                attachment_created=created,
            )
            refused = result.get("RefusedReason") if result else "no_result"
            linked = refused is None
        except Exception as exc:
            logger.warning("attachment_backfill.apply.link_error candidate_id=%s exc=%s", row.get("Id"),
                           type(exc).__name__)
            refused = None
            counts["error_link"] += 1
        finally:
            # A refusal, an error, or Ctrl-C after a NEW attachment was created: never leave an orphan behind.
            if created and not linked:
                self._delete_created_attachment(attachment_id, sha, counts, "orphan_attachment")

        if refused:
            counts[f"refused_{refused}"] += 1
            return
        if not linked:
            return
        counts["applied"] += 1
        counts["created_attachment" if created else "reused_attachment"] += 1
        counts["links_created"] += int(result.get("LinkCount") or 0)
        if not extraction_status:
            try:
                self.attachments.mark_pending_extraction(attachment_id)
                counts["marked_pending_extraction"] += 1
            except Exception as exc:
                logger.warning("attachment_backfill.apply.mark_pending_error attachment_id=%s exc=%s",
                               attachment_id, type(exc).__name__)
                counts["mark_pending_extraction_failed"] += 1

    # -- undo ------------------------------------------------------------------------------------ #

    def plan_undo(self, run_id: int, *, counts: Optional[Counter] = None) -> tuple[dict, Optional[str]]:
        counts = _seeded(counts, ("linked_candidates", "recorded_links", "attachments_created_by_apply"))
        for row in self._linked(run_id):
            if self.stop_requested():
                return dict(counts), STOP_INTERRUPTED
            counts["linked_candidates"] += 1
            counts["recorded_links"] += int(row.get("RecordedLinkCount") or 0)
            if row.get("AttachmentCreatedByApply"):
                counts["attachments_created_by_apply"] += 1
        return dict(counts), None

    def undo(self, run_id: int, *, limit: Optional[int] = None,
             counts: Optional[Counter] = None) -> tuple[dict, Optional[str]]:
        """Unlink this run's candidates one by one (each its own transaction), then sweep the run's registry of created
        attachments (deleted when nothing references them). (counts, stop_reason); `counts` is filled in place."""
        if self.blobs is None:
            raise RuntimeError("undo needs an injected blob store")
        counts = _seeded(counts, ("processed", "undone", "links_deleted"))
        stop: Optional[str] = None
        for row in self._linked(run_id):
            stop = self._stop_before(counts, limit)
            if stop:
                break
            counts["processed"] += 1
            try:
                result = self.repo.undo_candidate_link(candidate_id=int(row["Id"])) or {}
            except Exception as exc:
                logger.warning("attachment_backfill.undo.error candidate_id=%s exc=%s", row.get("Id"),
                               type(exc).__name__)
                counts["error_undo"] += 1
                continue
            if result.get("RefusedReason"):
                counts[f"refused_{result['RefusedReason']}"] += 1
                continue
            counts["undone"] += 1
            counts["links_deleted"] += int(result.get("LinksDeleted") or 0)
        if stop != STOP_INTERRUPTED:
            for reg in self.repo.read_created_attachments_to_sweep(run_id=run_id):
                if self.stop_requested():
                    stop = STOP_INTERRUPTED
                    break
                self._delete_created_attachment(int(reg["AttachmentId"]), _norm_sha(reg.get("FileHash")), counts,
                                                "swept")
        return dict(counts), stop
