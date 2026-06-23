"""API for EmailMessage + EmailAttachment.

Read endpoints (paginated list / by public_id / attachments) are gated
on Modules.EMAIL_MESSAGES read.

Two write endpoints back the email agent's tools:
  POST /email-attachments/{public_id}/extract — runs DI on demand
  PATCH /email-messages/{public_id}/outcome — flips ProcessingStatus +
                                              applies Outlook category
Both require can_update on EMAIL_MESSAGES.
"""
import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from entities.email_message.business.categories import (
    AGENT_AWAITING_APPROVAL,
    AGENT_IRRELEVANT,
    AGENT_NEEDS_REVIEW,
    AGENT_PROCESSED,
)
from entities.email_message.business.service import (
    EmailAttachmentBridgeService,
    EmailAttachmentExtractionService,
    EmailMessageService,
)
from integrations.ms.mail.external import client as mail_client
from shared.api.responses import item_response, list_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "email_message"])


# Outcome → (ProcessingStatus, Outlook category) mapping for the PATCH endpoint.
# Keeps the API contract honest: the caller picks a semantic outcome name
# and the server handles the side effects consistently.
_OUTCOME_MAP = {
    "processed":         ("agent_complete", AGENT_PROCESSED),
    "awaiting_approval": ("awaiting_review", AGENT_AWAITING_APPROVAL),
    "needs_review":      ("awaiting_review", AGENT_NEEDS_REVIEW),
    "irrelevant":        ("irrelevant", AGENT_IRRELEVANT),
}


