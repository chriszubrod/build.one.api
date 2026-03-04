"""
Email Classifier
================
Classifies incoming emails in the invoice inbox into actionable types
using lightweight heuristics (subject, sender, body keywords, attachments).

No external AI calls — fast, deterministic, and auditable.
For ambiguous messages the confidence will be low and the UI will prompt
the user to confirm the classification before processing.
"""
import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MessageType(str, Enum):
    BILL = "bill"                   # Vendor invoice — create a Bill
    EXPENSE = "expense"             # Expense receipt — create an Expense
    VENDOR_CREDIT = "vendor_credit" # Credit memo — create a Vendor Credit
    VENDOR_INQUIRY = "inquiry"      # Vendor asking about payment status
    VENDOR_STATEMENT = "statement"  # Vendor account statement
    UNKNOWN = "unknown"             # Cannot determine — needs manual review


@dataclass
class ClassificationResult:
    message_type: MessageType
    confidence: float               # 0.0 – 1.0
    signals: list[str] = field(default_factory=list)  # Explanation of signals used
    suggested_label: str = ""       # Human-readable label for UI display

    def __post_init__(self):
        if not self.suggested_label:
            self.suggested_label = self.message_type.value.replace('_', ' ').title()


class EmailClassifier:
    """
    Classifies incoming invoice inbox emails into MessageType categories.

    Supports optional per-sender overrides that bypass heuristic scoring.
    When an override_service is provided and matches the sender, the
    configured type is returned with 100% confidence.

    Usage:
        classifier = EmailClassifier()
        result = classifier.classify(
            subject="Invoice 540119",
            from_email="ar@coxinterior.com",
            body="Thank you for your purchase. Invoice attached.",
            attachments=[{"name": "20260224_540119.pdf", "content_type": "application/pdf"}],
        )
        print(result.message_type)   # MessageType.BILL
        print(result.confidence)     # 0.92
    """

    def __init__(self, override_service=None):
        self._override_service = override_service

    # -----------------------------------------------------------------------
    # Keyword dictionaries
    # -----------------------------------------------------------------------

    BILL_SUBJECT_KEYWORDS = [
        'invoice', 'inv #', 'inv#', 'bill', 'statement of account',
        'amount due', 'payment due', 'please remit', 'payable',
    ]

    CREDIT_SUBJECT_KEYWORDS = [
        'credit memo', 'credit note', 'credit invoice', 'cm #', 'cm#',
        'credit', 'refund', 'adjustment', 'corrected invoice',
    ]

    STATEMENT_SUBJECT_KEYWORDS = [
        'statement', 'account statement', 'account summary',
        'past due', 'outstanding balance', 'aging report',
        'account balance', 'balance reminder',
    ]

    INQUIRY_SUBJECT_KEYWORDS = [
        'payment status', 'payment inquiry', 'check status',
        'when will', 'have you received', 'following up',
        'please advise', 'unpaid', 'overdue notice',
    ]

    EXPENSE_SUBJECT_KEYWORDS = [
        'receipt', 'your receipt', 'purchase receipt',
        'order confirmation', 'order summary', 'your order',
        'transaction receipt', 'charge', 'electronic receipt',
        'ereceipt', 'e-receipt',
    ]

    EXPENSE_BODY_KEYWORDS = [
        'thank you for your purchase', 'thank you for shopping',
        'thank you for your recent transaction', 'your receipt',
        'digital copy of your receipt', 'transaction at',
        'your purchase', 'receipt is attached', 'purchase confirmation',
    ]

    BILL_BODY_KEYWORDS = [
        'invoice', 'amount due', 'payment due', 'please pay',
        'attached as a pdf', 'invoice is attached', 'remit payment',
    ]

    CREDIT_BODY_KEYWORDS = [
        'credit memo', 'credit note', 'credit has been issued',
        'we have issued a credit', 'credit applied', 'refund',
        # Patterns seen in real vendor credit emails:
        'credit #', 'credit no', 'credit is attached', 'credit attached',
        'a credit', 'your credit',
    ]

    STATEMENT_BODY_KEYWORDS = [
        'account statement', 'summary of your account',
        'outstanding balance', 'past due balance', 'aging',
        'transactions on your account',
    ]

    INQUIRY_BODY_KEYWORDS = [
        'following up on', 'status of payment', 'payment status',
        'when can we expect', 'please confirm receipt',
        'have not received', 'still outstanding',
    ]

    # Attachment patterns that are strong signals
    INVOICE_ATTACHMENT_PATTERNS = [
        r'inv', r'invoice', r'bill', r'statement',
    ]
    CREDIT_ATTACHMENT_PATTERNS = [
        r'credit', r'cm', r'refund',
    ]
    EXPENSE_ATTACHMENT_PATTERNS = [
        r'receipt', r'order', r'confirmation',
    ]

    def classify(
        self,
        subject: Optional[str] = None,
        from_email: Optional[str] = None,
        body: Optional[str] = None,
        attachments: Optional[list] = None,
    ) -> ClassificationResult:
        """
        Classify an email message. Returns a ClassificationResult with
        the predicted MessageType and a confidence score.

        Parameters
        ----------
        subject : str — email subject line
        from_email : str — sender email address
        body : str — plain text or HTML body (HTML tags will be stripped)
        attachments : list of dicts with 'name' and 'content_type' keys
        """
        # --- Check sender overrides first ---
        if self._override_service and from_email:
            try:
                override = self._override_service.find_override(from_email)
                if override:
                    return ClassificationResult(
                        message_type=MessageType(override.classification_type),
                        confidence=1.0,
                        signals=[f"sender_override: {override.match_type}:{override.match_value} → {override.classification_type}"],
                    )
            except Exception as exc:
                logger.warning("Override lookup failed (falling back to heuristics): %s", exc)

        subject = (subject or '').strip()
        body_text = self._strip_html(body or '')
        attachments = attachments or []

        scores: dict[MessageType, float] = {t: 0.0 for t in MessageType}
        signals: list[str] = []

        # --- Score from subject ---
        subject_lower = subject.lower()

        for kw in self.BILL_SUBJECT_KEYWORDS:
            if kw in subject_lower:
                scores[MessageType.BILL] += 0.35
                signals.append(f"subject contains '{kw}'")
                break

        for kw in self.CREDIT_SUBJECT_KEYWORDS:
            if kw in subject_lower:
                base = 0.40
                # Extra boost when "credit" is followed by a number — very likely a credit memo
                if re.search(r'credit\s*[#:]?\s*\d', subject_lower):
                    base = 0.55
                scores[MessageType.VENDOR_CREDIT] += base
                signals.append(f"subject contains '{kw}' (credit signal)")
                break

        for kw in self.STATEMENT_SUBJECT_KEYWORDS:
            if kw in subject_lower:
                scores[MessageType.VENDOR_STATEMENT] += 0.40
                signals.append(f"subject contains '{kw}' (statement signal)")
                break

        for kw in self.INQUIRY_SUBJECT_KEYWORDS:
            if kw in subject_lower:
                scores[MessageType.VENDOR_INQUIRY] += 0.40
                signals.append(f"subject contains '{kw}' (inquiry signal)")
                break

        for kw in self.EXPENSE_SUBJECT_KEYWORDS:
            if kw in subject_lower:
                scores[MessageType.EXPENSE] += 0.35
                signals.append(f"subject contains '{kw}' (expense signal)")
                break

        # --- Score from body keywords ---
        body_lower = body_text.lower()

        for kw in self.BILL_BODY_KEYWORDS:
            if kw in body_lower:
                scores[MessageType.BILL] += 0.20
                signals.append(f"body contains '{kw}'")
                break

        for kw in self.CREDIT_BODY_KEYWORDS:
            if kw in body_lower:
                scores[MessageType.VENDOR_CREDIT] += 0.25
                signals.append(f"body contains '{kw}' (credit body signal)")
                break

        for kw in self.STATEMENT_BODY_KEYWORDS:
            if kw in body_lower:
                scores[MessageType.VENDOR_STATEMENT] += 0.20
                signals.append(f"body contains '{kw}' (statement body signal)")
                break

        for kw in self.INQUIRY_BODY_KEYWORDS:
            if kw in body_lower:
                scores[MessageType.VENDOR_INQUIRY] += 0.25
                signals.append(f"body contains '{kw}' (inquiry body signal)")
                break

        for kw in self.EXPENSE_BODY_KEYWORDS:
            if kw in body_lower:
                scores[MessageType.EXPENSE] += 0.20
                signals.append(f"body contains '{kw}' (expense body signal)")
                break

        # --- Score from attachments ---
        has_pdf = False
        for att in attachments:
            att_name = (att.get('name') or '').lower()
            att_type = (att.get('content_type') or '').lower()

            if 'pdf' in att_type or att_name.endswith('.pdf'):
                has_pdf = True

            for pattern in self.INVOICE_ATTACHMENT_PATTERNS:
                if re.search(pattern, att_name):
                    scores[MessageType.BILL] += 0.20
                    signals.append(f"attachment name matches invoice pattern: {att.get('name')!r}")
                    break

            for pattern in self.CREDIT_ATTACHMENT_PATTERNS:
                if re.search(pattern, att_name):
                    scores[MessageType.VENDOR_CREDIT] += 0.20
                    signals.append(f"attachment name matches credit pattern: {att.get('name')!r}")
                    break

            for pattern in self.EXPENSE_ATTACHMENT_PATTERNS:
                if re.search(pattern, att_name):
                    scores[MessageType.EXPENSE] += 0.20
                    signals.append(f"attachment name matches expense pattern: {att.get('name')!r}")
                    break

        # A PDF attachment with no other signals leans toward bill
        if has_pdf and not attachments:
            scores[MessageType.BILL] += 0.10
            signals.append("has PDF attachment (weak bill signal)")

        # No attachments + body asking about payment → strong inquiry signal
        if not attachments and scores[MessageType.VENDOR_INQUIRY] > 0:
            scores[MessageType.VENDOR_INQUIRY] += 0.15
            signals.append("no attachment + inquiry keywords = likely inquiry")

        # Construction billing subject pattern:
        #   "[Project/Job Code] - [Vendor Name] - [Invoice#]"
        #   e.g. "WVA - JTA Land Surveying - 26-8697"
        # Detected when subject has two or more " - " separators and the last
        # segment contains digits (invoice/job number). Adds to existing score.
        parts = subject.split(' - ')
        if len(parts) >= 3 and re.search(r'\d', parts[-1]):
            scores[MessageType.BILL] += 0.40
            signals.append("subject matches construction billing pattern: [Project] - [Vendor] - [Invoice#]")

        # "payment request" or "payment" in subject is a weak bill signal
        if re.search(r'\bpayment\s+request\b', subject_lower):
            scores[MessageType.BILL] += 0.30
            signals.append("subject contains 'payment request' (bill signal)")

        # --- Pick winner ---
        # Remove UNKNOWN from competition — it's the fallback
        scores.pop(MessageType.UNKNOWN, None)

        if not scores or all(v == 0.0 for v in scores.values()):
            return ClassificationResult(
                message_type=MessageType.UNKNOWN,
                confidence=0.0,
                signals=["No classification signals found"],
            )

        best_type = max(scores, key=lambda t: scores[t])
        best_score = scores[best_type]

        # Normalize to [0, 1] — cap at 0.97 since we're heuristic-based
        confidence = min(round(best_score, 3), 0.97)

        # If confidence is low, mark as UNKNOWN so user must confirm
        if confidence < 0.25:
            return ClassificationResult(
                message_type=MessageType.UNKNOWN,
                confidence=confidence,
                signals=signals + [f"Best guess: {best_type.value} ({confidence:.0%}) — below threshold"],
            )

        logger.debug(
            "EmailClassifier: type=%s confidence=%.2f subject=%r",
            best_type.value, confidence, subject,
        )

        return ClassificationResult(
            message_type=best_type,
            confidence=confidence,
            signals=signals,
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _strip_html(self, html: str) -> str:
        """Remove HTML tags and decode common entities."""
        text = re.sub(r'<[^>]+>', ' ', html)
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&') \
                   .replace('&lt;', '<').replace('&gt;', '>') \
                   .replace('&quot;', '"')
        return re.sub(r'\s+', ' ', text).strip()
