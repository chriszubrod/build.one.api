"""Agent-facing tools for the TimeEntry entity.

V1 surface for the time_tracking_specialist agent — flag-only review of
iOS-submitted entries.

Read tools (no approval):
  validate_time_entry_completeness  → GET /api/v1/time-entries/{public_id}/validate-completeness

Write tools (no approval — flag is observability, not a status change):
  flag_time_entry_for_human_review  → POST /api/v1/time-entries/{public_id}/review-flag

Tools self-register on import.
"""
from typing import List

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# ─── Arg shapes ──────────────────────────────────────────────────────────

class _PublicIdArgs(BaseModel):
    public_id: str = Field(description="The TimeEntry's public_id (UUID).")


class _FlagArgs(BaseModel):
    public_id: str = Field(description="The TimeEntry's public_id (UUID).")
    priority: str = Field(
        description=(
            "ReviewPriority bucket. Must be one of:\n"
            "  'high'   — any of (over_12_hours, future_dated, no_time_logs), or 3+ reasons\n"
            "  'medium' — any single reason from the validation report\n"
            "  'low'    — minor issue not in the deterministic checklist (rarely used in v1)\n"
            "  'clean'  — empty reasons; entry passes validation"
        ),
    )
    reasons: List[str] = Field(
        default_factory=list,
        description=(
            "List of reason codes from the validation report. Pass the "
            "`reasons` array from validate_time_entry_completeness verbatim. "
            "Empty list when priority='clean'. Codes outside the canonical "
            "vocabulary are rejected by the server."
        ),
    )


# ─── Read tools ──────────────────────────────────────────────────────────

async def _validate_time_entry_completeness(
    args: dict, ctx: ToolContext
) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "GET",
        f"/api/v1/time-entries/{parsed.public_id}/validate-completeness",
    )


validate_time_entry_completeness = Tool(
    name="validate_time_entry_completeness",
    description=(
        "Run the deterministic completeness + anomaly checklist on one "
        "TimeEntry. Read-only — no mutation, no status transition.\n\n"
        "Returns a structured report:\n"
        "  is_complete: bool (true iff `reasons` is empty)\n"
        "  reasons: array of short codes from this fixed vocabulary —\n"
        "    no_time_logs        — entry has zero TimeLog rows\n"
        "    null_project        — at least one TimeLog has ProjectId NULL\n"
        "    missing_clockout    — at least one TimeLog has ClockOut NULL\n"
        "    overnight_shift     — a TimeLog crosses midnight\n"
        "    over_12_hours       — total work hours > 12 in this entry\n"
        "    under_15_minutes    — total work hours > 0 but < 0.25 (fat-finger)\n"
        "    future_dated        — WorkDate is after today\n"
        "    gps_no_project      — a TimeLog has GPS captured but ProjectId NULL\n"
        "  summary: { work_date, log_count, work_log_count, total_work_hours }\n\n"
        "Use this as the first call when reviewing a submitted TimeEntry. "
        "The reason codes drive your ReviewPriority decision:\n"
        "  Any reason → ReviewPriority is at minimum 'medium'.\n"
        "  Multiple reasons OR any of (over_12_hours, future_dated, "
        "no_time_logs) → 'high'.\n"
        "  Empty reasons → 'clean'.\n"
        "Then flag the entry via the flag-stamping tool."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_validate_time_entry_completeness,
)


# ─── Write tools ─────────────────────────────────────────────────────────

async def _flag_time_entry_for_human_review(
    args: dict, ctx: ToolContext
) -> ToolResult:
    parsed = _FlagArgs(**args)
    return await ctx.call_api(
        "POST",
        f"/api/v1/time-entries/{parsed.public_id}/review-flag",
        body={"priority": parsed.priority, "reasons": parsed.reasons},
    )


flag_time_entry_for_human_review = Tool(
    name="flag_time_entry_for_human_review",
    description=(
        "Stamp ReviewPriority + ReviewReasons on a TimeEntry. NO STATUS "
        "TRANSITION — the entry stays in 'submitted' regardless. The human "
        "Approver sees the priority on /time-entry/list and decides whether "
        "to approve or reject.\n\n"
        "Call this exactly once per agent run after validate_time_entry_completeness. "
        "Mapping rule (apply strictly):\n"
        "  • reasons is empty                                       → priority='clean'\n"
        "  • reasons contains any of:                              → priority='high'\n"
        "      over_12_hours, future_dated, no_time_logs\n"
        "    OR reasons has 3 or more codes\n"
        "  • otherwise (1-2 reasons, none from the high-list)       → priority='medium'\n"
        "  • 'low' is reserved for non-deterministic concerns; don't use in v1.\n\n"
        "Pass the validation tool's `reasons` array verbatim — server "
        "validates each code against the canonical vocabulary."
    ),
    input_schema=input_schema_from(_FlagArgs),
    handler=_flag_time_entry_for_human_review,
)


# ─── Registration ────────────────────────────────────────────────────────

for _tool in (
    validate_time_entry_completeness,
    flag_time_entry_for_human_review,
):
    register(_tool)