@router.get("/get/email-messages")
def get_email_messages_router(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    processing_status: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    sort_by: str = Query(default="ReceivedDatetime"),
    sort_direction: str = Query(default="DESC"),
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """
    Paginated read of polled email messages.
    Filter by processing_status (pending | processing | extracted |
    awaiting_review | agent_complete | irrelevant | failed).
    """
    service = EmailMessageService()
    rows = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search,
        processing_status=processing_status,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
    total = service.count(
        search_term=search,
        processing_status=processing_status,
        start_date=start_date,
        end_date=end_date,
    )
    return list_response([r.to_dict() for r in rows], count=total)


@router.get("/get/email-message/{public_id}")
def get_email_message_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """One email by public_id, with its attachment rows attached.

    Strips `di_result_json` from each attachment to keep the response
    lean — the raw DI JSON is ~80% of the payload by volume and the
    agent reads it via `extract_email_attachment` (which returns the
    structured `content`/`key_value_pairs`/`tables` shape it actually
    needs). The typed `Di*` columns stay so the agent still sees the
    agent-recorded vendor/invoice/total fields here without paying the
    100KB per turn.

    Also includes a slim `linked_bill` field (or null) — the Bill that
    was created from this email via the agent pipeline. Lets the React
    Inbox detail view render a one-click "View Bill" link without an
    extra round-trip.
    """
    service = EmailMessageService()
    email = service.read_by_public_id(public_id=public_id)
    if not email:
        raise_not_found("Email message")
    payload = email.to_dict()
    attachments = []
    for a in service.list_attachments(email_message_id=email.id):
        d = a.to_dict()
        d.pop("di_result_json", None)
        attachments.append(d)
    payload["attachments"] = attachments
    # Reply-text isolation: split the body into (new_text, quoted_history)
    # so the agent reads the sender's actual new content first and only
    # falls back to the quoted-history block when the new-text portion
    # alone isn't enough context. Best-effort — when no quote boundary is
    # detected, body_new_text carries the whole body and body_quoted_history
    # is null. Both fields are PLAIN TEXT regardless of body_content_type.
    # The original body_content is preserved unchanged for backward compat.
    from entities.email_message.business.service import isolate_new_text
    new_text, quoted_history = isolate_new_text(
        email.body_content, email.body_content_type
    )
    payload["body_new_text"] = new_text
    payload["body_quoted_history"] = quoted_history
    # Reverse lookup: any Bill carrying this email's id as its source.
    # Lazy import to avoid pulling the Bill module into the email-message
    # service's import graph at startup.
    from entities.bill.persistence.repo import BillRepository
    payload["linked_bill"] = BillRepository().read_slim_by_source_email_message_id(email.id)
    return item_response(payload)


@router.get("/get/email-message/{public_id}/attachments")
def get_email_message_attachments_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """Attachments for an email — useful for inspecting DI extraction state."""
    service = EmailMessageService()
    email = service.read_by_public_id(public_id=public_id)
    if not email:
        raise_not_found("Email message")
    rows = service.list_attachments(email_message_id=email.id)
    return list_response([r.to_dict() for r in rows])


@router.get("/email-messages/gather-invoice-context")
def gather_invoice_context_router(
    email_message_id: str = Query(
        ...,
        description="UUID of the focal EmailMessage.",
    ),
    email_attachment_id: Optional[str] = Query(
        default=None,
        description=(
            "Optional UUID of the invoice attachment to use as the source "
            "of DI typed fields. When omitted, the first attachment with a "
            "populated DiInvoiceNumber is used."
        ),
    ),
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """One-call gather for invoice-shaped emails: vendor candidates +
    project candidates + existing-bill dedup, bundled.

    Replaces the three chained calls (find_vendor_for_invoice + delegate
    to project_specialist + manual bill search) with a single read.
    Powers the agent's `gather_invoice_context` tool.

    Reads from the focal attachment's recorded DI typed columns —
    `extract_email_attachment` + `record_extracted_fields` must have
    been called first. Returns `extraction_required=true` when no
    attachment carries typed overlay yet.

    Existing-bill dedup is a non-trivial signal: when populated, the
    agent must NOT create a duplicate Bill; it should either link the
    email to the existing Bill (via the existing
    LinkBillSourceEmailMessage path) or surface the dup and skip
    creating a new one.
    """
    service = EmailMessageService()
    email = service.read_by_public_id(public_id=email_message_id)
    if not email:
        raise_not_found("Email message")
    attachment_id: Optional[int] = None
    if email_attachment_id is not None:
        from entities.email_message.persistence.repo import EmailAttachmentRepository
        ea = EmailAttachmentRepository().read_by_public_id(email_attachment_id)
        if not ea or ea.email_message_id != email.id:
            raise_not_found("Email attachment")
        attachment_id = ea.id
    return item_response(service.gather_invoice_context(
        email_message_id=email.id,
        email_attachment_id=attachment_id,
    ))


@router.get("/get/email-message/{public_id}/attachment-totals")
def get_email_message_attachment_totals_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """Sum DI-extracted total_amount across all attachments on an email.

    Used by the email agent's `compute_attachment_totals` tool: the agent
    compares the sum against any balance-due claim in the body as a
    strong completeness signal. Greenrise's 5 attached invoices summed
    exactly to $6,102.50, matching the vendor's claimed balance — that
    math reconciliation was the decisive signal the agent should detect
    automatically rather than each agent re-deriving it.

    Returns per-attachment Di* breakdown + aggregate sum + currency. Sum
    is null when no extractions exist OR when attachments use mixed
    currencies (refuse-to-sum). Use the per-attachment list to compare
    against the body's stated balance manually in that case.
    """
    service = EmailMessageService()
    email = service.read_by_public_id(public_id=public_id)
    if not email:
        raise_not_found("Email message")
    return item_response(service.compute_attachment_totals(email_message_id=email.id))


@router.get("/get/email-message/{public_id}/thread")
def get_email_message_thread_router(
    public_id: str,
    max_rows: int = Query(
        default=50, ge=1, le=200,
        description="Maximum number of sibling messages to return.",
    ),
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """Sibling EmailMessages in the same Graph conversation thread.

    Returns all EmailMessage rows sharing the focal email's
    `ConversationId`, ordered oldest → newest. The focal email itself is
    excluded (use `read_email_message` for that). Powers the agent's
    `read_email_thread` tool: the prior emails in the same conversation
    are usually the strongest single signal for what the current email
    means (e.g. a vendor's collections email only makes sense alongside
    the prior exchanges in that thread).

    Header-only — `body_content` + attachments are NOT included to keep
    the response slim. Call `read_email_message` on any sibling whose
    body the agent needs to read in full.
    """
    service = EmailMessageService()
    email = service.read_by_public_id(public_id=public_id)
    if not email:
        raise_not_found("Email message")
    if not email.conversation_id:
        return list_response([])
    siblings = service.read_thread_by_conversation_id(
        conversation_id=email.conversation_id,
        exclude_public_id=public_id,
        max_rows=max_rows,
    )
    return list_response([s.to_dict() for s in siblings])


# ─── Write endpoints (agent uses these via tools) ─────────────────────────


@router.post("/email-attachments/{public_id}/extract")
def extract_email_attachment_router(
    public_id: str,
    force_inline: bool = Query(
        default=False,
        description=(
            "When true, force-extract an inline attachment by fetching the "
            "bytes from MS Graph on demand (inline attachments are not "
            "persisted to blob storage). Use only when the visible text "
            "signal is ambiguous and an inline image — embedded screenshot, "
            "pasted remit advice — might carry decisive context. Default "
            "False preserves the existing cost-saving behavior of "
            "auto-skipping inline attachments."
        ),
    ),
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES, "can_update")),
):
    """Run Document Intelligence on a single EmailAttachment.

    Idempotent: if the attachment was already extracted, the extraction
    service overwrites with the latest run. Returns the hoisted result
    + validation outcome, suitable for the agent to reason over.
    """
    return item_response(
        EmailAttachmentExtractionService().extract_by_public_id(
            public_id, force_inline=force_inline
        )
    )


@router.post("/email-attachments/{public_id}/bridge-to-attachment")
def bridge_email_attachment_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES, "can_update")),
):
    """Bridge an EmailAttachment into a regular Attachment row.

    The new Attachment shares the same blob URL as the EmailAttachment
    (no blob copy). Hash-based dedup means re-running on the same email
    attachment returns the existing Attachment.

    The email_specialist agent calls this before delegating to
    bill_specialist, since bill_create requires `attachment_public_id`.
    """
    attachment = EmailAttachmentBridgeService().bridge(public_id)
    return item_response(attachment.to_dict())


