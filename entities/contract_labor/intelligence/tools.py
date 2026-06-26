"""Agent-facing tools for the ContractLabor entity.

Used by the contract_labor_specialist agent to materialize a forwarded
worker timesheet into a `ContractLabor` row (`status='pending_review'`)
that lands in the existing React review queue — and to bind a PM/Owner
reply email back to a tracked (ContractLabor, Project) pair so Unit 3's
apply path can apply the reviewer's decision.

Tools:
  create_contract_labor                    →  POST /api/v1/contract-labor
  find_contract_labor_by_conversation_id   →  GET  /api/v1/contract-labor/find-by-conversation-id

Tools self-register on import.
"""
from decimal import Decimal
from typing import Optional
from urllib.parse import quote

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# ─── Arg shape ──────────────────────────────────────────────────────────


class _CreateContractLaborArgs(BaseModel):
    vendor_public_id: str = Field(
        description=(
            "Public ID of the contract-labor Vendor (UUID). Resolve via "
            "`find_contract_labor_vendor_by_email` first — never guess "
            "or fall back to `search_vendors`."
        ),
    )
    employee_name: str = Field(
        max_length=255,
        description=(
            "Worker's name as it appears in the timesheet text "
            "(e.g. `John Randall Scruggs` or `JR Scruggs`). For "
            "consistency, prefer the Vendor's full Name when known."
        ),
    )
    work_date: str = Field(
        description=(
            "Work date in ISO `YYYY-MM-DD` format. Derive from the email "
            "subject (e.g. `Work Hours 5/11` → `2026-05-11` using the "
            "email's ReceivedDatetime year)."
        ),
    )
    total_hours: Decimal = Field(
        description=(
            "Hours worked, as a Decimal. Compute from `time_out - "
            "time_in` to 2 decimal places (e.g. 3:55 PM → 5:00 PM = "
            "`1.08`)."
        ),
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Public ID of the resolved Project (UUID), or `null` when "
            "the address couldn't be matched to a Project. Resolve via "
            "`delegate_to_project_specialist` with the job-site address "
            "from the timesheet."
        ),
    )
    time_in: Optional[str] = Field(
        default=None,
        max_length=20,
        description=(
            "Clock-in time as 24-hour `HH:MM` (e.g. `15:55` for 3:55 "
            "PM). Construction crews work afternoons unless the email "
            "explicitly says AM; default ambiguous times to PM."
        ),
    )
    time_out: Optional[str] = Field(
        default=None,
        max_length=20,
        description=(
            "Clock-out time as 24-hour `HH:MM`. Pair with `time_in` to "
            "validate `total_hours`."
        ),
    )
    job_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description=(
            "Raw job-site address as the worker wrote it (e.g. `206 "
            "Haverford Ave`). Preserved verbatim for audit even when "
            "`project_public_id` resolves cleanly — the human reviewer "
            "uses this to confirm the project binding."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description=(
            "Work-scope text from the timesheet (e.g. `Installed door "
            "hardware`). Pass verbatim; the human reviewer edits during "
            "the pending_review → ready transition."
        ),
    )
    status: Optional[str] = Field(
        default="pending_review",
        description=(
            "Workflow status. Always `pending_review` for agent-created "
            "rows — leaves the row in the human review queue for "
            "rate/markup/SCC entry before `mark_as_ready` flips it."
        ),
    )


# ─── Handler ────────────────────────────────────────────────────────────


