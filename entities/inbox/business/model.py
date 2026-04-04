"""
Inbox Models
============
Persistence model for InboxRecord and heuristic email classification.
"""
# Python Standard Library Imports
import re
from dataclasses import dataclass, asdict
from typing import List, Optional
import base64

from shared.classification.models import (
    ClassificationResult, ClassificationType, LABEL_MAP,
)


# ──────────────────────────────────────────────────────────────────────
# InboxRecord (persistence model — unchanged)
# ──────────────────────────────────────────────────────────────────────

@dataclass
class InboxRecord:
    id: Optional[int]                           = None
    public_id: Optional[str]                    = None
    row_version: Optional[str]                  = None
    created_datetime: Optional[str]             = None
    modified_datetime: Optional[str]            = None

    # MS Graph message ID
    message_id: Optional[str]                   = None

    # Workflow status
    status: Optional[str]                       = None

    # Submit-for-review metadata
    submitted_to_email: Optional[str]           = None
    submitted_at: Optional[str]                 = None

    # Process metadata
    processed_at: Optional[str]                 = None
    record_type: Optional[str]                  = None
    record_public_id: Optional[str]             = None

    # Classification data (ML training)
    classification_type: Optional[str]          = None
    classification_confidence: Optional[float]  = None
    classification_signals: Optional[str]       = None
    classified_at: Optional[str]                = None
    user_override_type: Optional[str]           = None

    # Email feature columns
    subject: Optional[str]                      = None
    from_email: Optional[str]                   = None
    from_name: Optional[str]                    = None
    has_attachments: Optional[bool]             = None

    # Processing channel
    processed_via: Optional[str]                = None

    # Email threading headers
    internet_message_id: Optional[str]          = None
    conversation_id: Optional[str]              = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────
# Heuristic email classification
# ──────────────────────────────────────────────────────────────────────

# Patterns compiled once at module load
_CREDIT_PATTERNS = re.compile(
    r"credit\s*(memo|note)|vendor\s*credit|cm[\s\-]?\d",
    re.IGNORECASE,
)
_BILL_PATTERNS = re.compile(
    r"invoice|bill\b|payment\s*(due|request|remittance)"
    r"|remittance|purchase\s*order|p\.?o\.?\s*#?\d"
    r"|amount\s*due|pay\s*by|net\s*\d{1,3}\b",
    re.IGNORECASE,
)
_EXPENSE_PATTERNS = re.compile(
    r"receipt|reimburse|expense\s*report|corporate\s*card",
    re.IGNORECASE,
)
_STATEMENT_PATTERNS = re.compile(
    r"statement\s*(of\s*account)?|account\s*summary"
    r"|aging\s*report|balance\s*(due|forward)",
    re.IGNORECASE,
)
_INVOICE_FILE_PATTERNS = re.compile(
    r"inv(oice)?[\s_\-]?\d|bill[\s_\-]?\d|po[\s_\-]?\d",
    re.IGNORECASE,
)


def classify_email_heuristic(
    subject: str = "",
    sender: str = "",
    body_preview: str = "",
    attachment_filenames: Optional[List[str]] = None,
) -> ClassificationResult:
    """
    Fast, rule-based email classification using subject, sender,
    attachment filenames, and body preview. No AI calls.

    Returns a ClassificationResult with moderate confidence (0.3–0.7).
    Designed as the fast path for list_inbox() and as fallback for
    the Claude classifier.
    """
    signals: List[str] = []
    scores = {t: 0.0 for t in ClassificationType if t != ClassificationType.UNKNOWN}

    subject_lower = (subject or "").lower()
    body_lower = (body_preview or "").lower()
    filenames = attachment_filenames or []
    filenames_joined = " ".join(filenames).lower()
    has_attachments = len(filenames) > 0
    has_pdf = any(f.lower().endswith(".pdf") for f in filenames)

    # ── Credit detection (check first — overrides bill) ──
    if _CREDIT_PATTERNS.search(subject):
        scores[ClassificationType.BILL_CREDIT_DOCUMENT] += 0.5
        signals.append("subject contains credit memo keywords")
    if _CREDIT_PATTERNS.search(body_lower):
        scores[ClassificationType.BILL_CREDIT_DOCUMENT] += 0.3
        signals.append("body contains credit memo keywords")

    # ── Bill / invoice detection ──
    if _BILL_PATTERNS.search(subject):
        scores[ClassificationType.BILL_DOCUMENT] += 0.4
        signals.append("subject contains invoice/bill keywords")
    if _BILL_PATTERNS.search(body_lower):
        scores[ClassificationType.BILL_DOCUMENT] += 0.2
        signals.append("body contains invoice/bill keywords")
    if has_pdf:
        scores[ClassificationType.BILL_DOCUMENT] += 0.2
        signals.append("has PDF attachment")
    if _INVOICE_FILE_PATTERNS.search(filenames_joined):
        scores[ClassificationType.BILL_DOCUMENT] += 0.3
        signals.append("attachment filename matches invoice pattern")

    # ── Expense detection ──
    if _EXPENSE_PATTERNS.search(subject):
        scores[ClassificationType.EXPENSE] += 0.5
        signals.append("subject contains expense/receipt keywords")
    if _EXPENSE_PATTERNS.search(body_lower):
        scores[ClassificationType.EXPENSE] += 0.3
        signals.append("body contains expense/receipt keywords")

    # ── Statement detection ──
    if _STATEMENT_PATTERNS.search(subject):
        scores[ClassificationType.STATEMENT] += 0.5
        signals.append("subject contains statement keywords")
    if _STATEMENT_PATTERNS.search(body_lower):
        scores[ClassificationType.STATEMENT] += 0.3
        signals.append("body contains statement keywords")

    # ── Pick the winner ──
    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]

    if best_score < 0.2:
        # Nothing matched with any confidence
        if has_attachments:
            best_type = ClassificationType.BILL_DOCUMENT
            best_score = 0.3
            signals.append("defaulting to bill (has attachments, no keyword match)")
        else:
            best_type = ClassificationType.INQUIRY
            best_score = 0.3
            signals.append("defaulting to inquiry (no attachments, no keyword match)")

    # Clamp confidence to heuristic range
    confidence = min(best_score, 0.7)

    return ClassificationResult(
        message_type=best_type,
        classification=best_type.value,
        confidence=confidence,
        signals=signals,
        suggested_label=LABEL_MAP[best_type],
    )