class _OutcomeBody(BaseModel):
    outcome: str = Field(
        description=(
            "One of: processed | awaiting_approval | needs_review | irrelevant. "
            "Drives both the DB ProcessingStatus and the Outlook category "
            "applied back to the source message."
        ),
    )
    reason: Optional[str] = Field(
        default=None,
        description=(
            "Optional human-readable note recorded on the EmailMessage row "
            "(LastError column when reason is non-empty)."
        ),
    )
    agent_session_id: Optional[int] = Field(
        default=None,
        description="Optional AgentSession.Id linking this email to its run.",
    )
    # Agent classification stamp — captures the email_specialist's
    # *semantic* decision so search_email_sender_history can surface
    # this email's classification to the next email from the same sender.
    classification: Optional[str] = Field(
        default=None,
        description=(
            "Controlled-vocabulary classification of what the email was. "
            "Values: vendor_invoice | vendor_credit_memo | vendor_statement "
            "| vendor_expense_receipt | customer_payment | customer_question "
            "| customer_dispute | internal_reply | internal_forward "
            "| vendor_newsletter | contract_labor_timesheet | non_actionable "
            "| unknown. `contract_labor_timesheet` covers forwarded worker "
            "timesheets (clock-in/out + address + work description) that the "
            "contract_labor_specialist agent handles — distinct from "
            "`non_actionable` so sender-history can route future emails."
        ),
    )
    classification_reason: Optional[str] = Field(
        default=None,
        description="One-sentence narrative of why the agent classified this way.",
    )
    decided_action: Optional[str] = Field(
        default=None,
        description=(
            "Controlled-vocabulary action the agent took. Values: "
            "delegated_to_bill_specialist | delegated_to_bill_credit_specialist "
            "| delegated_to_expense_specialist | delegated_to_contract_labor_specialist "
            "| flagged_needs_review | marked_irrelevant | marked_processed. "
            "`delegated_to_contract_labor_specialist` pairs with classification "
            "`contract_labor_timesheet` — the email gets routed to the "
            "contract_labor_specialist agent for ContractLabor row creation."
        ),
    )
    confidence: Optional[Decimal] = Field(
        default=None,
        ge=Decimal("0"), le=Decimal("1"),
        description=(
            "Agent's overall classification confidence in [0,1]. The "
            "email_specialist's prompt routes per the classification when "
            "this is >= 0.95, otherwise needs_review regardless of value."
        ),
    )