async def _create_contract_labor(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _CreateContractLaborArgs(**args)
    body: dict = {
        "vendor_public_id": parsed.vendor_public_id,
        "employee_name":    parsed.employee_name,
        "work_date":        parsed.work_date,
        "total_hours":      str(parsed.total_hours),
        "status":           parsed.status or "pending_review",
    }
    if parsed.project_public_id is not None: body["project_public_id"] = parsed.project_public_id
    if parsed.time_in is not None:           body["time_in"]           = parsed.time_in
    if parsed.time_out is not None:          body["time_out"]          = parsed.time_out
    if parsed.job_name is not None:          body["job_name"]          = parsed.job_name
    if parsed.description is not None:       body["description"]       = parsed.description
    return await ctx.call_api("POST", "/api/v1/contract-labor", body=body)


create_contract_labor = Tool(
    name="create_contract_labor",
    description=(
        "Create a ContractLabor row from a forwarded worker timesheet. "
        "The row lands in the React review queue with "
        "`status='pending_review'` so a human can add rate / markup / "
        "SubCostCode before flipping it to `ready` for billing.\n\n"
        "**MVP scope:** vendor + project + work_date + time_in/out + "
        "total_hours + job_name + description. **Leave rate / markup / "
        "SubCostCode UNSET** — those are the human reviewer's job. The "
        "row is incomplete-by-design.\n\n"
        "**Prerequisites** (do these in your run BEFORE calling this "
        "tool):\n"
        "  1. `find_contract_labor_vendor_by_email(email=<sender>)` → "
        "     vendor_public_id\n"
        "  2. `delegate_to_project_specialist(task=<address>)` → "
        "     project_public_id (or null if no match — pass null then)\n"
        "  3. Parse work_date + time_in/out + total_hours from the "
        "     subject + body\n\n"
        "No approval gate — the row is a draft awaiting human review. "
        "If the API returns 422 (vendor not found, etc.), do NOT retry; "
        "report back so email_specialist stamps `flagged_needs_review`."
    ),
    input_schema=input_schema_from(_CreateContractLaborArgs),
    handler=_create_contract_labor,
)


# ─── find_contract_labor_by_conversation_id ─────────────────────────


class _FindByConversationIdArgs(BaseModel):
    conversation_id: str = Field(
        description=(
            "MS Graph ConversationId from the inbound reply email. The "
            "tool finds the outbound `Contract Labor - {Worker} - "
            "{ProjectAbbr} - {YYYY-MM-DD}` notification on the same "
            "conversation and binds the reply back to its (CL, Project) "
            "pair."
        ),
    )
    worker_hint: Optional[str] = Field(
        default=None,
        description=(
            "Optional worker name parsed from the REPLY's own subject "
            "(`Re: Contract Labor - {Worker} - ...`). Enables the fuzzy "
            "fallback when conversation_id misses (rare). Required "
            "alongside project_hint + work_date_hint for the fallback "
            "to fire."
        ),
    )
    project_hint: Optional[str] = Field(
        default=None,
        description=(
            "Optional project abbreviation parsed from the REPLY subject "
            "(e.g. `TB3`, `HA`, `WVA`). Part of the fuzzy fallback "
            "triple."
        ),
    )
    work_date_hint: Optional[str] = Field(
        default=None,
        description=(
            "Optional work_date parsed from the REPLY subject in "
            "`YYYY-MM-DD` form. Part of the fuzzy fallback triple."
        ),
    )


async def _find_contract_labor_by_conversation_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _FindByConversationIdArgs(**args)
    qs = f"conversation_id={quote(parsed.conversation_id)}"
    if parsed.worker_hint:
        qs += f"&worker_hint={quote(parsed.worker_hint)}"
    if parsed.project_hint:
        qs += f"&project_hint={quote(parsed.project_hint)}"
    if parsed.work_date_hint:
        qs += f"&work_date_hint={quote(parsed.work_date_hint)}"
    return await ctx.call_api(
        "GET", f"/api/v1/contract-labor/find-by-conversation-id?{qs}"
    )


find_contract_labor_by_conversation_id = Tool(
    name="find_contract_labor_by_conversation_id",
    description=(
        "Bind a PM/Owner reply email back to the (ContractLabor, "
        "Project) pair it's reviewing. Use this as the first step of "
        "the reviewer-reply branch when the inbound email looks like a "
        "reply on a tracked CL notification thread (`Re: Contract "
        "Labor - {Worker} - {ProjectAbbr} - {YYYY-MM-DD}`).\n\n"
        "Returns a slim payload (`contract_labor_public_id`, "
        "`project_public_id`, `project_abbreviation`, `parsed_worker`, "
        "`parsed_work_date`, `contract_labor_status`, `match_kind`) "
        "when a single CL row resolves on that conversation, OR null "
        "when no match / ambiguous.\n\n"
        "`match_kind` will be `'conversation'` for strict ConversationId "
        "matches and `'fuzzy'` for fallback hits. The downstream apply "
        "path is identical either way.\n\n"
        "**Fuzzy fallback:** when ConversationId doesn't resolve "
        "(non-Outlook clients sometimes lose it), pass all three "
        "hints (`worker_hint`, `project_hint`, `work_date_hint`) parsed "
        "from the reply's own subject. Single-result-or-null is "
        "preserved — ambiguous cases return null so the email_specialist "
        "stamps `flagged_needs_review`.\n\n"
        "Null result means: not a tracked CL conversation (or "
        "ambiguous). Report back to email_specialist; do NOT proceed "
        "to apply."
    ),
    input_schema=input_schema_from(_FindByConversationIdArgs),
    handler=_find_contract_labor_by_conversation_id,
)


# ─── apply_contract_labor_reviewer_decision ─────────────────────────


class _ApplyReviewerDecisionArgs(BaseModel):
    contract_labor_public_id: str = Field(
        description="The ContractLabor's public_id (UUID). Get from `find_contract_labor_by_conversation_id`.",
    )
    project_public_id: str = Field(
        description=(
            "The matched Project's public_id (UUID). Comes from "
            "`find_contract_labor_by_conversation_id` — represents the "
            "specific project the PM's reply is about (CL notifications "
            "are sent per-project, so one CL with multiple projects "
            "produces multiple separate reviewer conversations)."
        ),
    )
    decision: str = Field(
        description=(
            "'approved' or 'rejected'. 'rejected' covers needs-revision / "
            "questions / out-of-band issues — the AP reviewer reads the "
            "raw_reply_text and triages."
        ),
    )
    reviewer_email: str = Field(
        description=(
            "The PM/Owner's email address (the inbound reply's from_address). "
            "Must match a UserProject → Role 'Project Manager' or 'Owner' on "
            "the project. Authz check runs server-side via "
            "ResolveContractLaborReviewRecipientsPerProject."
        ),
    )
    sub_cost_code_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Required on approval; SCC to apply to each ContractLaborLineItem "
            "on the matched project. Resolve via "
            "`find_sub_cost_code_for_reply` (shared with bill_specialist) "
            "from the PM's SCC text shorthand ('13.1', 'Cleaning - 62.0', etc.)."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description=(
            "Optional description text from the PM's approval reply. When "
            "supplied, REPLACES the description on each matched line item. "
            "When None, the existing per-line descriptions are preserved."
        ),
    )
    raw_reply_text: Optional[str] = Field(
        default=None,
        description=(
            "Full new-text portion of the reply (post-quote-stripping). "
            "Persisted verbatim to Review.Comments — AP reads this in the "
            "React review queue."
        ),
    )
    reviewer_email_message_public_id: Optional[str] = Field(
        default=None,
        description=(
            "The reply EmailMessage's public_id. Persisted to "
            "Review.EmailMessageId so the React review queue can navigate "
            "back to the source reply."
        ),
    )


