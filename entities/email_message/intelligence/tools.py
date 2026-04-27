"""Agent-facing tools for the EmailMessage entity.

Designed for the email_specialist agent — a pure orchestrator that
reads polled emails, decides what they are, runs DI on attachments
that look invoice-shaped, and delegates to bill_specialist (or other
specialists in v2+) for actual entity creation.

None of these tools require user approval — they're internal
bookkeeping or read-only side effects. The protective layer is
downstream: bill_specialist's `create_bill` is approval-gated, so
the human still sees and approves the proposed draft bill before
anything commits.

Tools:
  read_email_message               GET   /get/email-message/{public_id}
  extract_email_attachment         POST  /email-attachments/{public_id}/extract
  bridge_email_attachment          POST  /email-attachments/{public_id}/bridge-to-attachment
  mark_email_outcome               PATCH /email-messages/{public_id}/outcome

Tools self-register on import.
"""
from typing import Optional

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# ─── Arg shapes ──────────────────────────────────────────────────────────


class _PublicIdArgs(BaseModel):
    public_id: str = Field(description="UUID of the EmailMessage or EmailAttachment.")


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


# ─── Read tools ──────────────────────────────────────────────────────────


async def _read_email_message(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/email-message/{parsed.public_id}"
    )


read_email_message = Tool(
    name="read_email_message",
    description=(
        "Load one polled EmailMessage by public_id. Returns the email "
        "(from, subject, body, recipients, etc.) PLUS the list of "
        "attachments with their extraction status. Use this as your "
        "first call after picking up a pending email."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
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
        "Run Document Intelligence on an EmailAttachment. Returns the "
        "hoisted result: vendor_name, invoice_number, invoice_date, "
        "due_date, total_amount, currency, confidence, line_items, plus "
        "a validation block (`is_valid` + `issues[]`).\n\n"
        "Call this only on attachments that look invoice-shaped: PDF/JPG/"
        "PNG/TIFF, reasonable file size (>2KB), filename suggests an "
        "invoice (`INV-…`, `bill.pdf`, `invoice_…`). Skip xlsx/docx — "
        "DI doesn't support them; flag the email for review instead.\n\n"
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
    body = {"outcome": parsed.outcome}
    if parsed.reason:
        body["reason"] = parsed.reason
    return await ctx.call_api(
        "PATCH", f"/api/v1/email-messages/{parsed.public_id}/outcome", body=body
    )


mark_email_outcome = Tool(
    name="mark_email_outcome",
    description=(
        "Final step in your run. Flip the EmailMessage's ProcessingStatus "
        "AND apply the matching Outlook category back to the source "
        "message so a human can audit at a glance. No approval required "
        "— this is internal bookkeeping; the protective layer is the "
        "approval cards on `create_bill` (etc.) downstream.\n\n"
        "Outcome values:\n"
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
        "    content; or the attachment was clearly junk). Stamps "
        "    `Agent: Irrelevant`.\n\n"
        "Multi-attachment precedence (when an email had several "
        "attachments and outcomes diverged):\n"
        "  awaiting_approval > needs_review > processed > irrelevant"
    ),
    input_schema=input_schema_from(_OutcomeArgs),
    handler=_mark_email_outcome,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    read_email_message,
    extract_email_attachment,
    bridge_email_attachment,
    mark_email_outcome,
):
    register(_tool)
