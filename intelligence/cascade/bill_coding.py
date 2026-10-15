"""Cascade task: bill-level SubCostCode (+ optional project) suggestion from text.

Payload keys (only these may appear on the dict passed to build_user_message):
    vendor_name: str
    bill_text: str — truncated to BILL_TEXT_CHAR_BUDGET (default 6000)
    history_evidence: list[{scc_number, scc_name, count}] — top 5, may be empty
    candidate_sccs: list[{number, name}] — full catalog slice presented to the model
    candidate_projects: list[{abbreviation, name}] | None — None when project is fixed (Mode A)

Output JSON:
    scc_number, project_abbreviation|null, confidence 0..1, rationale (<= 200 chars)
"""
from __future__ import annotations

import dataclasses
import json
from contextvars import ContextVar
from typing import Any

from intelligence.cascade.core import CascadeResult, Rung, StructuredTask, run_cascade

BILL_TEXT_CHAR_BUDGET = 6000
SUSPENSE_SCC_NUMBER = "00.02"
BILL_CODING_THRESHOLD = 0.85

BILL_CODING_FOUNDRY_LADDER: tuple[Rung, ...] = (
    Rung("foundry", "DeepSeek-V4-Flash"),
    Rung("foundry", "gpt-5.4-nano"),
    Rung("foundry", "gpt-5.4-mini"),
)

ALLOWED_PAYLOAD_KEYS = frozenset({
    "vendor_name",
    "bill_text",
    "history_evidence",
    "candidate_sccs",
    "candidate_projects",
})

_bill_coding_payload: ContextVar[dict[str, Any] | None] = ContextVar(
    "_bill_coding_payload", default=None
)

_SYSTEM_PROMPT = (
    "You code vendor Bills for a construction bookkeeping system. Each Bill receives "
    "exactly ONE SubCostCode (SCC) — the dominant code for the whole Bill.\n\n"
    "Rules:\n"
    "- Choose scc_number ONLY from candidate_sccs; never invent a code.\n"
    "- Never choose SCC 00.02 (Suspense).\n"
    "- When history_evidence strongly matches the bill text, prefer that SCC unless "
    "the text clearly contradicts it.\n"
    "- When candidate_projects is null, the project is already chosen; set "
    "project_abbreviation to null.\n"
    "- When candidate_projects is a list, pick project_abbreviation from that list or "
    "null if truly unknown.\n\n"
    "Respond with ONLY a JSON object:\n"
    '{"scc_number": "<code>", "project_abbreviation": "<abbrev or null>", '
    '"confidence": <0..1>, "rationale": "<=200 chars>"}\n'
    "confidence is your calibrated probability that scc_number is correct."
)


def normalize_scc_number(value: str) -> str:
    return (value or "").strip()


def normalize_project_abbreviation(value: str) -> str:
    return (value or "").strip()


def truncate_bill_text(text: str, budget: int = BILL_TEXT_CHAR_BUDGET) -> str:
    text = (text or "").strip()
    if len(text) <= budget:
        return text
    return text[:budget] + "\n…(truncated)"


def _build_user_message(payload: dict[str, Any]) -> str:
    extra = set(payload.keys()) - ALLOWED_PAYLOAD_KEYS
    if extra:
        raise ValueError(f"unexpected payload keys: {sorted(extra)}")
    _bill_coding_payload.set(payload)
    safe = {
        "vendor_name": payload.get("vendor_name", ""),
        "bill_text": truncate_bill_text(payload.get("bill_text") or ""),
        "history_evidence": payload.get("history_evidence") or [],
        "candidate_sccs": payload.get("candidate_sccs") or [],
        "candidate_projects": payload.get("candidate_projects"),
    }
    return json.dumps(safe, ensure_ascii=False, indent=2)


def _candidate_scc_numbers(payload: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for row in payload.get("candidate_sccs") or []:
        if isinstance(row, dict) and isinstance(row.get("number"), str):
            out.add(normalize_scc_number(row["number"]))
    return out


def _candidate_project_abbrevs(payload: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for row in payload.get("candidate_projects") or []:
        if isinstance(row, dict) and isinstance(row.get("abbreviation"), str):
            out.add(normalize_project_abbreviation(row["abbreviation"]))
    return out


def validate_bill_coding_result(
    result: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Deterministic gate for parsed model JSON."""
    scc = result.get("scc_number")
    if not isinstance(scc, str) or not scc.strip():
        return False, "missing/non-string scc_number"
    scc = normalize_scc_number(scc)
    if scc == normalize_scc_number(SUSPENSE_SCC_NUMBER):
        return False, "suspense SCC 00.02 refused"

    proj = result.get("project_abbreviation")
    if proj is not None and not isinstance(proj, str):
        return False, "project_abbreviation must be string or null"

    rationale = result.get("rationale")
    if rationale is not None and isinstance(rationale, str) and len(rationale) > 200:
        return False, "rationale exceeds 200 chars"

    if payload is not None:
        catalog = _candidate_scc_numbers(payload)
        if scc not in catalog:
            return False, f"scc_number '{scc}' not in candidate_sccs"
        candidates = payload.get("candidate_projects")
        if candidates is None:
            if proj is not None:
                return False, "project must be null when candidate_projects is null"
        else:
            abbrevs = _candidate_project_abbrevs(payload)
            if proj is not None and normalize_project_abbreviation(proj) and (
                normalize_project_abbreviation(proj) not in abbrevs
            ):
                return False, f"project_abbreviation '{proj}' not in candidate_projects"
    return True, "ok"


def _validate(result: dict[str, Any]) -> tuple[bool, str]:
    payload = _bill_coding_payload.get()
    if payload is None:
        return False, "no_payload_context"
    return validate_bill_coding_result(result, payload=payload)


BILL_CODING_SUGGESTION_TASK = StructuredTask(
    name="bill_coding_suggestion",
    system_prompt=_SYSTEM_PROMPT,
    threshold=BILL_CODING_THRESHOLD,
    build_user_message=_build_user_message,
    validate=_validate,
    ladder=BILL_CODING_FOUNDRY_LADDER,
)


async def run_bill_coding_suggestion(
    payload: dict[str, Any],
    *,
    transport_for=None,
) -> CascadeResult:
    """Run the bill coding ladder with payload-aware catalog validation."""
    def validate(result: dict[str, Any]) -> tuple[bool, str]:
        return validate_bill_coding_result(result, payload=payload)

    task = dataclasses.replace(BILL_CODING_SUGGESTION_TASK, validate=validate)
    return await run_cascade(task, payload, transport_for=transport_for)
