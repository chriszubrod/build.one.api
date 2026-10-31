"""U-535b b2: the review API's business layer -- runs, facets, queue, candidate detail, PDF, approve/reject/undo.

Nothing here creates an Attachment / BillLineItemAttachment row or touches a Bill: a review writes the
candidate's review columns only (b4 links later). Responses stay neutral -- counts, flags and the
reviewer's own decision, never a conclusion about the link.

The PDF is fetched from Box by the candidate's STORED SourceFileId only. Snippets are computed live on
every detail read from the same text b1 read (text layer; OCR of the first 3 pages when b1's
``needs_ocr`` says so) and are never stored or logged. Logs carry exception type names only.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
from datetime import date
from decimal import Decimal
from typing import Callable, Optional

from entities.attachment_backfill.business.evidence import collect_evidence
from entities.attachment_backfill.business.service import (
    MAX_VERIFY_BYTES,
    OCR_MIN_TEXT_CHARS,
    _non_space_len,
    default_ocr,
    default_text_layer,
    needs_ocr,
)
from entities.attachment_backfill.business.snippets import collect_snippets
from entities.attachment_backfill.persistence.repo import AttachmentBackfillRepository

logger = logging.getLogger(__name__)

# The one Python source of these value sets; api/router.py builds its Literal types from them. The SQL procs carry their
# own copies (the evidence-group function, the reject-reason allowlist), pinned by the SQL tests.
DECISIONS = ("approved", "rejected")
REJECT_REASONS = ("wrong_bill", "statement", "multiple_invoices", "duplicate", "unreadable", "other")
EVIDENCE_GROUPS = ("clean", "flagged", "invoice_only", "amount_only", "neither")
# b5: the queue also filters by 'sample' (members of the run's OPEN review sample, b5_bulk.sql), and the facets add rows with
# evidence_group 'sample' (the members counted a second time, same shape). It is a filter, not an evidence group.
SAMPLE_GROUP = "sample"
QUEUE_GROUPS = EVIDENCE_GROUPS + (SAMPLE_GROUP,)
REVIEW_STATUSES = ("pending", "approved", "rejected")
QUEUE_DEFAULT_LIMIT = 50
QUEUE_MAX_LIMIT = 200
ROW_VERSION_BYTES = 8

# PDF failure codes (PdfUnavailable.code); the first two double as snippet failure codes.
PDF_TOO_LARGE = "too_large"
PDF_FETCH_FAILED = "fetch_failed"
PDF_NOT_PDF = "not_pdf"

# Snippet failure codes (never exception text).
SNIPPET_TOO_LARGE = PDF_TOO_LARGE
SNIPPET_FETCH_FAILED = PDF_FETCH_FAILED
SNIPPET_EXTRACT_FAILED = "extract_failed"
SNIPPET_OCR_FAILED = "ocr_failed"
SNIPPET_UNREADABLE = "unreadable"
SNIPPET_CONTENT_CHANGED = "content_changed"

FetchBytes = Callable[[str], bytes]
TextFn = Callable[[bytes], str]


class CandidateNotFound(Exception):
    pass


class RunNotFound(Exception):
    pass


class ReviewRefused(Exception):
    """The review/undo proc refused; ``code`` is its RefusedReason."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PdfUnavailable(Exception):
    """``code`` is one of too_large / fetch_failed / not_pdf."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def decode_row_version(value: Optional[str]) -> bytes:
    """Base64 -> the 8-byte ROWVERSION; ValueError on anything else."""
    if not isinstance(value, str) or not value:
        raise ValueError("row_version is required")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("row_version must be base64") from None
    if len(raw) != ROW_VERSION_BYTES:
        raise ValueError("row_version must decode to 8 bytes")
    return raw


def _b64(value) -> Optional[str]:
    return base64.b64encode(bytes(value)).decode("ascii") if value is not None else None


def _str(value) -> Optional[str]:
    return str(value) if value is not None else None


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)  # datetime is a date


def _bool(value) -> Optional[bool]:
    return None if value is None else bool(value)


def _int(value) -> Optional[int]:
    return None if value is None else int(value)


def _money(value) -> Optional[str]:
    return None if value is None else str(Decimal(str(value)))


def candidate_out(row: dict) -> dict:
    """The API shape of a queue/detail row. Internal ids and the Box file id are not exposed."""
    return {
        "public_id": _str(row.get("PublicId")),
        "row_version": _b64(row.get("RowVersion")),
        "run_public_id": _str(row.get("RunPublicId")),
        "run_status": row.get("RunStatus"),
        "bill_public_id": _str(row.get("BillPublicId")),
        "bill_number": row.get("BillNumber"),
        "bill_date": _iso(row.get("BillDate")),
        "bill_year": _int(row.get("BillYear")),
        "total_amount": _money(row.get("TotalAmount")),
        "vendor_name": row.get("VendorName"),
        "project_public_id": _str(row.get("ProjectPublicId")),
        "project_name": row.get("ProjectName"),
        "project_names": row.get("ProjectNames"),
        "source_file_name": row.get("SourceFileName"),
        "source_file_size": _int(row.get("SourceFileSize")),
        "match_tier": row.get("MatchTier"),
        "check_status": row.get("CheckStatus"),
        "invoice_seen_count": _int(row.get("InvoiceSeenCount")),
        "amount_seen_count": _int(row.get("AmountSeenCount")),
        "vendor_seen": _bool(row.get("VendorSeen")),
        "other_invoice_label_count": _int(row.get("OtherInvoiceLabelCount")),
        "statement_like": _bool(row.get("StatementLike")),
        "used_ocr": _bool(row.get("UsedOcr")),
        "evidence_group": row.get("EvidenceGroup"),
        "review_status": row.get("ReviewStatus"),
        "reject_reason": row.get("RejectReason"),
        "reviewed_by_user_public_id": _str(row.get("ReviewedByUserPublicId")),
        "reviewed_datetime": _iso(row.get("ReviewedDatetime")),
        "linked": bool(row.get("Linked")),
    }


def review_out(row: dict) -> dict:
    return {
        "public_id": _str(row.get("PublicId")),
        "row_version": _b64(row.get("RowVersion")),
        "review_status": row.get("ReviewStatus"),
        "reject_reason": row.get("RejectReason"),
        "reviewed_by_user_public_id": _str(row.get("ReviewedByUserPublicId")),
        "reviewed_datetime": _iso(row.get("ReviewedDatetime")),
    }


def run_out(row: dict) -> dict:
    return {
        "public_id": _str(row.get("PublicId")),
        "source": row.get("Source"),
        "status": row.get("Status"),
        "created_datetime": _iso(row.get("CreatedDatetime")),
        "modified_datetime": _iso(row.get("ModifiedDatetime")),
        "candidate_count": int(row.get("CandidateCount") or 0),
        "pending": int(row.get("Pending") or 0),
        "approved": int(row.get("Approved") or 0),
        "rejected": int(row.get("Rejected") or 0),
        "unchecked": int(row.get("Unchecked") or 0),
        "linked": int(row.get("Linked") or 0),
    }


def facet_out(row: dict) -> dict:
    return {
        "evidence_group": row.get("EvidenceGroup"),
        "project_public_id": _str(row.get("ProjectPublicId")),
        "project_name": row.get("ProjectName"),
        "bill_year": _int(row.get("BillYear")),
        "review_status": row.get("ReviewStatus"),
        "count": int(row.get("CandidateCount") or 0),
    }


def content_matches_check(stored_sha256: Optional[str], content: Optional[bytes]) -> Optional[bool]:
    """True/False when b1 stored a hash and bytes were fetched; None when either is missing."""
    if not stored_sha256 or content is None:
        return None
    return hashlib.sha256(content).hexdigest().lower() == str(stored_sha256).strip().lower()


def _written(row: Optional[dict]) -> dict:
    """The review/undo proc's row as the API shape, or ReviewRefused with its RefusedReason."""
    if not row or row.get("Id") is None:
        raise ReviewRefused((row or {}).get("RefusedReason") or "not_found")
    return review_out(row)


