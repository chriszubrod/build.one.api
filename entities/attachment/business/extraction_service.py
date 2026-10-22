"""AttachmentExtractionService (U-187, grouped and claim-based in U-535a).

Runs text extraction against dbo.Attachment rows and persists the result into the
extraction fields (ExtractionStatus / ExtractedTextBlobUrl / ExtractedDatetime) plus
the sync-proof VendorInvoiceNumber column.

Design goals:

- **Text-layer first, DI only for image-only scans.** `pypdf` reads a real text layer
  for free; only a raster/image-only scan falls through to Azure Document
  Intelligence (`prebuilt-read`).
- **Claimed before work.** Each row is claimed (status 'processing' with a lease) right
  before its OCR, so an abandoned request can't cause the next tick to re-OCR the same
  rows. A claimed row ends 'completed' or 'failed', or its lease expires and it is
  reclaimed; terminal writes are guarded by the claim token.
- **Failure-isolated, fixed codes.** One bad row never aborts the tick. Failures record
  a fixed code plus at most the exception class name, never exception text or
  document content, in ExtractionError, logs and the tick response.
- **Invoice number only where it belongs.** The number is parsed only for attachments
  linked to a Bill, BillCredit or Expense line and never for a W-9 (`TaxpayerAttachment`).
  The claim procedure computes that flag.
- **TIN-safe extraction JSON.** Persisted blob text and key-value pairs are masked for
  taxpayer ids before upload (W-9s: every digit; other documents: SSN and EIN
  shapes); the JSON never stores an unmasked taxpayer-id pattern.
- **Idempotent.** A row already 'completed' against the same file hash is skipped.
"""
from __future__ import annotations

import io
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import config
from entities.attachment.business.extraction_envelope import (
    EXTRACTION_BLOB_KEYS,
    EXTRACTION_KVP_KEYS,
    REDACTION_VERSION,
    extraction_payload_is_current,
)
from entities.attachment.business.model import Attachment
from entities.attachment.business.text_token_hash_service import (
    persist_token_hashes_failure_isolated,
)
from entities.attachment.business.tin_redaction import mask_tins, mask_tins_in_kvps
from entities.attachment.persistence.repo import AttachmentRepository
from integrations.azure.document_intelligence.external.client import (
    DocumentIntelligenceConfigError,
    DocumentIntelligenceError,
)
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)


# Scope is by link table (see ClaimAttachmentsForExtraction), not category.
# Each group is opt-in via ATTACHMENT_EXTRACTION_GROUPS.
VALID_EXTRACTION_GROUPS = frozenset({"bill", "bill_credit", "expense", "w9", "coi", "license"})
_GROUP_BY_KEY = {re.sub(r"[\s_-]", "", g): g for g in VALID_EXTRACTION_GROUPS}

_CLAIM_LEASE_MINUTES = 15

# Below this many non-whitespace characters the PDF text layer is treated as
# absent (an image-only scan) and we fall through to Document Intelligence.
_MIN_TEXT_LAYER_CHARS = 24

_EXTRACTION_BLOB_PREFIX = "attachment-extractions"


def parse_extraction_groups(raw: Optional[str]) -> list[str]:
    """Map a comma-separated setting onto canonical group names.

    Matching ignores case, whitespace, underscores and hyphens, so "BillCredit"
    resolves to "bill_credit". Unknown names are dropped with a warning.
    """
    groups: list[str] = []
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        canonical = _GROUP_BY_KEY.get(re.sub(r"[\s_-]", "", part.lower()))
        if canonical is None:
            logger.warning("attachment_extraction.unknown_group name=%s", part.strip())
            continue
        if canonical not in groups:
            groups.append(canonical)
    return groups


