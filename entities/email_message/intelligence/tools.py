"""Agent-facing tools for the EmailMessage entity.

Designed for the email_specialist agent — a pure orchestrator that
reads polled emails, decides what they are, runs DI on attachments,
records its interpretation of typed fields, looks up sender history,
and delegates to bill_specialist (or other specialists in v2+) for
actual entity creation.

None of these tools require user approval — they're internal
bookkeeping or read-only side effects. The protective layer is
downstream: bill_specialist's `create_bill` is approval-gated, so
the human still sees and approves the proposed draft bill before
anything commits.

Tools:
  read_email_message               GET   /get/email-message/{public_id}
  search_email_sender_history      GET   /email-messages/sender-history?from_email=...
  extract_email_attachment         POST  /email-attachments/{public_id}/extract
  record_extracted_fields          PATCH /email-attachments/{public_id}/extracted-fields
  bridge_email_attachment          POST  /email-attachments/{public_id}/bridge-to-attachment
  mark_email_outcome               PATCH /email-messages/{public_id}/outcome

Tools self-register on import.
"""
import json
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# Body-content cap on `read_email_message`. Trims the most expensive
# tool-call output the email_specialist accumulates per turn — vendor
# statements / marketing emails / threaded reply quotes can run to
# 200K+ chars, all of which would re-ride every subsequent turn's
# messages array. 4000 chars covers BodyPreview-equivalent for
# classification AND the new-text portion of a typical reviewer reply
# (which sits before quoted history / "On <date>...wrote:" markers).
# Override per-call with `full_body=true`.
BODY_CONTENT_TRUNCATE_AT = 4000


# ─── Arg shapes ──────────────────────────────────────────────────────────


class _PublicIdArgs(BaseModel):
    public_id: str = Field(description="UUID of the EmailMessage or EmailAttachment.")


class _ReadEmailMessageArgs(BaseModel):
    public_id: str = Field(description="UUID of the EmailMessage.")
    full_body: bool = Field(
        default=False,
        description=(
            f"Optional escape hatch. By default `body_content` is "
            f"truncated to {BODY_CONTENT_TRUNCATE_AT} chars and a "
            f"`body_content_truncated_at` field is added so you know "
            f"the cap fired. The truncated form is sufficient for "
            f"classification (use body_preview + the leading body) "
            f"and for parsing reviewer replies (the new-text portion "
            f"is at the top, before quoted history). Set this to true "
            f"ONLY when you genuinely need the rest of the body — e.g. "
            f"the truncation flag fired AND the new-text portion of a "
            f"reviewer reply spills past the cap. Most calls should "
            f"omit this."
        ),
    )


class _SenderHistoryArgs(BaseModel):
    from_email: str = Field(
        description=(
            "Sender SMTP address to look up (use the EmailMessage's "
            "`from_address` verbatim — e.g. `laura@walkerlumber.com`)."
        ),
    )
    exclude_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional EmailMessage.PublicId to suppress from the counts. "
            "Pass the SAME public_id you got in the user_message that "
            "started this run so the prior-email totals don't include "
            "the email you're currently classifying."
        ),
    )


class _ExtractedFieldsArgs(BaseModel):
    public_id: str = Field(description="UUID of the EmailAttachment to update.")
    vendor_name: Optional[str] = Field(default=None, description="Vendor name as you read it.")
    invoice_number: Optional[str] = Field(default=None, description="Invoice / DOC# / Bill #.")
    invoice_date: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD.")
    due_date: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD.")
    subtotal: Optional[Decimal] = Field(default=None, description="Pre-tax subtotal.")
    total_amount: Optional[Decimal] = Field(default=None, description="Final invoice total.")
    currency: Optional[str] = Field(default=None, description="ISO currency code (USD default).")