def _default_fetch(file_id: str) -> bytes:
    from integrations.box.base.client import BoxHttpClient

    with BoxHttpClient() as client:
        return client.download_file(file_id, operation_name="box.attachment_backfill.review_download")


class AttachmentBackfillReviewService:
    def __init__(
        self,
        repo: Optional[AttachmentBackfillRepository] = None,
        *,
        fetch_bytes: Optional[FetchBytes] = None,
        text_layer: Optional[TextFn] = None,
        ocr: Optional[TextFn] = None,
    ):
        self.repo = repo or AttachmentBackfillRepository()
        self.fetch_bytes = fetch_bytes or _default_fetch
        self.text_layer = text_layer or default_text_layer
        self.ocr = ocr or default_ocr

    # -- reads -------------------------------------------------------------------------------- #

    def list_runs(self) -> list[dict]:
        return [run_out(r) for r in self.repo.read_runs_for_review()]

    def _require_run(self, run_public_id: str) -> dict:
        run = self.repo.read_run_by_public_id(run_public_id)
        if not run:
            raise RunNotFound()
        return run

    def facets(self, run_public_id: str) -> list[dict]:
        self._require_run(run_public_id)
        return [facet_out(r) for r in self.repo.read_run_facets(run_public_id=run_public_id)]

    def queue(
        self,
        run_public_id: str,
        *,
        group: Optional[str] = None,
        project_public_id: Optional[str] = None,
        no_project: bool = False,
        year: Optional[int] = None,
        no_year: bool = False,
        review_status: Optional[str] = "pending",
        after: Optional[str] = None,
        limit: int = QUEUE_DEFAULT_LIMIT,
    ) -> tuple[list[dict], Optional[str]]:
        """(rows, next_after). next_after is the last row's PublicId when another page may follow, else None."""
        self._require_run(run_public_id)
        limit = max(1, min(int(limit), QUEUE_MAX_LIMIT))
        rows = self.repo.read_queue(
            run_public_id=run_public_id,
            evidence_group=group,
            project_public_id=project_public_id,
            no_project=no_project,
            bill_year=year,
            no_bill_year=no_year,
            review_status=review_status,
            after_public_id=after,
            top=limit + 1,
        )
        more = len(rows) > limit
        page = [candidate_out(r) for r in rows[:limit]]
        return page, (page[-1]["public_id"] if more and page else None)

    def _candidate_row(self, candidate_public_id: str) -> dict:
        row = self.repo.read_candidate_detail(public_id=candidate_public_id)
        if not row:
            raise CandidateNotFound()
        return row

    def _fetch(self, row: dict) -> bytes:
        """The file's bytes by the candidate's STORED SourceFileId. PdfUnavailable too_large (by the stored size, no
        download) / fetch_failed. The post-download size check is the caller's, after its own checks."""
        size = row.get("SourceFileSize")
        if size is not None and int(size) > MAX_VERIFY_BYTES:
            raise PdfUnavailable(PDF_TOO_LARGE)
        try:
            return self.fetch_bytes(str(row["SourceFileId"]))
        except Exception as exc:
            logger.warning("attachment_backfill.review.fetch_error exc=%s", type(exc).__name__)
            raise PdfUnavailable(PDF_FETCH_FAILED) from None

    def _read_text(self, row: dict) -> tuple[Optional[str], Optional[str], Optional[bool]]:
        """(text, None, matches) or (None, failure code, matches) -- b1's text path: layer, then OCR when needs_ocr.

        `matches` compares the fetched bytes with the hash b1 checked (None: no stored hash or nothing fetched).
        Snippets are never computed from content other than what b1 checked.
        """
        try:
            content = self._fetch(row)
        except PdfUnavailable as exc:
            return None, exc.code, None  # too_large / fetch_failed: the same codes as SNIPPET_*
        matches = content_matches_check(row.get("SourceSha256"), content)
        if matches is False:
            return None, SNIPPET_CONTENT_CHANGED, matches
        if len(content) > MAX_VERIFY_BYTES:
            return None, SNIPPET_TOO_LARGE, matches
        fields = dict(
            bill_number=row.get("BillNumber"), total_amount=row.get("TotalAmount"), vendor_name=row.get("VendorName")
        )
        try:
            text = self.text_layer(content)
            evidence = collect_evidence(text, **fields)
        except Exception as exc:
            logger.warning("attachment_backfill.review.extract_error exc=%s", type(exc).__name__)
            return None, SNIPPET_EXTRACT_FAILED, matches
        if needs_ocr(text, evidence):
            try:
                text = text + "\n" + self.ocr(content)
            except Exception as exc:
                logger.warning("attachment_backfill.review.ocr_error exc=%s", type(exc).__name__)
                return None, SNIPPET_OCR_FAILED, matches
        if _non_space_len(text) < OCR_MIN_TEXT_CHARS:
            return None, SNIPPET_UNREADABLE, matches
        return text, None, matches

    def candidate_detail(self, candidate_public_id: str) -> dict:
        row = self._candidate_row(candidate_public_id)
        out = candidate_out(row)
        text, error, matches = self._read_text(row)
        out["content_matches_check"] = matches
        if error is not None:
            out["snippets"], out["snippet_error"] = None, error
            return out
        try:
            out["snippets"] = collect_snippets(
                text,
                bill_number=row.get("BillNumber"),
                total_amount=row.get("TotalAmount"),
                vendor_name=row.get("VendorName"),
            )
            out["snippet_error"] = None
        except Exception as exc:
            logger.warning("attachment_backfill.review.snippet_error exc=%s", type(exc).__name__)
            out["snippets"], out["snippet_error"] = None, SNIPPET_EXTRACT_FAILED
        return out

    def candidate_pdf(self, candidate_public_id: str) -> tuple[bytes, Optional[bool]]:
        """(PDF bytes, content_matches_check), fetched by the candidate's stored SourceFileId only."""
        row = self._candidate_row(candidate_public_id)
        content = self._fetch(row)
        if len(content) > MAX_VERIFY_BYTES:
            raise PdfUnavailable(PDF_TOO_LARGE)
        if b"%PDF-" not in content[:1024]:
            raise PdfUnavailable(PDF_NOT_PDF)
        return content, content_matches_check(row.get("SourceSha256"), content)

    # -- writes ------------------------------------------------------------------------------- #

    def review(
        self,
        candidate_public_id: str,
        *,
        decision: str,
        reject_reason: Optional[str],
        row_version: bytes,
        reviewed_by_user_id: int,
    ) -> dict:
        row = self.repo.update_candidate_review(
            public_id=candidate_public_id,
            decision=decision,
            reject_reason=reject_reason,
            row_version=row_version,
            reviewed_by_user_id=reviewed_by_user_id,
        )
        return _written(row)

    def undo(self, candidate_public_id: str, *, row_version: bytes) -> dict:
        return _written(self.repo.undo_candidate_review(public_id=candidate_public_id, row_version=row_version))
