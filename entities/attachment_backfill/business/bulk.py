"""U-535b b5: the review sample and the sample-gated bulk approval.

The gate (Chris, Gate 1): a truly random sample of clean candidates (default 300) is reviewed by Chris on the b3 page and,
BLIND, by a second reviewer (Claude, the EM) from an export that carries no human decision. Only when BOTH approved EVERY
sampled candidate may the run's remaining pending clean candidates be approved in bulk -- each marked ApprovalMethod 'bulk'
with the BulkApproval id, so the whole bulk can be undone. The gate itself is checked under lock in SQL
(BulkApproveAttachmentBackfillCandidates); this module only drives the procs, validates the second-review CSV, and writes
the export.

The export (``export_sample``) is READ-ONLY: it downloads each member's PDF from Box by the STORED SourceFileId (25 MB cap,
``%PDF-`` check), compares its SHA-256 with the checked SourceSha256 (a mismatch is marked, not hidden), and writes
``<candidate PublicId>.pdf`` plus ``manifest.json``. The manifest is built from a whitelist (MANIFEST_FIELDS) and never
carries the human ReviewStatus, reviewer or reject reason -- the members proc does not even return them.

Nothing here writes Attachment, BillLineItemAttachment, Bill, blob storage or any outbox. Output and logs are counts and
ids only: no document text.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from entities.attachment_backfill.business.review import content_matches_check
from entities.attachment_backfill.business.service import MAX_VERIFY_BYTES
from entities.attachment_backfill.persistence.repo import AttachmentBackfillRepository

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_SIZE = 300
DEFAULT_BATCH_SIZE = 500
VERDICTS = ("approve", "reject")
MAX_REASON_CHARS = 200
CSV_COLUMNS = ("candidate_public_id", "verdict", "reason")
MANIFEST_NAME = "manifest.json"
UMBRELLA_DIR_NAME = "build.one"


def utf16_units(text: str) -> int:
    """Length as SQL Server NVARCHAR counts it (UTF-16 code units): an emoji is 2."""
    return len(text.encode("utf-16-le")) // 2


# Every key a manifest member may carry. Deliberately no review_status / reviewer / reject_reason (the review is blind).
MANIFEST_FIELDS = (
    "ordinal",
    "candidate_public_id",
    "bill_number",
    "bill_amount",
    "vendor_name",
    "bill_date",
    "project_name",
    "line_count",
    "bill_exists",
    "invoice_seen_count",
    "amount_seen_count",
    "vendor_seen",
    "other_invoice_label_count",
    "statement_like",
    "used_ocr",
    "pdf_file",
    "pdf_error",
    "content_matches_check",
)

FetchBytes = Callable[[str], bytes]


class SecondReviewCsvError(Exception):
    """The CSV failed validation; ``errors`` lists (line number, code) pairs. Nothing was written."""

    def __init__(self, errors: list[tuple[int, str]]):
        super().__init__(f"{len(errors)} invalid row(s)")
        self.errors = errors


@dataclass(frozen=True)
class SecondReviewRow:
    candidate_public_id: str
    verdict: str
    reason: Optional[str]


def _canonical_uuid(value: str) -> Optional[str]:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError):
        return None


def parse_second_review_csv(text: str, member_ids: set[str]) -> list[SecondReviewRow]:
    """Validate EVERY row before anything is written; SecondReviewCsvError lists each bad line by code only.

    Codes: missing_columns (header), bad_candidate_id, not_member, duplicate, bad_verdict, reason_required (reject),
    reason_too_long, empty (no data rows).
    """
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    if any(col not in header for col in CSV_COLUMNS):
        raise SecondReviewCsvError([(1, "missing_columns")])
    members = {m.lower() for m in member_ids}
    rows: list[SecondReviewRow] = []
    errors: list[tuple[int, str]] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(reader, start=2):
        record = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
        if not any(str(v).strip() for v in record.values()):
            continue  # blank line
        cid = _canonical_uuid(record.get("candidate_public_id", ""))
        verdict = record.get("verdict", "").strip().lower()
        reason = record.get("reason", "").strip() or None
        if cid is None:
            errors.append((line_no, "bad_candidate_id"))
            continue
        if cid.lower() not in members:
            errors.append((line_no, "not_member"))
            continue
        if cid.lower() in seen:
            errors.append((line_no, "duplicate"))
            continue
        seen.add(cid.lower())
        if verdict not in VERDICTS:
            errors.append((line_no, "bad_verdict"))
            continue
        if verdict == "reject" and reason is None:
            errors.append((line_no, "reason_required"))
            continue
        if reason is not None and utf16_units(reason) > MAX_REASON_CHARS:
            errors.append((line_no, "reason_too_long"))
            continue
        rows.append(SecondReviewRow(cid, verdict, reason))
    if errors:
        raise SecondReviewCsvError(errors)
    if not rows:
        raise SecondReviewCsvError([(1, "empty")])
    return rows


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def _opt_int(value) -> Optional[int]:
    return None if value is None else int(value)


def _opt_bool(value) -> Optional[bool]:
    return None if value is None else bool(value)


def manifest_member(row: dict, *, pdf_file: Optional[str], pdf_error: Optional[str],
                    content_matches_check: Optional[bool]) -> dict:
    """One manifest entry, built ONLY from MANIFEST_FIELDS (no human decision can leak in, whatever the row holds)."""
    entry = {
        "ordinal": _opt_int(row.get("Ordinal")),
        "candidate_public_id": str(row.get("CandidatePublicId")),
        "bill_number": row.get("BillNumber"),
        "bill_amount": None if row.get("TotalAmount") is None else str(row.get("TotalAmount")),
        "vendor_name": row.get("VendorName"),
        "bill_date": _iso(row.get("BillDate")),
        "project_name": row.get("ProjectName"),
        "line_count": int(row.get("LineCount") or 0),
        "bill_exists": bool(row.get("BillExists")),
        "invoice_seen_count": _opt_int(row.get("InvoiceSeenCount")),
        "amount_seen_count": _opt_int(row.get("AmountSeenCount")),
        "vendor_seen": _opt_bool(row.get("VendorSeen")),
        "other_invoice_label_count": _opt_int(row.get("OtherInvoiceLabelCount")),
        "statement_like": _opt_bool(row.get("StatementLike")),
        "used_ocr": _opt_bool(row.get("UsedOcr")),
        "pdf_file": pdf_file,
        "pdf_error": pdf_error,
        "content_matches_check": content_matches_check,
    }
    assert tuple(entry) == MANIFEST_FIELDS
    return entry


def export_dir_refusal(path: Path) -> Optional[str]:
    """Why an export directory is refused, or None. The export holds bill data and PDFs: it must never land in a git
    checkout (any ancestor, or the dir itself, holds a ``.git`` entry -- worktrees included) nor anywhere under the
    build.one umbrella (any ancestor named ``build.one``)."""
    resolved = path.expanduser().resolve()
    chain = [resolved, *resolved.parents]
    if any(p.name == UMBRELLA_DIR_NAME for p in chain):
        return "inside_build_one"
    if any((p / ".git").exists() for p in chain):
        return "inside_git_checkout"
    return None


def is_deadlock(exc: BaseException) -> bool:
    """SQL Server deadlock victim (1205 / SQLSTATE 40001), whatever wrapper the repo raised."""
    text = f"{exc} {getattr(exc, 'original', '') or ''}".lower()
    return "deadlock" in text or "1205" in text or "40001" in text


class AttachmentBackfillBulkService:
    def __init__(self, repo: Optional[AttachmentBackfillRepository] = None, *, fetch_bytes: Optional[FetchBytes] = None):
        self.repo = repo or AttachmentBackfillRepository()
        self.fetch_bytes = fetch_bytes
        self.stop_requested: Callable[[], bool] = lambda: False

    # -- sample ------------------------------------------------------------------------------- #

    def draw(self, run_id: int, *, size: int, user_id: int, apply: bool) -> dict:
        """The seed is always server-generated (CRYPT_GEN_RANDOM) and stored: no caller can choose the draw."""
        return self.repo.draw_sample(
            run_id=run_id, sample_size=size, created_by_user_id=user_id, dry_run=not apply
        ) or {"RefusedReason": "no_result"}

    def close(self, run_id: int) -> dict:
        return self.repo.close_sample(run_id=run_id) or {"RefusedReason": "no_result"}

    def status(self, run_id: int) -> Optional[dict]:
        return self.repo.read_sample_status(run_id=run_id)

    def members(self, run_id: int) -> list[dict]:
        return self.repo.read_sample_members(run_id=run_id)

    # -- blind export (read-only) ------------------------------------------------------------- #

    def _fetch_checked(self, row: dict) -> tuple[Optional[bytes], Optional[str], Optional[bool]]:
        """(content, None, matches) or (None, error code, None); codes too_large / fetch_failed / not_pdf (the b2/b4 caps)."""
        size = row.get("SourceFileSize")
        if size is not None and int(size) > MAX_VERIFY_BYTES:
            return None, "too_large", None
        try:
            content = self.fetch_bytes(str(row["SourceFileId"]))
        except Exception as exc:
            logger.warning("attachment_backfill.bulk.export_fetch_error exc=%s", type(exc).__name__)
            return None, "fetch_failed", None
        if len(content) > MAX_VERIFY_BYTES:
            return None, "too_large", None
        if b"%PDF-" not in content[:1024]:
            return None, "not_pdf", None
        matches = content_matches_check((row.get("SourceSha256") or "").strip() or None, content)
        return content, None, matches

    def export_sample(self, run_id: int, out_dir: Path, *, counts: Counter, rows: Optional[list[dict]] = None) -> Optional[str]:
        """Write the open sample's PDFs + manifest.json into out_dir; returns 'interrupted' or None.

        The manifest is written even after an interruption (members not yet fetched carry pdf_error 'not_fetched').
        """
        rows = self.members(run_id) if rows is None else rows
        counts["members"] = len(rows)
        out_dir.mkdir(parents=True, exist_ok=True)
        entries: list[dict] = []
        stop = None
        try:
            for row in rows:
                if stop is None and self.stop_requested():
                    stop = "interrupted"
                if stop is not None:
                    entries.append(manifest_member(row, pdf_file=None, pdf_error="not_fetched", content_matches_check=None))
                    counts["not_fetched"] += 1
                    continue
                content, error, matches = self._fetch_checked(row)
                pdf_file = None
                if content is not None:
                    pdf_file = f"{row['CandidatePublicId']}.pdf"
                    (out_dir / pdf_file).write_bytes(content)
                    counts["written"] += 1
                    counts["content_matches" if matches else ("content_changed" if matches is False else "no_check_hash")] += 1
                else:
                    counts[f"error_{error}"] += 1
                entries.append(manifest_member(row, pdf_file=pdf_file, pdf_error=error, content_matches_check=matches))
        finally:
            sample_public_id = str(rows[0]["SamplePublicId"]) if rows else None
            manifest = {"run_id": run_id, "sample_public_id": sample_public_id, "members": entries}
            (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return stop

    # -- second review ------------------------------------------------------------------------ #

    def record_second_reviews(self, sample_public_id: str, rows: list[SecondReviewRow], *, reviewer: str) -> dict:
        """The whole CSV in ONE all-or-nothing proc call: {Recorded, SecondMissing, BadRow, RefusedReason}.

        Once every member has a verdict the proc refuses any further write (second_review_complete): the verdicts freeze
        before the human side becomes visible. All-or-nothing means the completing write can never land half-applied.
        """
        return self.repo.record_second_reviews(
            sample_public_id=sample_public_id, rows=rows, reviewer=reviewer
        ) or {"RefusedReason": "no_result"}

    # -- bulk approval ------------------------------------------------------------------------ #

    def plan_bulk(self, run_id: int, *, batch_size: int) -> dict:
        """The gate result and what one apply would approve now (dry run: no write)."""
        return self.repo.bulk_approve(
            run_id=run_id, approved_by_user_id=None, batch_size=batch_size, dry_run=True
        ) or {"RefusedReason": "no_result"}

    def bulk_approve(self, run_id: int, *, user_id: int, batch_size: int, counts: Counter) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Loop batches until nothing remains. Returns (bulk approval PublicId, refusal code, stop reason).

        Every batch re-checks the gate under lock; a refusal mid-way stops with what was approved so far kept (each batch is
        its own transaction) -- the bulk undo reverts them all.
        """
        bulk_id: Optional[str] = None
        while True:
            if self.stop_requested():
                return bulk_id, None, "interrupted"
            kwargs = dict(run_id=run_id, approved_by_user_id=user_id, batch_size=batch_size,
                          bulk_approval_public_id=bulk_id, dry_run=False)
            try:
                out = self.repo.bulk_approve(**kwargs)
            except Exception as exc:
                # A deadlock victim's batch rolled back whole (one transaction): retry it once. Avoid web review during
                # a bulk apply -- a review click and a batch can deadlock (DESIGN §14).
                if not is_deadlock(exc):
                    raise
                counts["deadlock_retried"] += 1
                out = self.repo.bulk_approve(**kwargs)
            out = out or {"RefusedReason": "no_result"}
            refused = out.get("RefusedReason")
            if refused:
                return bulk_id, refused, None
            if out.get("BulkApprovalPublicId") is not None:
                bulk_id = str(out["BulkApprovalPublicId"])
            approved = int(out.get("Approved") or 0)
            counts["batches"] += 1
            counts["approved"] += approved
            counts["excluded_bill_missing"] = int(out.get("ExcludedBillMissing") or 0)
            counts["excluded_claimed_elsewhere"] = int(out.get("ExcludedClaimedElsewhere") or 0)
            counts["remaining"] = int(out.get("Remaining") or 0)
            if approved == 0 or counts["remaining"] == 0:
                return bulk_id, None, None

    def undo_bulk(self, bulk_approval_public_id: str, *, user_id: int, apply: bool) -> dict:
        return self.repo.undo_bulk_approval(
            bulk_approval_public_id=bulk_approval_public_id, undone_by_user_id=user_id, dry_run=not apply
        ) or {"RefusedReason": "no_result"}
