"""Pilot cascade task: email pre-classification.

Classifies a polled inbox email into the controlled vocabulary the
email_specialist already uses (`EmailMessage.AgentClassification`). This is a
discrete, single-shot task with a deterministic validator (the label must be
in-vocabulary), which makes it the ideal first proof of the cascade gate.

Payload shape (a plain dict):
    {
      "from_address": str, "from_name": str, "subject": str,
      "body": str,                         # body_new_text preferred
      "attachment_names": list[str],       # filenames (optional)
      "di_text": str | None,               # DI 'content' of the main attachment
    }
"""
from typing import Any

from intelligence.cascade.core import Rung, StructuredTask

# Controlled vocabulary — must match EmailMessage.AgentClassification.
EMAIL_CLASSIFICATIONS: frozenset[str] = frozenset({
    "vendor_invoice",
    "vendor_credit_memo",
    "vendor_statement",
    "vendor_expense_receipt",
    "customer_payment",
    "customer_question",
    "customer_dispute",
    "reviewer_reply",
    "internal_reply",
    "internal_forward",
    "vendor_newsletter",
    "contract_labor_timesheet",
    "non_actionable",
    "unknown",
})

# Hand-set for v1 (tune from the per-attempt logs). The deterministic validator
# is the hard gate; τ is the soft gate that triggers escalation when a cheap
# model is unsure. Lower τ = accept cheap rungs more often = cheaper.
EMAIL_CLASSIFY_THRESHOLD = 0.85

_SYSTEM_PROMPT = (
    "You classify one incoming email for a construction-bookkeeping system "
    "into exactly one category from this controlled vocabulary:\n"
    + ", ".join(sorted(EMAIL_CLASSIFICATIONS))
    + ".\n\n"
    "Definitions of the most common labels:\n"
    "- vendor_invoice: a vendor billing us (has an invoice/bill document).\n"
    "- vendor_credit_memo: a vendor crediting/refunding us.\n"
    "- vendor_statement: a multi-invoice account summary.\n"
    "- vendor_expense_receipt: a point-of-sale / card receipt.\n"
    "- contract_labor_timesheet: a worker-submitted timesheet (clock in/out + "
    "job-site address, no invoice attached).\n"
    "- reviewer_reply: an internal PM/Owner reply approving/rejecting a bill.\n"
    "- internal_reply / internal_forward: other intra-company mail.\n"
    "- vendor_newsletter: marketing / FYI / non-transactional.\n"
    "- non_actionable: nothing to act on. unknown: you cannot tell.\n\n"
    "Weigh the sender, subject, body, attachment names, and the extracted "
    "document text. Respond with ONLY a JSON object, no prose, of the form:\n"
    '{"classification": "<one label from the vocabulary>", '
    '"confidence": <number 0..1>, "reason": "<one short sentence>"}\n'
    "Set confidence to your true calibrated probability that the label is "
    "correct. Use a low confidence (and the label `unknown`) when the signals "
    "are weak or conflicting."
)


def _build_user_message(payload: dict[str, Any]) -> str:
    attachments = payload.get("attachment_names") or []
    di_text = (payload.get("di_text") or "").strip()
    if len(di_text) > 4000:
        di_text = di_text[:4000] + "\n…(truncated)"
    lines = [
        f"From: {payload.get('from_name', '')} <{payload.get('from_address', '')}>",
        f"Subject: {payload.get('subject', '')}",
        f"Attachments: {', '.join(attachments) if attachments else '(none)'}",
        "",
        "Body:",
        (payload.get("body") or "").strip() or "(empty)",
    ]
    if di_text:
        lines += ["", "Extracted document text (Document Intelligence):", di_text]
    return "\n".join(lines)


def _validate(result: dict[str, Any]) -> tuple[bool, str]:
    label = result.get("classification")
    if not isinstance(label, str):
        return False, "missing/non-string classification"
    if label not in EMAIL_CLASSIFICATIONS:
        return False, f"'{label}' not in controlled vocabulary"
    return True, "ok"


EMAIL_CLASSIFICATION_TASK = StructuredTask(
    name="email_classification",
    system_prompt=_SYSTEM_PROMPT,
    threshold=EMAIL_CLASSIFY_THRESHOLD,
    build_user_message=_build_user_message,
    validate=_validate,
    ladder=None,  # use the default cheapest-first ladder
)