class AttachmentExtractionService:
    def __init__(
        self,
        repo: Optional[AttachmentRepository] = None,
        storage: Optional[AzureBlobStorage] = None,
        di=None,
    ):
        self.repo = repo or AttachmentRepository()
        self.storage = storage or AzureBlobStorage()
        # DI is constructed lazily — the text-layer path never needs it, so a
        # DI-unconfigured environment still extracts text-bearing PDFs.
        self._di = di

    @property
    def di(self):
        if self._di is None:
            from integrations.azure.document_intelligence.business.service import (
                DocumentIntelligenceService,
            )

            self._di = DocumentIntelligenceService()
        return self._di

    # -- public API ---------------------------------------------------------- #

    def extract_pending(
        self,
        *,
        limit: int = 20,
    ) -> dict:
        """Claim and drain up to ``limit`` PDFs linked to an enabled group.

        Each row is claimed immediately before its work starts, so a row can never sit
        claimed-but-unworked while the tick runs. With no enabled group this returns
        without touching the repo. Each returned result carries only fixed fields
        (see `_extract`).
        """
        groups = parse_extraction_groups(config.Settings().attachment_extraction_groups)
        if not groups:
            return {"groups": [], "processed": 0, "results": []}

        results: list[dict] = []
        for _ in range(max(0, int(limit))):
            claimed = self.repo.claim_for_extraction(
                groups=groups,
                limit=1,
                lease_minutes=_CLAIM_LEASE_MINUTES,
            )
            if not claimed:
                break
            att = claimed[0]
            try:
                results.append(self._extract(att))
            except Exception as e:  # failure isolation: one bad row never aborts the tick
                results.append(self._fail(att, "unexpected", e))
        return {
            "groups": groups,
            "processed": len(results),
            "results": results,
        }

    # extract_by_id and extract_by_public_id never parse VendorInvoiceNumber (no W-9 knowledge, fails closed); unused in prod.
    def extract_by_id(self, attachment_id: int, *, force: bool = False) -> dict:
        attachment = self.repo.read_by_id(attachment_id)
        if not attachment:
            raise ValueError(f"Attachment not found: id={attachment_id}")
        return self._extract(attachment, force=force)

    def extract_by_public_id(self, public_id: str, *, force: bool = False) -> dict:
        attachment = self.repo.read_by_public_id(public_id)
        if not attachment:
            raise ValueError(f"Attachment not found: {public_id}")
        return self._extract(attachment, force=force)

    def extract_for_bill_drop_prefill(
        self,
        attachment: Attachment,
        *,
        is_taxpayer_document: bool,
    ) -> dict:
        """Sync extraction for bill-create drop (U-676b).

        Invoice files use standard TIN masking; taxpayer-linked files use aggressive
        masking. Reuses a current completed extraction when present; does not fight a
        background ``processing`` lease.
        """
        aggressive = bool(is_taxpayer_document)
        if not attachment.blob_url:
            return self._bill_drop_fail(attachment, "no_blob_url", aggressive=aggressive)

        reused = self._bill_drop_reusable_payload(attachment, aggressive=aggressive)
        if reused is not None:
            return self._completed_outcome_from_payload(attachment, reused, reused=True)

        fresh = self.repo.read_by_id(int(attachment.id))
        if fresh is not None:
            attachment = fresh
            reused = self._bill_drop_reusable_payload(attachment, aggressive=aggressive)
            if reused is not None:
                return self._completed_outcome_from_payload(attachment, reused, reused=True)

        status = (attachment.extraction_status or "").lower()
        if status == "processing" and self._processing_lease_fresh(attachment):
            return {
                "status": "failed",
                "reason": "processing_in_flight",
                "attachment_id": attachment.id,
            }

        outcome = self._bill_drop_extract(attachment, aggressive=aggressive)
        return self._prefill_outcome_with_text(attachment, outcome, aggressive=aggressive)

    # -- core ---------------------------------------------------------------- #

    def _extract(self, attachment: Attachment, *, force: bool = False) -> dict:
        if not attachment.blob_url:
            return self._fail(attachment, "no_blob_url")

        prior_extracted_blob_url = attachment.extracted_text_blob_url

        # Idempotent skip: already completed against this exact content + redaction version.
        if not force and self._already_extracted_same_content(attachment):
            return {
                "status": "skipped",
                "reason": "unchanged",
                "attachment_id": attachment.id,
            }

        try:
            content, metadata = self.storage.download_file(attachment.blob_url)
        except Exception as e:
            return self._fail(attachment, "blob_download_error", e)

        content_type = (
            (metadata or {}).get("content_type")
            or attachment.content_type
            or "application/pdf"
        )

        # 1) Text layer first (free, no DI).
        text, pages_count = _extract_text_layer(content, content_type)
        method = "text_layer"
        key_value_pairs: list[dict] = []

        # 2) DI fallback ONLY for image-only scans (no usable text layer).
        if not _has_usable_text(text):
            try:
                di_result = self._run_di(content, content_type)
            except DocumentIntelligenceConfigError as e:
                return self._fail(attachment, "di_config_error", e)
            except DocumentIntelligenceError as e:
                return self._fail(attachment, "di_error", e)
            text = di_result["content"]
            pages_count = di_result["pages_count"]
            key_value_pairs = di_result["key_value_pairs"]
            method = "document_intelligence_read"

        # Written straight through: the setter's CASE-WHEN keeps the existing value when
        # this is NULL, so a parse miss never wipes a good number. Not routed through
        # preserve_human_edited_ref (QBO-ref-specific). FOLLOW-UP: protecting a genuine
        # operator correction across re-extractions needs a human-confirmed flag.
        parsed_number = (
            parse_vendor_invoice_number(key_value_pairs, text)
            if attachment.parse_invoice_number
            else None
        )

        try:
            blob_name, blob_url, extraction_payload = self._persist_extraction_blob(
                attachment, text, key_value_pairs, pages_count, method
            )
        except Exception as e:
            return self._fail(attachment, "persist_error", e)

        outcome = self._commit_terminal(
            attachment,
            {
                "status": "completed",
                "attachment_id": attachment.id,
                "method": method,
                "pages_count": pages_count,
            },
            extraction_status="completed",
            extracted_text_blob_url=blob_url,
            vendor_invoice_number=parsed_number,
            orphan_blob=(blob_name, blob_url),
        )
        self._maybe_persist_token_hashes(
            attachment,
            outcome,
            extraction_payload,
            is_taxpayer_document=attachment.is_taxpayer_document,
        )
        if (
            outcome.get("status") == "completed"
            and prior_extracted_blob_url
            and prior_extracted_blob_url != blob_url
        ):
            self._delete_blob_quietly(prior_extracted_blob_url, "prior_blob_delete_failed id=%s", attachment.id)
        return outcome

    def _bill_drop_extract(
        self,
        attachment: Attachment,
        *,
        aggressive: bool,
    ) -> dict:
        """Bill-drop sync extraction (masking + invoice number); not used by the sweep.

        Only MASKED text ever leaves this method: a reused blob's content, or the text
        returned by the masking writer. Never the raw text layer / DI output."""
        if not attachment.blob_url:
            return self._bill_drop_fail(attachment, "no_blob_url", aggressive=aggressive)

        prior_extracted_blob_url = attachment.extracted_text_blob_url
        reused = self._bill_drop_reusable_payload(attachment, aggressive=aggressive)
        if reused is not None:
            return self._completed_outcome_from_payload(attachment, reused, reused=True)

        try:
            content, metadata = self.storage.download_file(attachment.blob_url)
        except Exception as e:
            return self._bill_drop_fail(attachment, "blob_download_error", e, aggressive=aggressive)

        content_type = (
            (metadata or {}).get("content_type")
            or attachment.content_type
            or "application/pdf"
        )
        text, pages_count = _extract_text_layer(content, content_type)
        method = "text_layer"
        key_value_pairs: list[dict] = []
        if not _has_usable_text(text):
            try:
                di_result = self._run_di(content, content_type)
            except DocumentIntelligenceConfigError as e:
                return self._bill_drop_fail(attachment, "di_config_error", e, aggressive=aggressive)
            except DocumentIntelligenceError as e:
                return self._bill_drop_fail(attachment, "di_error", e, aggressive=aggressive)
            text = di_result["content"]
            pages_count = di_result["pages_count"]
            key_value_pairs = di_result["key_value_pairs"]
            method = "document_intelligence_read"

        parsed_number = parse_vendor_invoice_number(key_value_pairs, text)

        try:
            persisted = self._persist_bill_drop_extraction_blob(
                attachment,
                text,
                key_value_pairs,
                pages_count,
                method,
                aggressive=aggressive,
            )
            blob_name, blob_url, masked_text, extraction_payload = persisted
        except Exception as e:
            return self._bill_drop_fail(attachment, "persist_error", e, aggressive=aggressive)

        outcome = self._commit_terminal(
            attachment,
            {
                "status": "completed",
                "attachment_id": attachment.id,
                "method": method,
                "pages_count": pages_count,
            },
            extraction_status="completed",
            extracted_text_blob_url=blob_url,
            vendor_invoice_number=parsed_number,
            orphan_blob=(blob_name, blob_url),
        )
        self._maybe_persist_token_hashes(
            attachment,
            outcome,
            extraction_payload,
            is_taxpayer_document=aggressive,
        )
        if (
            outcome.get("status") == "completed"
            and prior_extracted_blob_url
            and prior_extracted_blob_url != blob_url
        ):
            self._delete_blob_quietly(
                prior_extracted_blob_url,
                "prior_blob_delete_failed id=%s",
                attachment.id,
            )
        if outcome.get("reason") == "claim_lost":
            refetched = self.repo.read_by_id(int(attachment.id))
            if refetched is not None:
                reused = self._bill_drop_reusable_payload(refetched, aggressive=aggressive)
                if reused is not None:
                    return self._completed_outcome_from_payload(refetched, reused, reused=True)
        if outcome.get("status") == "completed":
            outcome = dict(outcome)
            outcome["masked_text"] = masked_text
            outcome["redaction_version"] = REDACTION_VERSION
            outcome["extracted_text_blob_url"] = blob_url
        return outcome

    def _completed_outcome_from_payload(
        self,
        attachment: Attachment,
        prior: dict[str, Any],
        *,
        reused: bool = False,
    ) -> dict:
        text = prior.get("content") or ""
        if not _has_usable_text(text):
            return {"status": "failed", "reason": "no_text", "attachment_id": attachment.id}
        outcome: dict[str, Any] = {
            "status": "completed",
            "attachment_id": attachment.id,
            "method": prior.get("method"),
            "masked_text": text,
            "redaction_version": prior.get("redaction_version"),
            "extracted_text_blob_url": attachment.extracted_text_blob_url,
        }
        if reused:
            outcome["reused"] = True
        return outcome

    def _run_di(self, content: bytes, content_type: str) -> dict:
        extracted = self.di.extract_text(content, content_type=content_type)
        return {
            "content": extracted.get("content") or "",
            "pages_count": int(extracted.get("pages_count") or 0),
            "key_value_pairs": extracted.get("key_value_pairs") or [],
        }

    def _persist_extraction_blob(
        self,
        attachment: Attachment,
        text: str,
        key_value_pairs: list[dict],
        pages_count: int,
        method: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """Mask taxpayer ids, then upload. The ONLY writer of extraction JSON, so the
        redaction_version stamp is always true of what it stamps."""
        aggressive = attachment.is_taxpayer_document is not False  # unknown => fail closed
        text, tins_masked = mask_tins(text, aggressive=aggressive)
        key_value_pairs, kvp_count = mask_tins_in_kvps(key_value_pairs, aggressive=aggressive)
        tins_masked += kvp_count
        if tins_masked > 0:
            logger.info("attachment_extraction.tins_masked id=%s count=%s", attachment.id, tins_masked)
        payload = {
            "attachment_public_id": str(attachment.public_id),
            "source_file_hash": attachment.file_hash,
            "redaction_version": REDACTION_VERSION,
            "method": method,
            "pages_count": pages_count,
            "content": text or "",
            "key_value_pairs": [
                {
                    "key": kvp.get("key"),
                    "value": kvp.get("value"),
                    "confidence": (
                        str(kvp.get("confidence"))
                        if kvp.get("confidence") is not None
                        else None
                    ),
                }
                for kvp in key_value_pairs
            ],
        }
        # Unique per write: a stale worker must never overwrite the blob a newer claim published.
        blob_name = f"{_EXTRACTION_BLOB_PREFIX}/{attachment.public_id}/{uuid.uuid4().hex}.json"
        data = json.dumps(payload).encode("utf-8")
        blob_url = self.storage.upload_file(
            blob_name=blob_name,
            file_content=data,
            content_type="application/json",
        )
        return blob_name, blob_url, payload

    def _persist_bill_drop_extraction_blob(
        self,
        attachment: Attachment,
        text: str,
        key_value_pairs: list[dict],
        pages_count: int,
        method: str,
        *,
        aggressive: bool,
    ) -> tuple[str, str, str, dict[str, Any]]:
        """Mask, upload, and return (blob_name, blob_url, masked_text, payload)."""
        text, tins_masked = mask_tins(text, aggressive=aggressive)
        key_value_pairs, kvp_count = mask_tins_in_kvps(key_value_pairs, aggressive=aggressive)
        tins_masked += kvp_count
        if tins_masked > 0:
            logger.info("attachment_extraction.tins_masked id=%s count=%s", attachment.id, tins_masked)
        payload = {
            "attachment_public_id": str(attachment.public_id),
            "source_file_hash": attachment.file_hash,
            "redaction_version": REDACTION_VERSION,
            "method": method,
            "pages_count": pages_count,
            "content": text or "",
            "key_value_pairs": [
                {
                    "key": kvp.get("key"),
                    "value": kvp.get("value"),
                    "confidence": (
                        str(kvp.get("confidence"))
                        if kvp.get("confidence") is not None
                        else None
                    ),
                }
                for kvp in key_value_pairs
            ],
            "masking": "aggressive" if aggressive else "standard",
        }
        blob_name = f"{_EXTRACTION_BLOB_PREFIX}/{attachment.public_id}/{uuid.uuid4().hex}.json"
        data = json.dumps(payload).encode("utf-8")
        blob_url = self.storage.upload_file(
            blob_name=blob_name,
            file_content=data,
            content_type="application/json",
        )
        return blob_name, blob_url, text or "", payload

    def _maybe_persist_token_hashes(
        self,
        attachment: Attachment,
        outcome: dict,
        envelope: Optional[dict[str, Any]],
        *,
        is_taxpayer_document: Optional[bool],
    ) -> None:
        if outcome.get("status") != "completed" or envelope is None:
            return
        taxpayer = is_taxpayer_document
        if taxpayer is None:
            try:
                _linked, is_taxpayer = self.repo.read_link_state(int(attachment.id))
                taxpayer = is_taxpayer
            except Exception as error:
                logger.warning(
                    "attachment_extraction.taxpayer_resolve_failed id=%s err=%s",
                    attachment.id,
                    type(error).__name__,
                )
                taxpayer = True
        if taxpayer:
            return

        persist_token_hashes_failure_isolated(
            attachment_id=int(attachment.id),
            envelope=envelope,
        )

    def _already_extracted_same_content(self, attachment: Attachment) -> bool:
        """True when a prior extraction blob was written for this exact file hash.

        Only consulted for rows still marked 'completed' (a trigger re-marks
        'pending' on any content change), so this never re-downloads a blob during
        a normal sweep. Any read/parse error → re-extract (returns False)."""
        if (attachment.extraction_status or "").lower() != "completed":
            return False
        if not attachment.extracted_text_blob_url or not attachment.file_hash:
            return False
        try:
            content, _ = self.storage.download_file(attachment.extracted_text_blob_url)
            prior = json.loads(content)
        except Exception:
            return False
        return extraction_payload_is_current(prior) and prior.get("source_file_hash") == attachment.file_hash

    def _bill_drop_reusable_payload(
        self,
        attachment: Attachment,
        *,
        aggressive: bool,
    ) -> Optional[dict[str, Any]]:
        if (attachment.extraction_status or "").lower() != "completed":
            return None
        if not attachment.extracted_text_blob_url or not attachment.file_hash:
            return None
        try:
            content, _ = self.storage.download_file(attachment.extracted_text_blob_url)
            prior = json.loads(content)
        except Exception:
            return None
        if not (
            extraction_payload_is_current(prior)
            and prior.get("source_file_hash") == attachment.file_hash
        ):
            return None
        if not self._blob_masking_matches(prior, aggressive=aggressive):
            return None
        return prior

    @staticmethod
    def _blob_masking_matches(prior: dict, *, aggressive: bool) -> bool:
        """May a caller needing ``aggressive`` masking reuse this blob?

        Every current blob masks at least the standard SSN/EIN shapes, so a standard
        caller may reuse any of them. A blob without a ``masking`` key was written by the
        background path, which masks standard OR aggressive depending on the row — so an
        aggressive caller may reuse only a blob explicitly marked aggressive."""
        if not aggressive:
            return True
        return prior.get("masking") == "aggressive"

    def _processing_lease_fresh(self, attachment: Attachment) -> bool:
        stamp = _coerce_attachment_timestamp(
            attachment.modified_datetime or attachment.claim_modified_datetime
        )
        if stamp is None:
            return False
        age = datetime.now(timezone.utc) - stamp
        return age < timedelta(minutes=_CLAIM_LEASE_MINUTES)

    def _prefill_outcome_with_text(
        self, attachment: Attachment, outcome: dict, *, aggressive: bool
    ) -> dict:
        if outcome.get("status") != "completed":
            return outcome
        masked = outcome.get("masked_text")
        if masked is None:
            reused = self._bill_drop_reusable_payload(attachment, aggressive=aggressive)
            if reused is not None:
                return self._completed_outcome_from_payload(attachment, reused)
            return {"status": "failed", "reason": "blob_read", "attachment_id": attachment.id}
        outcome = dict(outcome)
        if "redaction_version" not in outcome:
            outcome["redaction_version"] = REDACTION_VERSION
        if "extracted_text_blob_url" not in outcome:
            outcome["extracted_text_blob_url"] = attachment.extracted_text_blob_url
        return outcome

    def _bill_drop_fail(
        self,
        attachment: Attachment,
        code: str,
        exc: Optional[BaseException] = None,
        *,
        aggressive: bool = True,
    ) -> dict:
        try:
            refetched = self.repo.read_by_id(int(attachment.id))
        except Exception:
            refetched = None
        if refetched is not None and (
            (refetched.extraction_status or "").lower() == "completed"
        ):
            reused = self._bill_drop_reusable_payload(refetched, aggressive=aggressive)
            if reused is not None:
                return self._completed_outcome_from_payload(refetched, reused, reused=True)
            return {
                "status": "completed",
                "attachment_id": attachment.id,
            }
        return self._fail(attachment, code, exc)

    def _fail(self, attachment: Attachment, code: str, exc: Optional[BaseException] = None) -> dict:
        """Mark the row failed with a fixed code (+ exception class name only) and return the tick row."""
        exc_name = type(exc).__name__ if exc is not None else None
        error = code if exc_name is None else f"{code}:{exc_name}"
        logger.error("attachment_extraction.failed id=%s code=%s exc=%s", attachment.id, code, exc_name)
        return self._commit_terminal(
            attachment,
            {"status": "failed", "reason": code, "attachment_id": attachment.id},
            extraction_status="failed",
            extraction_error=error,
        )

    def _commit_terminal(
        self,
        attachment: Attachment,
        outcome: dict,
        *,
        orphan_blob: Optional[tuple[str, str]] = None,
        **fields,
    ) -> dict:
        """Write a terminal status guarded by the claim token, then return `outcome`.

        Only the lease holder may finish a row. A write that matches nothing means the
        lease was reclaimed, so the row is reported as claim_lost and left alone, and
        `orphan_blob` (the blob this attempt just wrote, provably never published) is
        deleted. A write that RAISES has an unknown outcome — the commit may have landed
        before the acknowledgement was lost — so the blob is KEPT: the row may now
        reference it. If it did not commit, the row stays 'processing' for the lease to
        reclaim and the kept blob is an unreferenced orphan (storage debt, never current).
        """
        guard = {}
        if attachment.claim_modified_datetime is not None:
            guard = {
                "expected_status": "processing",
                "expected_modified_datetime": attachment.claim_modified_datetime,
            }
        try:
            written = self.repo.update_extraction(id=attachment.id, **fields, **guard)
        except Exception as e:
            logger.error(
                "attachment_extraction.terminal_write_failed id=%s exc=%s",
                attachment.id,
                type(e).__name__,
            )
            # Outcome unknown: never delete a blob the committed row may reference.
            return {"status": "failed", "reason": "terminal_write_failed", "attachment_id": attachment.id}
        if written is None:
            logger.warning("attachment_extraction.claim_lost id=%s", attachment.id)
            self._discard_blob(orphan_blob)
            return {"status": "skipped", "reason": "claim_lost", "attachment_id": attachment.id}
        return outcome

    def _discard_blob(self, orphan_blob: Optional[tuple[str, str]]) -> None:
        if orphan_blob is None:
            return
        blob_name, blob_url = orphan_blob
        self._delete_blob_quietly(blob_url, "orphan_blob_delete_failed blob=%s", blob_name)

    def _delete_blob_quietly(self, blob_url: str, event: str, ref) -> None:
        """Best-effort delete; a failure logs the event, its ref and the exception class only."""
        try:
            self.storage.delete_file(blob_url)
        except Exception as e:
            logger.warning("attachment_extraction." + event + " exc=%s", ref, type(e).__name__)


# --------------------------------------------------------------------------- #
# Pure helpers (module-level so they are unit-testable without I/O)
# --------------------------------------------------------------------------- #

def _coerce_attachment_timestamp(stamp: object) -> Optional[datetime]:
    """Parse sproc ModifiedDatetime (naive UTC string) or tolerate datetime."""
    if stamp is None:
        return None
    if isinstance(stamp, datetime):
        dt = stamp
    elif isinstance(stamp, str):
        try:
            dt = datetime.strptime(stamp.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# CLI / script entry points do not import app.py; keep pypdf silenced via shared helper.
from shared.observability import silence_pypdf_loggers

silence_pypdf_loggers()


def _looks_like_pdf(content: bytes) -> bool:
    return bool(content) and content[:5] == b"%PDF-"


def _extract_text_layer(content: bytes, content_type: str) -> tuple[str, int]:
    """Return (text, pages_count) from a PDF's text layer via pypdf.

    Only attempts PDFs (by content-type or magic bytes); images and other types
    return ("", 0) so the caller falls through to DI. Never raises — a malformed
    PDF returns whatever pages parsed (possibly empty), which drives the DI
    fallback naturally.
    """
    is_pdf = (content_type or "").strip().lower() == "application/pdf" or _looks_like_pdf(content)
    if not is_pdf:
        return "", 0
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                parts.append("")
        return "\n".join(parts), len(reader.pages)
    except Exception as e:
        logger.warning("attachment_extraction.text_layer_error exc=%s", type(e).__name__)
        return "", 0


def _has_usable_text(text: Optional[str]) -> bool:
    if not text:
        return False
    stripped = re.sub(r"\s+", "", text)
    return len(stripped) >= _MIN_TEXT_LAYER_CHARS


# Keys that carry a vendor invoice / reference number in DI key-value output.
def _key_is_invoice_number(key: Optional[str]) -> bool:
    k = (key or "").strip().lower().rstrip(":").strip()
    if not k:
        return False
    if "invoice" in k and any(t in k for t in ("#", "no", "number", "num", "ref")):
        return True
    return k in {
        "invoice #",
        "invoice no",
        "invoice no.",
        "invoice number",
        "invoice num",
        "invoice",
        "inv #",
        "inv no",
        "inv no.",
        "inv #:",
        "ref",
        "ref #",
        "ref no",
        "ref no.",
        "reference",
        "reference #",
        "reference no",
        "reference number",
    }


_INVOICE_TEXT_RE = re.compile(
    r"(?:invoice|inv)\s*(?:#|no\.?|number|num)?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{1,})",
    re.IGNORECASE,
)


def _parse_number_from_text(text: Optional[str]) -> Optional[str]:
    for m in _INVOICE_TEXT_RE.finditer(text or ""):
        candidate = (m.group(1) or "").strip().strip("-/").strip()
        # Require a digit so "Invoice Date" / "Invoice To" don't false-positive.
        if candidate and any(c.isdigit() for c in candidate):
            return candidate
    return None


def parse_vendor_invoice_number(
    key_value_pairs: Optional[list[dict]],
    content_text: Optional[str],
) -> Optional[str]:
    """Extract the vendor invoice number from DI key-value pairs, falling back to
    a regex over the raw text (the text-layer path has no key-value pairs). Pure;
    no I/O."""
    for kvp in key_value_pairs or []:
        if _key_is_invoice_number(kvp.get("key")):
            value = (kvp.get("value") or "").strip()
            if value:
                return value
    return _parse_number_from_text(content_text)