class _OutcomeArgs(BaseModel):
    public_id: str = Field(description="UUID of the EmailMessage to mark.")
    outcome: str = Field(
        description=(
            "One of: `processed` | `awaiting_approval` | `needs_review` | "
            "`irrelevant`. Drives both the DB ProcessingStatus and the "
            "Outlook category applied back to the source message."
        ),
    )
    reason: Optional[str] = Field(
        default=None,
        description=(
            "Optional human-readable note recorded on the EmailMessage row. "
            "Useful when outcome is needs_review or irrelevant — the future "
            "human reviewer reads this to know why the agent flagged it."
        ),
    )
    classification: Optional[str] = Field(
        default=None,
        description=(
            "Controlled-vocabulary classification of WHAT the email was. "
            "Pick exactly one: `vendor_invoice` | `vendor_credit_memo` | "
            "`vendor_statement` | `vendor_expense_receipt` | "
            "`customer_payment` | `customer_question` | `customer_dispute` "
            "| `internal_reply` | `internal_forward` | `vendor_newsletter` "
            "| `non_actionable` | `unknown`. Persisted to "
            "`EmailMessage.AgentClassification` so future emails from the "
            "same sender can be informed by this decision."
        ),
    )
    classification_reason: Optional[str] = Field(
        default=None,
        description="One-sentence narrative of why you classified this way (under 1024 chars).",
    )
    decided_action: Optional[str] = Field(
        default=None,
        description=(
            "Controlled-vocabulary action you took. Pick exactly one: "
            "`delegated_to_bill_specialist` | "
            "`delegated_to_bill_credit_specialist` | "
            "`delegated_to_expense_specialist` | `flagged_needs_review` | "
            "`marked_irrelevant` | `marked_processed`."
        ),
    )
    confidence: Optional[Decimal] = Field(
        default=None,
        ge=Decimal("0"), le=Decimal("1"),
        description=(
            "Your overall classification confidence in [0,1]. The prompt's "
            "decision tree treats >= 0.95 as confident enough to act per "
            "the classification; below 0.95 you should already be picking "
            "outcome=needs_review regardless of classification."
        ),
    )


# ─── Read tools ──────────────────────────────────────────────────────────


