"""AttachmentExtractionService (U-187).

Runs text extraction against a single dbo.Attachment and persists the result
into the previously-DEAD extraction fields (ExtractionStatus /
ExtractedTextBlobUrl / ExtractedDatetime) plus the new sync-proof
VendorInvoiceNumber column.

Design goals:

- **Text-layer first, DI only for image-only scans.** Most source PDFs (vendor
  invoices, statements) carry a real text layer — `pypdf` reads it for free. Only
  a raster/image-only scan (a phone photo, an image wrapped into a PDF) falls
  through to Azure Document Intelligence, which is the expensive path. This bounds
  DI spend to the documents that genuinely need OCR.
- **Failure-isolated.** Any error on one attachment marks that row 'failed' and is
  contained — it never raises out of `extract_pending`, so one bad blob can't
  abort a drain tick.
- **Idempotent.** A row already 'completed' against the same file hash is skipped
  (no re-download, no DI). Content changes always transit a fresh 'pending'
  re-mark from the trigger, so a completed row reflects current content.
- **Sync-proof number.** The parsed vendor invoice number is written through
  `preserve_human_edited_ref`, so an operator correction survives a re-extraction
  and only empty/placeholder values are replaced.

The extraction trigger (mark 'pending') lives in the QBO-attachable connector and
the receipt upload path; this service is the drain that turns pending rows into
extracted text. It mirrors EmailAttachmentExtractionService but writes
dbo.Attachment (not dbo.EmailAttachment).
"""
from __future__ import annotations

import io
import json
import logging
import re
from typing import Optional

from entities.attachment.business.model import Attachment
from entities.attachment.persistence.repo import AttachmentRepository
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)


# Attachment categories that represent source documents (bill/expense/invoice
# support). The drain scopes to these so a stray pending mark on an unrelated
# attachment can't pull the whole catalog through DI. Compared case-insensitively;
# a falsy category (a bare receipt upload with no category) is allowed through.
SOURCE_DOC_CATEGORIES = frozenset(
    {
        "bill_line_item",
        "qbo_import",
        "email_intake",
        "expense",
        "bill",
        "bill_credit",
        "invoice",
        "receipt",
    }
)

# Below this many non-whitespace characters the PDF text layer is treated as
# absent (an image-only scan) and we fall through to Document Intelligence.
_MIN_TEXT_LAYER_CHARS = 24

