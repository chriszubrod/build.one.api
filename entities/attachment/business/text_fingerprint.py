"""Document-text fingerprint for bill duplicate detection (U-723 Rule B).

Normalizer version ``u723-v1`` — golden-tested in tests/test_u723_text_fingerprint.py.
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from typing import Optional

from entities.attachment.business.extraction_service import _looks_like_pdf
from entities.attachment.business.tin_redaction import mask_tins

FINGERPRINT_VERSION = "u723-v1"
_MIN_NORMALIZED_CHARS = 200
_MAX_PDF_PAGES = 10

_URL_SCHEME_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_URL_WWW_RE = re.compile(r"www\.\S+", re.IGNORECASE)
_OPAQUE_TOKEN_RE = re.compile(r"[a-z0-9_-]{20,}")
_CLOCK_TIME_RE = re.compile(
    r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:\s*[ap]\.?m\.?)?\b",
    re.IGNORECASE,
)
# Slash tokens are URL/path noise except calendar dates (Rule B keeps dates).
_DATE_TOKEN_RE = re.compile(
    r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}-\d{2,4})$"
)


def normalize_document_text(text: str) -> str:
    """Pure normalizer for u723-v1 (no I/O)."""
    if not text:
        return ""
    masked, _count = mask_tins(text, aggressive=False)
    normalized = unicodedata.normalize("NFKC", masked).lower()
    normalized = _URL_SCHEME_RE.sub(" ", normalized)
    normalized = _URL_WWW_RE.sub(" ", normalized)
    normalized = _CLOCK_TIME_RE.sub(" ", normalized)
    tokens: list[str] = []
    for raw in normalized.split():
        if ".com" in raw:
            continue
        if "/" in raw and not _DATE_TOKEN_RE.fullmatch(raw):
            continue
        if _OPAQUE_TOKEN_RE.fullmatch(raw):
            continue
        tokens.append(raw)
    return re.sub(r"\s+", " ", " ".join(tokens)).strip()


def fingerprint_document_text(text: str) -> Optional[str]:
    """Return sha256 hex of normalized text, or None when normalized length < 200."""
    normalized = normalize_document_text(text)
    if len(normalized) < _MIN_NORMALIZED_CHARS:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _extract_pdf_text_capped(content: bytes, content_type: str) -> str:
    """Text layer from stored PDF bytes; at most ``_MAX_PDF_PAGES`` pages. Never raises."""
    is_pdf = (content_type or "").strip().lower() == "application/pdf" or _looks_like_pdf(content)
    if not is_pdf:
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        parts: list[str] = []
        for page in reader.pages[:_MAX_PDF_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                parts.append("")
        return "\n".join(parts)
    except Exception:
        return ""


def fingerprint_pdf_bytes(content: bytes, *, content_type: str = "application/pdf") -> Optional[str]:
    """Fingerprint PDF bytes after text-layer extraction. Never raises."""
    try:
        text = _extract_pdf_text_capped(content, content_type)
        return fingerprint_document_text(text)
    except Exception:
        return None