async def _apply_contract_labor_reviewer_decision(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _ApplyReviewerDecisionArgs(**args)
    body = {
        'project_public_id': parsed.project_public_id,
        'decision': parsed.decision,
        'reviewer_email': parsed.reviewer_email,
    }
    if parsed.sub_cost_code_public_id is not None:
        body['sub_cost_code_public_id'] = parsed.sub_cost_code_public_id
    if parsed.description is not None:
        body['description'] = parsed.description
    if parsed.raw_reply_text is not None:
        body['raw_reply_text'] = parsed.raw_reply_text
    if parsed.reviewer_email_message_public_id is not None:
        body['reviewer_email_message_public_id'] = parsed.reviewer_email_message_public_id
    return await ctx.call_api(
        'POST',
        f'/api/v1/contract-labor/{parsed.contract_labor_public_id}/apply-reviewer-decision',
        body=body,
    )


apply_contract_labor_reviewer_decision = Tool(
    name='apply_contract_labor_reviewer_decision',
    description=(
        "Apply a Project Manager / Owner's emailed approval or rejection "
        "to a ContractLabor row by writing an insert-only Review row "
        "(mirrors bill_specialist.apply_reviewer_decision exactly, using "
        "the existing Review entity).\n\n"
        "**Flow** (call after find_contract_labor_by_conversation_id "
        "resolves the (CL, Project) pair):\n"
        "  1. (Approval only) Resolve the PM's SCC shorthand via "
        "`find_sub_cost_code_for_reply`\n"
        "  2. Call this tool with (contract_labor_public_id, "
        "project_public_id, decision, reviewer_email, "
        "sub_cost_code_public_id, ...).\n\n"
        "**Side effects:**\n"
        "  - Approval: every ContractLaborLineItem on the matched "
        "Project gets the SCC + (optional) description applied. Other "
        "line fields (hours/rate/markup) are preserved.\n"
        "  - Overhead lines (NULL ProjectId, IsOverhead=true) are "
        "silently skipped — the PM is reviewing a specific project, "
        "not the worker's overhead allocation.\n"
        "  - When `description` is supplied AND multiple line items on "
        "the same project match, the same description string overwrites "
        "ALL of them (flattens distinct per-line descriptions). When "
        "`description` is omitted, per-line descriptions are preserved. "
        "Only pass `description` when the PM clearly intends a "
        "project-wide overwrite.\n"
        "  - Both: a new Review row is inserted with the chosen "
        "ReviewStatus (approved → first IsFinal-non-Declined; rejected "
        "→ first IsDeclined), the reviewer's user_id, raw_reply_text "
        "as Comments, and EmailMessageId FK linking back to the reply.\n"
        "  - The Review row is written BEFORE line-item updates so the "
        "audit trail is always captured. On partial line-item failure "
        "(rare; row-version conflict during scheduler aggregation), the "
        "tool returns a 400 with a structured 'partial-failure' "
        "message — AP reconciles via the React queue.\n"
        "  - On approval, an auto-mirror (mirrors ReviewService.create's "
        "canonical hook) flips ContractLabor.Status pending_review → "
        "ready so Generate Bills picks it up — same behavior as the "
        "React /advance/review path. On rejection, Status is untouched.\n\n"
        "**Multi-SCC bailout:** if the PM's reply mentions 2+ distinct "
        "SCCs (e.g. `Cleaning - 62.0 (...) Trim Labor - 44.0 (...)`), "
        "DO NOT call this tool. Report back to email_specialist with "
        "the parsed SCC list so it can stamp `flagged_needs_review` "
        "(per design Q1). Auto-splitting hours across SCCs is out of v1.\n\n"
        "**Error responses (HTTP 400)** the agent should expect:\n"
        "  - 'ContractLabor X is no longer pending_review' → CL has "
        "advanced; tell email_specialist to classify `internal_reply` "
        "+ `marked_irrelevant` (decision arrived too late).\n"
        "  - 'Sender X is not an authorized reviewer' → from-address "
        "isn't a PM/Owner on this project; classify same as above.\n"
        "  - 'SubCostCode with public_id X not found' → pass the SCC's "
        "public_id verbatim from find_sub_cost_code_for_reply (don't "
        "pass the human-readable name).\n\n"
        "Do NOT retry on any of these; report back so email_specialist "
        "stamps the right outcome."
    ),
    input_schema=input_schema_from(_ApplyReviewerDecisionArgs),
    handler=_apply_contract_labor_reviewer_decision,
)


# ─── Self-register ───────────────────────────────────────────────────────


for _tool in (
    create_contract_labor,
    find_contract_labor_by_conversation_id,
    apply_contract_labor_reviewer_decision,
):
    register(_tool)
