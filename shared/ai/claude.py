"""
Claude Classification Service
==============================
Single-call Haiku classifier for email messages.
Uses the raw Anthropic SDK — no LangChain, no Agent SDK.
"""
import json
import logging
from typing import List, Optional

import anthropic

from config import Settings
from shared.classification.models import ClassificationResult, ClassificationType, LABEL_MAP

logger = logging.getLogger(__name__)

CLASSIFICATION_SYSTEM_PROMPT = """\
You are an email classifier for a construction company's invoice inbox.

Classify the email into exactly ONE of these types:

- BILL_DOCUMENT: An invoice, bill, or payment request from a vendor/subcontractor. \
Has an attached PDF/image of the invoice. May include purchase orders or delivery tickets.
- BILL_CREDIT_DOCUMENT: A credit memo, credit note, or vendor credit adjusting a \
previous bill. Often references a prior invoice number. May have a negative amount.
- EXPENSE: A receipt, reimbursement request, or expense report. Typically from an \
employee or corporate card statement for a specific purchase.
- INQUIRY: A question, follow-up, or correspondence that is NOT a bill or expense. \
Includes approval requests, status inquiries, vendor communications, and general questions.
- STATEMENT: A periodic account statement summarizing multiple invoices and payments. \
Not a single bill — it is a summary of account activity over a period.

Decision criteria:
1. If the email has an attached invoice/bill PDF → BILL_DOCUMENT (most common).
2. If the attachment or subject mentions "credit memo" or "credit note" → BILL_CREDIT_DOCUMENT.
3. If it is a receipt or reimbursement with no vendor invoice → EXPENSE.
4. If it is a summary of multiple invoices/payments over a date range → STATEMENT.
5. If none of the above, or it is a question/reply/FYI → INQUIRY.

Respond with JSON only, no other text:
{
  "type": "BILL_DOCUMENT|BILL_CREDIT_DOCUMENT|EXPENSE|INQUIRY|STATEMENT",
  "confidence": 0.0 to 1.0,
  "signals": ["reason 1", "reason 2"]
}"""


def classify_email(
    subject: str,
    sender: str,
    body_preview: str,
    attachment_filenames: List[str],
    heuristic_result: ClassificationResult,
    thread_history: Optional[List[str]] = None,
) -> ClassificationResult:
    """
    Classify an email using a single Claude Haiku call.

    Falls back to ``heuristic_result`` on any failure (API error,
    parse error, timeout, missing API key, etc.).
    """
    # Build the user message with all available signals
    parts = [
        f"Subject: {subject or '(none)'}",
        f"From: {sender or '(unknown)'}",
    ]

    if attachment_filenames:
        parts.append(f"Attachments: {', '.join(attachment_filenames)}")
    else:
        parts.append("Attachments: none")

    if body_preview:
        # Truncate to avoid burning tokens on long email bodies
        truncated = body_preview[:2000]
        parts.append(f"Body preview:\n{truncated}")

    if thread_history:
        parts.append(f"Thread context (newest first):\n" + "\n".join(thread_history[:5]))

    user_message = "\n".join(parts)

    try:
        settings = Settings()
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=CLASSIFICATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        logger.debug("Claude classification stop_reason=%s, content_blocks=%d",
                     response.stop_reason, len(response.content))
        raw_text = response.content[0].text.strip() if response.content else ""
        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
            raw_text = raw_text.rsplit("```", 1)[0].strip()
        parsed = json.loads(raw_text)

        cls_type = ClassificationType(parsed["type"])
        confidence = float(parsed["confidence"])
        signals = parsed.get("signals", [])

        return ClassificationResult(
            message_type=cls_type,
            classification=cls_type.value,
            confidence=confidence,
            signals=signals,
            suggested_label=LABEL_MAP[cls_type],
        )

    except anthropic.APIError as exc:
        logger.warning("Claude classification API error, falling back to heuristic: %s", exc)
        return heuristic_result

    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Claude classification parse error, falling back to heuristic: %s", exc)
        return heuristic_result

    except Exception as exc:
        logger.warning("Claude classification failed, falling back to heuristic: %s", exc)
        return heuristic_result


# ── Reply parsing ────────────────────────────────────────────────────

REPLY_PARSE_SYSTEM = """\
You are parsing a PM's email reply to an invoice approval request.

The PM was asked to review a bill and reply with one of:
- Approved: with a Project, Sub Cost Code, and Description
- Not approved: with a brief note explaining why

Parse the reply and return JSON only:
{
  "approved": true or false,
  "project": "the project name or abbreviation if provided, or null",
  "sub_cost_code": "the sub cost code name if approved, or null",
  "description": "the line item description if approved, or null",
  "note": "the PM's note if not approved, or null"
}

Rules:
- If the reply contains words like "approved", "looks good", "go ahead", "ok", "yes" → approved: true
- If the reply contains words like "not approved", "decline", "hold", "no", "reject" → approved: false
- The project is a construction project name or abbreviation (e.g., "MR2-MAIN", "HP", "SHT")
- The sub cost code is a construction cost category (e.g., "Framing Labor", "Hardwood Flooring Material", "21.0 Siding Material")
- The description is what the line item should say on the bill
- If approved but project, sub cost code, or description are not provided, set them to null
- If ambiguous, set approved to false with note explaining the ambiguity"""


def parse_pm_reply(
    reply_body: str,
    original_subject: str,
    vendor_name: str,
    bill_number: str,
    bill_amount: str,
) -> Optional[dict]:
    """
    Parse a PM's email reply to determine approval status.

    Returns:
        {
            "approved": bool,
            "sub_cost_code": str | None,
            "description": str | None,
            "note": str | None,
        }
        or None on failure.
    """
    parts = [
        f"Original subject: {original_subject or '(none)'}",
        f"Bill from: {vendor_name or '(unknown)'}",
        f"Bill number: {bill_number or 'N/A'}",
        f"Bill amount: {bill_amount or 'N/A'}",
        f"\nPM's reply:\n{reply_body[:3000]}",
    ]
    user_message = "\n".join(parts)

    try:
        settings = Settings()
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=REPLY_PARSE_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
            raw_text = raw_text.rsplit("```", 1)[0].strip()

        parsed = json.loads(raw_text)

        logger.info(
            "Reply parsed: approved=%s project=%s sub_cost_code=%s description=%s note=%s",
            parsed.get("approved"),
            parsed.get("project"),
            parsed.get("sub_cost_code"),
            (parsed.get("description") or "")[:50],
            (parsed.get("note") or "")[:50],
        )

        return {
            "approved": bool(parsed.get("approved", False)),
            "project": parsed.get("project"),
            "sub_cost_code": parsed.get("sub_cost_code"),
            "description": parsed.get("description"),
            "note": parsed.get("note"),
        }

    except Exception as exc:
        logger.warning("Reply parsing failed: %s", exc)
        return None
