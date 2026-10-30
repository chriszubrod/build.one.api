"""Entity contract — AttachmentBackfill.   (maintained with the code; checked by tests/test_entity_docstrings.py)

Owns
  dbo.AttachmentBackfillRun — one generation pass of the U-535b Bill -> PDF backfill (source box|qbo, status).
  dbo.AttachmentBackfillCandidate — one proposed (Box file, Bill) link per row, its check status + neutral evidence and (b2+) its review.
Invariants
  b1 is staging only: nothing here writes Attachment, BillLineItemAttachment, blob storage or any outbox.
  Only tier A is generated: normalized invoice # + amount exact and same sign + bill project = filename-prefix or folder project, mutually unique.
  Insert is idempotent on (RunId, SourceFileId). It is refused (RefusedReason) when: the run is not 'generating'; another file already claims the bill in this run; the same (SourceFileId, BillId) pair was rejected in any run; or the bill has a pending/approved candidate in another non-cancelled run (an 'applied' run counts, so a pending candidate left in an applied run blocks that bill until b4 resolves it).
  Resume (prune_stale) deletes this generating run's candidates that are pending, unchecked, unlinked and absent from the new plan, before inserting.
  A new run is refused while another 'generating' or 'ready' run of the same source exists (the job's --force-new-run overrides).
  Run status moves generating -> ready -> applied, or generating/ready -> cancelled; never out of applied or cancelled.
  The per-PDF check never presents a conclusion: it stores a CheckStatus (checked / unreadable / error) and neutral evidence only — InvoiceSeenCount, AmountSeenCount, VendorSeen, OtherInvoiceLabelCount, StatementLike, UsedOcr (rules in evidence.py). The reviewer weighs them against the highlighted PDF (b3).
  The check runs only on a 'ready' run (enforced by both the read and the update sproc) and never writes ReviewStatus, a reviewed row or a linked row.
  PDF text is read in memory to collect the evidence and then dropped: no text, memo or line description is stored or logged.
  Files over 25 MB (no download), and PDFs with < 40 non-space chars of text after OCR, are 'unreadable'. OCR (existing Document Intelligence read path, first 3 pages) runs only when the text layer has < 40 non-space chars, or < 400 chars and neither the invoice # nor the amount was seen in it.
  BillId carries no FK (a deleted bill leaves a stale candidate that b2/b4 treat as stale).
  Writes run on one connection per loop and commit after every row, so an interrupted pass resumes where it stopped; Ctrl-C cancels pending downloads and leaves unwritten rows unchecked (NULL).
External writes
  None. Reads only: Box folder listing and file download (scripts/attachment_backfill_candidates.py), Document Intelligence read for scans.
Sprocs
  CreateAttachmentBackfillRun
  DeleteStaleAttachmentBackfillCandidates
  InsertAttachmentBackfillCandidate
  ReadAttachmentBackfillCandidatesForVerification
  ReadAttachmentBackfillRunByPublicId
  ReadAttachmentBackfillRunSummary
  ReadBillsForAttachmentBackfill
  ReadOpenAttachmentBackfillRuns
  UpdateAttachmentBackfillCandidateVerification
  UpdateAttachmentBackfillRunStatus
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from entities.attachment_backfill.business.matcher import (
    BillRow,
    InventoryFile,
    MatchResult,
    match_tier_a,
)
from entities.attachment_backfill.business.project_map import ProjectMap, ProjectRef
from entities.attachment_backfill.business.evidence import Evidence, collect_evidence
from entities.attachment_backfill.persistence.repo import AttachmentBackfillRepository

logger = logging.getLogger(__name__)

OCR_MIN_TEXT_CHARS = 40
OCR_SHORT_TEXT_CHARS = 400
OCR_MAX_PAGES = 3
MAX_VERIFY_BYTES = 25 * 1024 * 1024
PDF_CONTENT_TYPE = "application/pdf"

CHECKED = "checked"
UNREADABLE = "unreadable"
ERROR = "error"
CHECK_STATUSES = (CHECKED, UNREADABLE, ERROR)

FetchBytes = Callable[[str], bytes]
TextFn = Callable[[bytes], str]


def _non_space_len(text: Optional[str]) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def first_pages(content: bytes, pages: int = OCR_MAX_PAGES) -> bytes:
    """The first `pages` pages of a PDF as a new PDF; the input unchanged if it cannot be split."""
    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(io.BytesIO(content))
        if len(reader.pages) <= pages:
            return content
        writer = PdfWriter()
        for page in reader.pages[:pages]:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as exc:
        logger.warning("attachment_backfill.first_pages_error exc=%s", type(exc).__name__)
        return content


def default_text_layer(content: bytes) -> str:
    from entities.attachment.business.extraction_service import _extract_text_layer

    text, _pages = _extract_text_layer(content, PDF_CONTENT_TYPE)
    return text or ""


def default_ocr(content: bytes, *, di=None) -> str:
    """The existing DI `prebuilt-read` path (AttachmentExtractionService's), on the first 3 pages only."""
    if di is None:
        from integrations.azure.document_intelligence.business.service import DocumentIntelligenceService

        di = DocumentIntelligenceService()
    extracted = di.extract_text(first_pages(content), content_type=PDF_CONTENT_TYPE)
    return extracted.get("content") or ""


def needs_ocr(layer_text: str, evidence: Evidence) -> bool:
    if _non_space_len(layer_text) < OCR_MIN_TEXT_CHARS:
        return True
    nothing_seen = not evidence.invoice_seen_count and not evidence.amount_seen_count
    return len(layer_text.strip()) < OCR_SHORT_TEXT_CHARS and nothing_seen


@dataclass(frozen=True)
class _Evaluation:
    candidate_id: int
    sha256: Optional[str]
    status: str
    evidence: Optional[Evidence] = None
    used_ocr: Optional[bool] = None


class AttachmentBackfillService:
    def __init__(self, repo: Optional[AttachmentBackfillRepository] = None):
        self.repo = repo or AttachmentBackfillRepository()

    # -- runs ------------------------------------------------------------------ #

    def create_run(self, *, source: str, notes: Optional[str] = None,
                   created_by_user_id: Optional[int] = None) -> dict:
        return self.repo.create_run(source=source, notes=notes, created_by_user_id=created_by_user_id)

    def read_run(self, public_id: str) -> Optional[dict]:
        return self.repo.read_run_by_public_id(public_id)

    def mark_run_ready(self, run_id: int) -> Optional[dict]:
        return self.repo.update_run_status(run_id=run_id, status="ready")

    def cancel_run(self, run_id: int) -> Optional[dict]:
        return self.repo.update_run_status(run_id=run_id, status="cancelled")

    def open_runs(self, source: str) -> list[dict]:
        """'generating' or 'ready' runs of `source`, newest first."""
        return self.repo.read_open_runs(source=source)

    def run_summary(self, run_id: int) -> list[dict]:
        return self.repo.read_run_summary(run_id=run_id)

    # -- generation ------------------------------------------------------------ #

    @staticmethod
    def plan_box_candidates(
        inventory: Iterable[InventoryFile],
        bills: Iterable[BillRow],
        projects: Iterable[ProjectRef],
    ) -> MatchResult:
        """Pure: tier-A candidates and exclusion counts. Writes nothing."""
        return match_tier_a(inventory, bills, ProjectMap(projects))

    def generate_box_candidates(
        self,
        run_id: int,
        inventory: Iterable[InventoryFile],
        bills: Iterable[BillRow],
        projects: Iterable[ProjectRef],
        *,
        prune_stale: bool = False,
    ) -> dict:
        """Insert the plan on one connection, committing after every row. prune_stale (resume): first drop
        this run's pending, unchecked, unlinked candidates that the new plan no longer proposes."""
        plan = self.plan_box_candidates(inventory, bills, projects)
        counts = plan.counts()
        outcome: Counter = Counter({"inserted": 0, "already_present": 0})
        counts["pruned_stale"] = 0
        with self.repo.connection() as conn:
            if prune_stale:
                keep = [(c.file_id, c.bill_id) for c in plan.candidates]
                counts["pruned_stale"] = int(self.repo.delete_stale_candidates(run_id=run_id, keep=keep, conn=conn))
                conn.commit()
            for candidate in plan.candidates:
                row = self.repo.insert_candidate(
                    run_id=run_id,
                    bill_id=candidate.bill_id,
                    source_file_id=candidate.file_id,
                    source_file_name=candidate.file_name,
                    source_folder_id=candidate.folder_id,
                    source_file_size=candidate.size,
                    match_tier=candidate.tier,
                    conn=conn,
                )
                conn.commit()
                if row is None or row.get("Id") is None:
                    outcome[f"refused_{(row or {}).get('RefusedReason') or 'unknown'}"] += 1
                elif row.get("Inserted"):
                    outcome["inserted"] += 1
                else:
                    outcome["already_present"] += 1
        counts.update(outcome)
        return counts

    # -- per-PDF check --------------------------------------------------------- #

    @staticmethod
    def _evaluate(row: dict, fetch_bytes: FetchBytes, text_layer: TextFn, ocr: TextFn) -> _Evaluation:
        candidate_id = int(row["Id"])
        size = row.get("SourceFileSize")
        if size is not None and int(size) > MAX_VERIFY_BYTES:
            return _Evaluation(candidate_id=candidate_id, sha256=None, status=UNREADABLE)
        sha256: Optional[str] = None
        fields = dict(
            bill_number=row.get("BillNumber"),
            total_amount=row.get("TotalAmount"),
            vendor_name=row.get("VendorName"),
        )
        try:
            content = fetch_bytes(str(row["SourceFileId"]))
            sha256 = hashlib.sha256(content).hexdigest()
            if len(content) > MAX_VERIFY_BYTES:
                return _Evaluation(candidate_id=candidate_id, sha256=sha256, status=UNREADABLE)
            text = text_layer(content)
            evidence = collect_evidence(text, **fields)
            used_ocr = needs_ocr(text, evidence)
            if used_ocr:
                text = text + "\n" + ocr(content)
                evidence = collect_evidence(text, **fields)
            if _non_space_len(text) < OCR_MIN_TEXT_CHARS:
                return _Evaluation(candidate_id=candidate_id, sha256=sha256, status=UNREADABLE, used_ocr=used_ocr)
            return _Evaluation(
                candidate_id=candidate_id, sha256=sha256, status=CHECKED, evidence=evidence, used_ocr=used_ocr
            )
        except Exception as exc:  # per-candidate isolation: one bad PDF never stops the pass
            logger.warning(
                "attachment_backfill.check_error candidate_id=%s exc=%s", candidate_id, type(exc).__name__
            )
            return _Evaluation(candidate_id=candidate_id, sha256=sha256, status=ERROR)

    def _write_check(self, ev: _Evaluation, conn) -> Optional[dict]:
        e = ev.evidence
        return self.repo.update_candidate_verification(
            candidate_id=ev.candidate_id,
            source_sha256=ev.sha256,
            check_status=ev.status,
            invoice_seen_count=e.invoice_seen_count if e else None,
            amount_seen_count=e.amount_seen_count if e else None,
            vendor_seen=e.vendor_seen if e else None,
            other_invoice_label_count=e.other_invoice_label_count if e else None,
            statement_like=e.statement_like if e else None,
            used_ocr=ev.used_ocr,
            conn=conn,
        )

    @staticmethod
    def _tally(counts: Counter, ev: _Evaluation, written: Optional[dict]) -> None:
        counts["processed"] += 1
        if written is None or written.get("Id") is None:
            counts["refused"] += 1
            return
        counts[ev.status] += 1
        if ev.used_ocr:
            counts["used_ocr"] += 1
        e = ev.evidence
        if e is None:
            return
        if e.statement_like:
            counts["statement_like"] += 1
        if e.invoice_seen_count is None:
            counts["invoice_not_checkable"] += 1
        elif e.invoice_seen_count == 0:
            counts["invoice_seen_0"] += 1
        else:
            counts["invoice_seen_1plus"] += 1
        if e.other_invoice_label_count:
            counts["other_label_1plus"] += 1

    def verify_candidates(
        self,
        run_id: int,
        fetch_bytes: FetchBytes,
        text_layer: Optional[TextFn] = None,
        ocr: Optional[TextFn] = None,
        *,
        batch: int = 50,
        workers: int = 1,
        include_errors: bool = False,
        limit: Optional[int] = None,
    ) -> dict:
        """One keyset pass over the run's unchecked candidates. Returns counts only (no conclusions)."""
        layer_fn = text_layer or default_text_layer
        ocr_fn = ocr or default_ocr
        counts: Counter = Counter({status: 0 for status in CHECK_STATUSES})
        counts.update(
            processed=0, refused=0, used_ocr=0, statement_like=0,
            invoice_seen_0=0, invoice_seen_1plus=0, invoice_not_checkable=0, other_label_1plus=0,
        )
        after_id = 0
        batch = max(1, int(batch))
        workers = max(1, int(workers))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            while limit is None or counts["processed"] < limit:
                top = batch if limit is None else min(batch, limit - counts["processed"])
                rows = self.repo.read_candidates_for_verification(
                    run_id=run_id, top=top, after_id=after_id, include_errors=include_errors
                )
                if not rows:
                    break
                after_id = max(int(r["Id"]) for r in rows)
                with self.repo.connection() as conn:
                    for ev in pool.map(lambda r: self._evaluate(r, fetch_bytes, layer_fn, ocr_fn), rows):
                        written = self._write_check(ev, conn)
                        conn.commit()
                        self._tally(counts, ev, written)
        except BaseException:  # KeyboardInterrupt included
            # Stop promptly: cancel queued downloads; rows not yet written stay unchecked (NULL) for a re-run.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return dict(counts)
