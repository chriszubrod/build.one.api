"""Agent-facing tools for the ContractLabor entity.

Used by the contract_labor_specialist agent to materialize a forwarded
worker timesheet into a `ContractLabor` row that lands in the existing
React review queue (`status='pending_review'`).

Tools:
  create_contract_labor  →  POST /api/v1/contract-labor

Tool self-registers on import.
"""
from decimal import Decimal
from typing import Optional

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


# ─── Self-register ───────────────────────────────────────────────────────


for _tool in (
    create_contract_labor,
):
    register(_tool)