async def _read_email_message(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _ReadEmailMessageArgs(**args)
    result = await ctx.call_api(
        "GET", f"/api/v1/get/email-message/{parsed.public_id}"
    )
    if result.is_error or parsed.full_body:
        return result
    # Truncate body_content to keep tool-result re-ride cheap on multi-turn
    # sessions. Best-effort: if the response shape is unexpected, return
    # the original unchanged — the agent must never break over a tweaked
    # envelope.
    try:
        if not isinstance(result.content, str):
            return result
        payload = json.loads(result.content)
        inner = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(inner, dict):
            return result
        body = inner.get("body_content")
        if not isinstance(body, str) or len(body) <= BODY_CONTENT_TRUNCATE_AT:
            return result
        inner["body_content_full_length"] = len(body)
        inner["body_content_truncated_at"] = BODY_CONTENT_TRUNCATE_AT
        inner["body_content"] = body[:BODY_CONTENT_TRUNCATE_AT]
        return ToolResult(content=json.dumps(payload))
    except (ValueError, TypeError, KeyError):
        return result


read_email_message = Tool(
    name="read_email_message",
    description=(
        "Load one polled EmailMessage by public_id. Returns the email "
        "(from, subject, body, recipients, etc.) PLUS the list of "
        "attachments with their extraction status. Use this as your "
        "first call after picking up a pending email."
        "\n\n"
        f"By default `body_content` is truncated to "
        f"{BODY_CONTENT_TRUNCATE_AT} chars and the response carries "
        f"`body_content_truncated_at` + `body_content_full_length` "
        f"fields when the cap fires. The truncated form covers "
        f"classification + the new-text portion of reviewer replies "
        f"(which sits at the top, before quoted history). Pass "
        f"`full_body=true` only when you need the rest — rare."
    ),
    input_schema=input_schema_from(_ReadEmailMessageArgs),
    handler=_read_email_message,
)


# ─── Write tools (no approval — internal bookkeeping) ────────────────────


async def _extract_email_attachment(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "POST", f"/api/v1/email-attachments/{parsed.public_id}/extract"
    )


extract_email_attachment = Tool(
    name="extract_email_attachment",
    description=(
        "Run Document Intelligence on an EmailAttachment using the "
        "generic `prebuilt-layout` model with `keyValuePairs` enabled. "
        "Returns a doc-type-agnostic structure:\n"
        "  • `content`         — full document text as one string\n"
        "  • `key_value_pairs` — auto-extracted [{key, value, "
        "confidence}, …]\n"
        "  • `tables`          — row-major matrices of cell text\n"
        "  • `pages_count`     — page count\n\n"
        "You do the document-type classification (invoice / credit memo "
        "/ receipt / statement / non-financial) by reading `content` + "
        "`key_value_pairs`. You also pull out the fields you need for "
        "delegation (vendor name, invoice number, total, dates, line "
        "items) from the same data.\n\n"
        "Call on every PDF/JPG/PNG/TIFF attachment that isn't inline. "
        "DI doesn't support xlsx/docx; flag those emails for review "
        "without calling extract.\n\n"
        "Idempotent — re-running on the same attachment overwrites the "
        "stored DI result with the latest run."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_extract_email_attachment,
)


async def _bridge_email_attachment(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "POST", f"/api/v1/email-attachments/{parsed.public_id}/bridge-to-attachment"
    )


bridge_email_attachment = Tool(
    name="bridge_email_attachment",
    description=(
        "Create an Attachment row from an EmailAttachment so it can be "
        "passed to `bill_specialist.create_bill` (which requires "
        "`attachment_public_id`). The new Attachment shares the same blob "
        "URL — no bytes are copied. Hash-based dedup means re-running "
        "returns the existing Attachment.\n\n"
        "Returns the Attachment row including its `public_id`, which "
        "you then pass verbatim to bill_specialist via the delegation "
        "task description."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_bridge_email_attachment,
)


async def _mark_email_outcome(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _OutcomeArgs(**args)
    body: dict = {"outcome": parsed.outcome}
    if parsed.reason:
        body["reason"] = parsed.reason
    if parsed.classification:
        body["classification"] = parsed.classification
    if parsed.classification_reason:
        body["classification_reason"] = parsed.classification_reason
    if parsed.decided_action:
        body["decided_action"] = parsed.decided_action
    if parsed.confidence is not None:
        body["confidence"] = str(parsed.confidence)
    # Forward the agent's own session id so EmailMessage.AgentSessionId
    # gets linked back to this run — gives the React UI + future audits
    # a direct path from email row to transcript without grepping prose.
    if ctx.session_id is not None:
        body["agent_session_id"] = ctx.session_id
    return await ctx.call_api(
        "PATCH", f"/api/v1/email-messages/{parsed.public_id}/outcome", body=body
    )


mark_email_outcome = Tool(
    name="mark_email_outcome",
    description=(
        "Final step in your run. Stamps the agent's decision onto the "
        "EmailMessage row (workflow status + classification + action + "
        "confidence) AND applies the matching Outlook category back to "
        "the source message so a human can audit at a glance. No approval "
        "required — this is internal bookkeeping; the protective layer is "
        "the approval cards on `create_bill` (etc.) downstream.\n\n"
        "Outcome values (workflow status / Outlook category):\n"
        "  • `processed` — agent successfully delegated and the "
        "    specialist proposed/created entities. Stamps `Agent: "
        "    Processed` in Outlook.\n"
        "  • `awaiting_approval` — specialist proposed a draft entity "
        "    that's waiting for human approval (most common happy path). "
        "    Stamps `Agent: Awaiting Approval`.\n"
        "  • `needs_review` — extraction failed validation, confidence "
        "    too low, unsupported attachment type, or the email looks "
        "    relevant but the agent can't act on it confidently. Always "
        "    pass a `reason` so the human knows why.\n"
        "  • `irrelevant` — email isn't actionable (no attachment + "
        "    body is a discussion / approval / forward without invoice "
        "    content; or the attachment was clearly junk).\n\n"
        "Always pass `classification`, `decided_action`, "
        "`classification_reason`, and `confidence` when outcome is "
        "`awaiting_approval` or `needs_review` — these power "
        "`search_email_sender_history` for future emails from the same "
        "sender. Optional but recommended for `processed` and "
        "`irrelevant`.\n\n"
        "Multi-attachment precedence (when an email had several "
        "attachments and outcomes diverged):\n"
        "  awaiting_approval > needs_review > processed > irrelevant"
    ),
    input_schema=input_schema_from(_OutcomeArgs),
    handler=_mark_email_outcome,
)


# ─── Sender history (read) ──────────────────────────────────────────────


async def _search_email_sender_history(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _SenderHistoryArgs(**args)
    qs = f"from_email={parsed.from_email}"
    if parsed.exclude_public_id:
        qs += f"&exclude_public_id={parsed.exclude_public_id}"
    return await ctx.call_api("GET", f"/api/v1/email-messages/sender-history?{qs}")


search_email_sender_history = Tool(
    name="search_email_sender_history",
    description=(
        "Look up prior context for an email sender (keyed on "
        "`from_email`). Returns aggregate signal you can weigh when "
        "classifying a fresh email from the same address:\n"
        "  • `prior_emails.total` and breakdowns by_status / "
        "by_classification / by_action\n"
        "  • `prior_bills_committed`, `prior_expenses_committed`, "
        "`prior_bill_credits_committed` — actual entities created from "
        "prior emails by this sender (zero is common — many emails get "
        "stuck at `awaiting_approval`)\n"
        "  • `associated_vendors` — distinct Vendor rows transitively "
        "associated via committed Bills (gives you a Vendor public_id + "
        "name to feed bill_specialist if available)\n\n"
        "A sender with `prior_emails.by_classification.vendor_invoice >= 1` "
        "is a recognized invoice sender — high prior. A sender with no "
        "history at all is a stranger; lean more heavily on email + DI "
        "signals for that classification."
    ),
    input_schema=input_schema_from(_SenderHistoryArgs),
    handler=_search_email_sender_history,
)


# ─── Agent-driven typed-field overlay ───────────────────────────────────


async def _record_extracted_fields(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _ExtractedFieldsArgs(**args)
    body: dict = {}
    if parsed.vendor_name is not None:    body["vendor_name"]    = parsed.vendor_name
    if parsed.invoice_number is not None: body["invoice_number"] = parsed.invoice_number
    if parsed.invoice_date is not None:   body["invoice_date"]   = parsed.invoice_date
    if parsed.due_date is not None:       body["due_date"]       = parsed.due_date
    if parsed.subtotal is not None:       body["subtotal"]       = str(parsed.subtotal)
    if parsed.total_amount is not None:   body["total_amount"]   = str(parsed.total_amount)
    if parsed.currency is not None:       body["currency"]       = parsed.currency
    return await ctx.call_api(
        "PATCH",
        f"/api/v1/email-attachments/{parsed.public_id}/extracted-fields",
        body=body,
    )


record_extracted_fields = Tool(
    name="record_extracted_fields",
    description=(
        "Persist the typed fields you read from DI's prebuilt-layout "
        "output onto the EmailAttachment row (DiVendorName, "
        "DiInvoiceNumber, DiInvoiceDate, DiDueDate, DiSubtotal, "
        "DiTotalAmount, DiCurrency). Preserves the underlying DI "
        "extraction (status, raw JSON, model) — this is your "
        "interpretation of the document, not DI's hoist.\n\n"
        "Call this after `extract_email_attachment` returns and you've "
        "identified which key_value_pairs / content lines map to which "
        "semantic fields. Required for any attachment you'll delegate to "
        "bill_specialist — search_email_sender_history's "
        "`associated_vendors` and prior-classification signals depend on "
        "these fields being persisted.\n\n"
        "All fields optional — pass only what you actually extracted. If "
        "DI's output didn't surface a due date, leave `due_date` unset "
        "rather than guessing."
    ),
    input_schema=input_schema_from(_ExtractedFieldsArgs),
    handler=_record_extracted_fields,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    read_email_message,
    search_email_sender_history,
    extract_email_attachment,
    record_extracted_fields,
    bridge_email_attachment,
    mark_email_outcome,
):
    register(_tool)