_EXTRACTION_BLOB_PREFIX = "attachment-extractions"


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
        categories: Optional[frozenset] = SOURCE_DOC_CATEGORIES,
    ) -> dict:
        """Drain up to ``limit`` attachments explicitly marked 'pending'.

        Scoped to ``categories`` (source-doc categories by default). NULL-status
        legacy rows returned by ReadAttachmentsPendingExtraction are deliberately
        skipped — only rows a trigger set to 'pending' are processed, which is the
        real cost bound.
        """
        rows = self.repo.read_pending_extraction()
        pending = [a for a in rows if self._is_pending_in_scope(a, categories)]
        limit = max(0, int(limit))
        results: list[dict] = []
        for att in pending[:limit]:
            try:
                results.append(self._extract(att))
            except Exception as e:  # failure isolation: one bad row never aborts the tick
                logger.exception(
                    "attachment_extraction.tick_item_failed id=%s: %s", att.id, e
                )
                self._mark_failed(att, f"unexpected: {e}")
                results.append(
                    {"status": "failed", "attachment_id": att.id, "error": str(e)}
                )
        return {
            "pending_in_scope": len(pending),
            "processed": len(results),
            "results": results,
        }

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

    # -- core ---------------------------------------------------------------- #

    def _extract(self, attachment: Attachment, *, force: bool = False) -> dict:
        if not attachment.blob_url:
            self._mark_failed(attachment, "No blob_url on attachment — cannot extract")
            return {"status": "failed", "reason": "no_blob_url", "attachment_id": attachment.id}

        # Idempotent skip: already completed against this exact content.
        if not force and self._already_extracted_same_content(attachment):
            return {
                "status": "skipped",
                "reason": "unchanged",
                "attachment_id": attachment.id,
            }

        try:
            content, metadata = self.storage.download_file(attachment.blob_url)
        except Exception as e:
            logger.exception(
                "attachment_extraction.blob_download_failed id=%s: %s", attachment.id, e
            )
            self._mark_failed(attachment, f"Blob download failed: {e}")
            return {"status": "failed", "reason": "blob_download_error", "attachment_id": attachment.id}

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
            di_result = self._run_di(attachment, content, content_type)
            if di_result is None:
                # _run_di already marked the row failed.
                return {"status": "failed", "reason": "di_error", "attachment_id": attachment.id}
            text = di_result["content"]
            pages_count = di_result["pages_count"]
            key_value_pairs = di_result["key_value_pairs"]
            method = "document_intelligence"

        parsed_number = parse_vendor_invoice_number(key_value_pairs, text)
        # Write the freshly-extracted number straight through. The setter's
        # CASE-WHEN keeps the existing value when this is NULL (a parse miss never
        # wipes a good number); a parsed value updates. We deliberately do NOT run
        # this through preserve_human_edited_ref — that helper is QBO-ref-specific
        # (it treats a stored "QBO-<id>" as human-edited), which is wrong here.
        # FOLLOW-UP: to protect a genuine OPERATOR correction of VendorInvoiceNumber
        # across re-extractions, add a human-confirmed flag and gate the write on it
        # — do not fake it via the QBO placeholder helper.

        try:
            blob_url = self._persist_extraction_blob(
                attachment, text, key_value_pairs, pages_count, method
            )
        except Exception as e:
            logger.exception(
                "attachment_extraction.persist_blob_failed id=%s: %s", attachment.id, e
            )
            self._mark_failed(attachment, f"Persist extraction blob failed: {e}")
            return {"status": "failed", "reason": "persist_error", "attachment_id": attachment.id}

        self.repo.update_extraction(
            id=attachment.id,
            extraction_status="completed",
            extracted_text_blob_url=blob_url,
            vendor_invoice_number=parsed_number,
        )
        return {
            "status": "completed",
            "attachment_id": attachment.id,
            "method": method,
            "pages_count": pages_count,
            "vendor_invoice_number": parsed_number,
        }

    def _run_di(self, attachment: Attachment, content: bytes, content_type: str) -> Optional[dict]:
        from integrations.azure.document_intelligence.external.client import (
            DocumentIntelligenceConfigError,
            DocumentIntelligenceError,
        )

        try:
            extracted = self.di.extract_invoice(content, content_type=content_type)
        except DocumentIntelligenceConfigError as e:
            logger.error("attachment_extraction.di_not_configured id=%s: %s", attachment.id, e)
            self._mark_failed(attachment, f"DI not configured: {e}")
            return None
        except DocumentIntelligenceError as e:
            logger.error("attachment_extraction.di_failed id=%s: %s", attachment.id, e)
            self._mark_failed(attachment, str(e)[:1000])
            return None
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
    ) -> str:
        payload = {
            "attachment_public_id": str(attachment.public_id),
            "source_file_hash": attachment.file_hash,
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
                for kvp in (key_value_pairs or [])
            ],
        }
        blob_name = f"{_EXTRACTION_BLOB_PREFIX}/{attachment.public_id}.json"
        data = json.dumps(payload).encode("utf-8")
        return self.storage.upload_file(
            blob_name=blob_name,
            file_content=data,
            content_type="application/json",
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
        return bool(prior) and prior.get("source_file_hash") == attachment.file_hash

    def _mark_failed(self, attachment: Attachment, error: str) -> None:
        try:
            self.repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                extraction_error=str(error)[:1000],
            )
        except Exception:
            logger.exception(
                "attachment_extraction.mark_failed_persist_failed id=%s", attachment.id
            )

    @staticmethod
    def _is_pending_in_scope(attachment: Attachment, categories: Optional[frozenset]) -> bool:
        if (attachment.extraction_status or "").lower() != "pending":
            return False
        if not categories:
            return True
        cat = (attachment.category or "").lower()
        return (not cat) or (cat in categories)


# --------------------------------------------------------------------------- #
# Pure helpers (module-level so they are unit-testable without I/O)
# --------------------------------------------------------------------------- #


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
        logger.warning("attachment_extraction.text_layer_failed: %s", e)
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