@router.patch("/email-messages/{public_id}/outcome")
def set_email_outcome_router(
    public_id: str,
    body: _OutcomeBody,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES, "can_update")),
):
    """Mark an email's processing outcome.

    Two side effects, both attempted; either failing doesn't block the
    other:
      1. Update EmailMessage.ProcessingStatus + LastError
      2. PATCH the source MS Graph message to APPEND the outcome category
         (preserves the human-applied input category — we don't strip it)
    """
    if body.outcome not in _OUTCOME_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid outcome '{body.outcome}'. Valid: {list(_OUTCOME_MAP.keys())}",
        )

    service = EmailMessageService()
    email = service.read_by_public_id(public_id=public_id)
    if not email:
        raise_not_found("Email message")

    new_status, outcome_category = _OUTCOME_MAP[body.outcome]

    # 1. DB-side state transition + agent classification stamp.
    service.update_status(
        id=email.id,
        processing_status=new_status,
        last_error=body.reason,
        agent_session_id=body.agent_session_id,
        agent_classification=body.classification,
        agent_classification_reason=body.classification_reason,
        agent_decided_action=body.decided_action,
        agent_classification_confidence=body.confidence,
    )

    # 2. Outlook category — append, don't replace, so the human's input
    #    category (Blue category) stays visible. Best-effort: log + carry
    #    on if Graph PATCH fails, since the DB state has already moved.
    graph_result: dict = {"status": "skipped"}
    if email.graph_message_id and email.mailbox_address:
        existing = list(_safe_categories_from_graph(email.graph_message_id, email.mailbox_address))
        # Avoid stamping the same outcome twice on re-runs.
        if outcome_category not in existing:
            new_categories = existing + [outcome_category]
            graph_result = mail_client.set_categories(
                message_id=email.graph_message_id,
                categories=new_categories,
                mailbox=email.mailbox_address,
            )
        else:
            graph_result = {"status": "already_tagged"}

    # 3. Red flag — the agent sets a flag on EVERY outcome stamp,
    #    including the dismissive ones (marked_irrelevant /
    #    marked_processed). Only a human clears the flag (manually in
    #    Outlook) after they've eyeballed the email + agreed with the
    #    agent's verdict. This is the visual guarantee that no agent
    #    decision can silently close an email — every outcome leaves a
    #    flag claiming human attention until acknowledged.
    #    Best-effort: log + carry on if Graph PATCH fails. PATCH-ing
    #    `flag.flagStatus = "flagged"` is idempotent — a no-op if the
    #    message is already flagged.
    flag_result: dict = {"status": "skipped"}
    if email.graph_message_id and email.mailbox_address:
        flag_result = mail_client.flag_message(
            message_id=email.graph_message_id,
            flagged=True,
            mailbox=email.mailbox_address,
        )

    # 4. Inquiry forward — on every needs_review stamp where `reason`
    #    is non-empty, enqueue a self-forward of the source email to
    #    invoice@. The agent's `reason` (composed as findings + ask
    #    per the email_specialist prompt's Step 10) becomes the email
    #    body; AP replies inline, and Step 1e on the next poll picks
    #    up the reply as an instruction to act on.
    #
    #    The prompt mandates `reason` on every needs_review, so in
    #    practice this fires for every flag. `classification_reason`
    #    is the audit narrative (persisted on the row) and does NOT
    #    trigger a forward by itself — only `reason` becomes the
    #    AP-facing email body.
    #    Failure-isolated — never blocks the outcome stamp.
    if body.outcome == "needs_review":
        question = (body.reason or "").strip()
        if question:
            from entities.email_message.business.inquiry_service import (
                AgentInquiryService,
            )
            AgentInquiryService().send_inquiry(
                email_public_id=public_id,
                question=question,
                confidence=(
                    float(body.confidence) if body.confidence is not None else None
                ),
            )

    return item_response({
        "public_id": public_id,
        "processing_status": new_status,
        "outcome_category": outcome_category,
        "graph_result": graph_result,
        "flag_result": flag_result,
    })


def _safe_categories_from_graph(graph_message_id: str, mailbox: str) -> list[str]:
    """Best-effort fetch of the message's current categories. On error
    return an empty list so we still attempt to apply the outcome."""
    try:
        full = mail_client.get_message(message_id=graph_message_id, include_body=False, mailbox=mailbox)
    except Exception as e:
        logger.warning(f"Could not read existing categories for {graph_message_id}: {e}")
        return []
    if full.get("status_code") != 200:
        return []
    return (full.get("email") or {}).get("categories") or []


# ─── Agent-driven typed-field overlay on EmailAttachment ────────────────────


class _ExtractedFieldsBody(BaseModel):
    vendor_name: Optional[str] = Field(default=None, description="Vendor name as the agent read it.")
    invoice_number: Optional[str] = Field(default=None, description="Invoice / DOC# / Bill #.")
    invoice_date: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD.")
    due_date: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD.")
    subtotal: Optional[Decimal] = Field(default=None, description="Pre-tax subtotal.")
    total_amount: Optional[Decimal] = Field(default=None, description="Final invoice total.")
    currency: Optional[str] = Field(default=None, description="ISO currency code (USD default).")


@router.patch("/email-attachments/{public_id}/extracted-fields")
def set_email_attachment_extracted_fields_router(
    public_id: str,
    body: _ExtractedFieldsBody,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES, "can_update")),
):
    """Agent-driven overlay onto EmailAttachment.Di* typed columns.

    Preserves the underlying DI extraction (status, raw JSON, model). The
    agent reads DI's prebuilt-layout output and pulls out semantic fields,
    then calls this endpoint to persist its own interpretation so
    search_email_sender_history can surface those fields downstream.
    """
    return item_response(
        EmailAttachmentExtractionService().record_extracted_fields_by_public_id(
            public_id,
            vendor_name=body.vendor_name,
            invoice_number=body.invoice_number,
            invoice_date=body.invoice_date,
            due_date=body.due_date,
            subtotal=body.subtotal,
            total_amount=body.total_amount,
            currency=body.currency,
        )
    )


# ─── Sender-history lookup keyed on FromAddress ─────────────────────────────


@router.get("/email-messages/sender-history")
def get_email_sender_history_router(
    from_email: str = Query(..., description="Sender SMTP address to look up."),
    exclude_public_id: Optional[str] = Query(
        default=None,
        description=(
            "Optional EmailMessage.PublicId to exclude from counts (used "
            "by an in-flight agent run so it doesn't see its own row in "
            "the prior totals — pass the same UUID the run was kicked "
            "off with)."
        ),
    ),
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """Aggregate prior context for an email sender. Powers the
    email_specialist agent's search_email_sender_history tool.

    Returns: prior_emails counts (by status / classification / action),
    counts of committed Bills/Expenses/BillCredits sourced from prior
    emails by this sender, and the distinct Vendor rows transitively
    associated via those committed Bills.
    """
    history = EmailMessageService().get_sender_history(
        from_email=from_email,
        exclude_public_id=exclude_public_id,
    )
    return item_response(history)
