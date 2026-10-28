"""Bill coding ladder V2 decision core (U-701b3): history, cascade roles, filtered vote.

One decision for the PDF drop (stage "drop") and the background tick ("tick_full" /
"tick_escalate"). Order (owner decisions 2026-10-28): strong history wins, else
Haiku (accepted at >= 0.70) -> Sonnet -> Foundry only when Claude gave no valid answer
-> the FILTERED similar-bills vote; if all of those give nothing the code stays blank.

Mode A (user chose the project): strong history for that project short-circuits before
any model call. Mode B (no project, U-676k "text project first"): the model runs first;
its project P then keys history - strong history FOR P overrides the code; with no
model project, strong vendor-wide history applies, as the drop path does.

Similar-bills scope: decide_bill_coding does the neighbour lookup itself from
``inputs.scope`` (never from neighbours a caller hands in), so a caller cannot pass
neighbours gathered under a wider scope. The lookup is best-effort: no service, no
token hashes, an empty accessible set or any error -> no neighbours.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Literal, Optional, Sequence

from entities.coding_suggestion.business.bill_suggestion_service import (
    BillSuggestion,
    history_blocks_text_tier,
)
from entities.coding_suggestion.business.similar_bills import (
    Neighbour,
    VisibilityFilter,
    apply_similar_evidence,
    filtered_vote,
)
from intelligence.cascade.bill_coding import (
    BILL_CODING_SUGGESTION_TASK_V2,
    BILL_CODING_THRESHOLD,
    FULL_CONTENT_CHAR_CAP,
    should_escalate,
    truncate_bill_text,
    validate_bill_coding_result,
)
from intelligence.cascade.bill_coding_payload import (
    build_candidate_projects_for_bill,
    history_evidence_from_scc_counts,
    resolve_text_prediction,
)
from intelligence.cascade.core import (
    Attempt,
    CascadeResult,
    Rung,
    _attempt_error_code,
    run_cascade,
)
from intelligence.cascade.ladder_config import (
    DEFAULT_BILL_CODING_LADDER_V2,
    drop_ladder,
    escalate_ladder,
)

logger = logging.getLogger(__name__)

DecisionSource = Literal[
    "history", "primary", "escalate", "fallback", "similar_vote", "none", "reused_intake"
]
CodingMode = Literal["A", "B"]
DecisionStage = Literal["drop", "tick_full", "tick_escalate"]
DropEscalation = Literal["low_confidence", "claude_unavailable"]

# The model sees the first 5 neighbours (measured arm, scripts/u654a_text_backtest.py);
# the filtered vote runs over all DEFAULT_TOP_K (10).
SIMILAR_BILLS_TOP_K = 5
DROP_ESCALATE_BELOW = 0.70
ERROR_CODE_MAX_LEN = 50


@dataclass(frozen=True)
class RungAnswer:
    model: Optional[str]
    sub_cost_code_id: Optional[int]
    project_id: Optional[int]
    confidence: Optional[Decimal]
    error: Optional[str] = None


@dataclass(frozen=True)
class SimilarScope:
    """Who the similar-bills lookup runs as. accessible_project_ids None = all projects;
    an empty frozenset = no neighbours. company_id None = unknown -> no neighbours."""

    company_id: Optional[int]
    actor_user_id: Optional[int]
    accessible_project_ids: Optional[frozenset[int]]


@dataclass(frozen=True)
class BillCodingInputs:
    stage: DecisionStage
    vendor_id: int
    vendor_name: str
    chosen_project_id: Optional[int]
    bill_text: str  # extraction CONTENT only - never memo / line descriptions
    query_token_hashes: frozenset[int]
    amount: Optional[Decimal]  # None = no total read -> no neighbours (see scoped_neighbours)
    as_of: Any
    exclude_bill_id: Optional[int]
    scope: SimilarScope
    prior_primary: Optional[RungAnswer] = None


@dataclass(frozen=True)
class BillCodingDecision:
    source: DecisionSource
    mode: CodingMode
    sub_cost_code_id: Optional[int]
    project_id: Optional[int]
    confidence: Optional[Decimal]
    model: Optional[str]
    primary: Optional[RungAnswer]
    escalate: Optional[RungAnswer]
    fallback: Optional[RungAnswer]
    similar_vote_scc_id: Optional[int]
    similar_vote_confidence: Optional[Decimal]
    similar_neighbour_count: int
    escalation: Optional[DropEscalation]
    stopped_reason: Optional[str]
    cascades: tuple[CascadeResult, ...]
    history: Optional[BillSuggestion]


@dataclass(frozen=True)
class BillCodingCatalog:
    scc_by_id: dict[int, Any]
    scc_number_to_id: dict[str, int]
    ambiguous_scc_numbers: frozenset[str]
    proj_abbrev_to_id: dict[str, int]
    ambiguous_project_abbrevs: frozenset[str]
    candidate_projects: list[dict[str, str]]
    # The FULL as-of SCC catalog for the bill (holding codes excluded) - what
    # apply_similar_evidence / filtered_vote filter neighbours against.
    as_of_catalog: list[dict[str, str]]


@dataclass(frozen=True)
class BillCodingDeps:
    catalog: BillCodingCatalog
    history_rows: Sequence[Any]
    # History for the chosen project (Mode A) or vendor-wide (Mode B).
    history_suggestion: Optional[BillSuggestion]
    # The (possibly narrowed) payload candidate list; neighbours may add to it.
    candidate_sccs: list[dict[str, str]]
    candidate_scc_narrowed: bool
    # Object with neighbours_for(...) (SimilarBillsService); None = no neighbours.
    similar_service: Optional[Any] = None
    # Mode B: history for the model's project P.
    history_for_project: Optional[Callable[[int], Optional[BillSuggestion]]] = None
    run_cascade: Optional[
        Callable[
            [dict[str, Any], tuple[Rung, ...], Optional[Callable[[str], Any]]],
            Any,
        ]
    ] = None
    transport_for: Optional[Callable[[str], Any]] = None


def history_is_strong(suggestion: Optional[BillSuggestion]) -> bool:
    return suggestion is not None and history_blocks_text_tier(suggestion)


def rung_error_code(error: Optional[str]) -> Optional[str]:
    """Short, provider-text-free code for the *Error columns."""
    if not error:
        return None
    if error.startswith("skipped:"):
        return "skipped"
    if error == "no parseable result":
        return "unparseable"
    code = _attempt_error_code(error)
    if code is None:
        return "error"
    if code.startswith("claude_unavailable:"):
        code = code[len("claude_unavailable:"):]
    return code[:ERROR_CODE_MAX_LEN]


def _decimal_conf(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value))


def _attempt_rung_answer(
    att: Attempt,
    *,
    scc_number_to_id: dict[str, int],
    proj_abbrev_to_id: dict[str, int],
) -> RungAnswer:
    if not att.validated or att.result is None:
        return RungAnswer(
            model=att.rung.model,
            sub_cost_code_id=None,
            project_id=None,
            confidence=_decimal_conf(att.confidence),
            error=rung_error_code(att.error) or "not_validated",
        )
    shim = CascadeResult(
        accepted=True,
        needs_human=False,
        result=att.result,
        confidence=att.confidence,
        winning_rung=att.rung,
        attempts=[],
    )
    scc_id, proj_id = resolve_text_prediction(
        shim,
        scc_number_to_id=scc_number_to_id,
        proj_abbrev_to_id=proj_abbrev_to_id,
    )
    return RungAnswer(
        model=att.rung.model,
        sub_cost_code_id=scc_id,
        project_id=proj_id,
        confidence=_decimal_conf(att.confidence),
        error=rung_error_code(att.error),
    )


def map_cascade_role_answers(
    cascade: CascadeResult,
    *,
    scc_number_to_id: dict[str, int],
    proj_abbrev_to_id: dict[str, int],
    escalate_only: bool = False,
) -> tuple[Optional[RungAnswer], Optional[RungAnswer], Optional[RungAnswer]]:
    """(primary, escalate, fallback) from the attempts.

    primary = first non-fallback attempt, escalate = second, fallback = first validated
    fallback_only attempt. escalate_only (stage tick_escalate, ladder = Sonnet only): the
    first non-fallback attempt IS the escalate role and primary stays None - the caller
    supplies the intake's Haiku answer as the primary.
    """
    primary: Optional[RungAnswer] = None
    escalate: Optional[RungAnswer] = None
    fallback: Optional[RungAnswer] = None
    roles = ("escalate",) if escalate_only else ("primary", "escalate")
    non_fallback_seen = 0
    for att in cascade.attempts:
        if att.rung.fallback_only:
            if fallback is None and att.validated and att.result is not None:
                fallback = _attempt_rung_answer(
                    att,
                    scc_number_to_id=scc_number_to_id,
                    proj_abbrev_to_id=proj_abbrev_to_id,
                )
            continue
        if non_fallback_seen < len(roles):
            answer = _attempt_rung_answer(
                att,
                scc_number_to_id=scc_number_to_id,
                proj_abbrev_to_id=proj_abbrev_to_id,
            )
            if roles[non_fallback_seen] == "primary":
                primary = answer
            else:
                escalate = answer
        non_fallback_seen += 1
    return primary, escalate, fallback


def _rung_valid(answer: Optional[RungAnswer]) -> bool:
    return (
        answer is not None
        and answer.sub_cost_code_id is not None
        and not answer.error
    )


def _fallback_accepted(
    answer: Optional[RungAnswer],
    *,
    threshold: float = BILL_CODING_THRESHOLD,
) -> bool:
    if not _rung_valid(answer) or answer.confidence is None:
        return False
    return float(answer.confidence) >= threshold


def _model_pick(
    primary: Optional[RungAnswer],
    escalate: Optional[RungAnswer],
    fallback: Optional[RungAnswer],
) -> Optional[tuple[DecisionSource, RungAnswer]]:
    """escalate if valid; else primary if valid (any confidence); else Foundry >= 0.5."""
    if _rung_valid(escalate):
        return "escalate", escalate
    if _rung_valid(primary):
        return "primary", primary
    if _fallback_accepted(fallback):
        return "fallback", fallback
    return None


def _model_project(
    primary: Optional[RungAnswer],
    escalate: Optional[RungAnswer],
    fallback: Optional[RungAnswer],
) -> Optional[int]:
    """The model's project P (U-676k): kept even when the answer's SCC did not resolve
    (V1 retain_accessible_project). Sonnet's project first; a Sonnet answer with no
    project keeps Haiku's; an accepted Foundry answer last."""
    for answer in (escalate, primary):
        if answer is not None and not answer.error and answer.project_id is not None:
            return answer.project_id
    if (
        fallback is not None
        and not fallback.error
        and fallback.project_id is not None
        and fallback.confidence is not None
        and float(fallback.confidence) >= BILL_CODING_THRESHOLD
    ):
        return fallback.project_id
    return None


def mode_b_text_project(
    primary: Optional[RungAnswer],
    escalate: Optional[RungAnswer],
    fallback: Optional[RungAnswer],
    *,
    project_accessible: Callable[[int], bool],
) -> Optional[int]:
    """P after the access check; an inaccessible P is treated as no project (as V1)."""
    project_id = _model_project(primary, escalate, fallback)
    if project_id is None or not project_accessible(project_id):
        return None
    return project_id


def scope_project_accessible(scope: SimilarScope) -> Callable[[int], bool]:
    """The access check V1 runs (resolve_accessible_text_project_id), from the decision's
    scope instead of the ambient request: the bill creator's set on the tick, the request
    user's on the drop. None = every project."""
    allowed = scope.accessible_project_ids

    def accessible(project_id: int) -> bool:
        return allowed is None or int(project_id) in allowed

    return accessible


def _allow_all_projects(_project_id: int) -> bool:
    return True


FinalPick = tuple[
    DecisionSource,
    Optional[int],
    Optional[int],
    Optional[Decimal],
    Optional[str],
]


def choose_final(
    *,
    mode: CodingMode,
    history: Optional[BillSuggestion],
    primary: Optional[RungAnswer],
    escalate: Optional[RungAnswer],
    fallback: Optional[RungAnswer],
    similar_vote_scc_id: Optional[int],
    similar_vote_confidence: Optional[Decimal],
    history_for_project: Optional[Callable[[int], Optional[BillSuggestion]]] = None,
    project_accessible: Callable[[int], bool] = _allow_all_projects,
) -> FinalPick:
    """Final (source, scc, project, confidence, model). Never weak history.

    Mode A: strong history -> model pick -> filtered vote -> blank; project is always
    None (the user's chosen project stands).
    Mode B mirrors read_attachment_service._drop_coding_after_text_model (U-676k): the
    model's accessible project P keys history - strong history FOR P gives its code on
    P, else the model's code on P; once P is known vendor-wide history is never used.
    With no (accessible) P: strong vendor-wide ``history`` with its project access-checked,
    else the model's code with no project. Past V1, the filtered vote then blank (owner
    rule 2026-10-28); the vote never sets a project.
    """
    pick = _model_pick(primary, escalate, fallback)
    if mode == "A":
        if history_is_strong(history):
            return "history", history.sub_cost_code_id, None, history.confidence, None
        if pick is not None:
            src, ans = pick
            return src, ans.sub_cost_code_id, None, ans.confidence, ans.model
    else:
        text_project = mode_b_text_project(
            primary, escalate, fallback, project_accessible=project_accessible
        )
        if text_project is not None:
            history_p = history_for_project(text_project) if history_for_project else None
            if history_is_strong(history_p):
                return (
                    "history",
                    history_p.sub_cost_code_id,
                    text_project,
                    history_p.confidence,
                    None,
                )
            if pick is not None:
                src, ans = pick
                return src, ans.sub_cost_code_id, text_project, ans.confidence, ans.model
        elif history_is_strong(history):
            history_project = history.project_id
            if history_project is not None and not project_accessible(history_project):
                history_project = None
            return (
                "history",
                history.sub_cost_code_id,
                history_project,
                history.confidence,
                None,
            )
        elif pick is not None:
            src, ans = pick
            return src, ans.sub_cost_code_id, None, ans.confidence, ans.model
    if similar_vote_scc_id is not None:
        return "similar_vote", similar_vote_scc_id, None, similar_vote_confidence, None
    return "none", None, None, None, None


def drop_escalation_marker(
    *,
    source: DecisionSource,
    primary: Optional[RungAnswer],
) -> Optional[DropEscalation]:
    """Drop only: low_confidence when Haiku answered validly below 0.70;
    claude_unavailable when Haiku gave no valid answer; None when history decided."""
    if source == "history":
        return None
    if not _rung_valid(primary):
        return "claude_unavailable"
    if should_escalate(
        primary.sub_cost_code_id,
        primary.confidence,
        escalate_below=DROP_ESCALATE_BELOW,
    ):
        return "low_confidence"
    return None


def _ladder_for_stage(stage: DecisionStage) -> tuple[Rung, ...]:
    base = DEFAULT_BILL_CODING_LADDER_V2
    if stage == "drop":
        return drop_ladder(base)
    if stage == "tick_escalate":
        return escalate_ladder(base)
    return base


def scoped_neighbours(
    inputs: BillCodingInputs,
    similar_service: Optional[Any],
) -> list[Neighbour]:
    """Neighbours visible to inputs.scope; best-effort (never raises)."""
    scope = inputs.scope
    if similar_service is None or not inputs.query_token_hashes:
        return []
    # SimilarBillsService has no band-less mode: an unknown amount scores every prior 0
    # (as U-701a's shadow, which returns nothing on "no_amount"), so skip the lookup.
    if inputs.amount is None:
        return []
    if scope.company_id is None:
        return []
    if scope.accessible_project_ids is not None and not scope.accessible_project_ids:
        return []
    try:
        return list(
            similar_service.neighbours_for(
                bill_text_hashes=inputs.query_token_hashes,
                amount=abs(inputs.amount),
                vendor_id=inputs.vendor_id,
                exclude_bill_id=inputs.exclude_bill_id,
                as_of=inputs.as_of,
                visible=VisibilityFilter(
                    actor_user_id=scope.actor_user_id,
                    accessible_project_ids=scope.accessible_project_ids,
                ),
            )
        )
    except Exception as error:
        logger.warning(
            "bill_coding_decision.similar_lookup_failed err=%s",
            type(error).__name__,
        )
        return []


def build_v2_payload(
    inputs: BillCodingInputs,
    deps: BillCodingDeps,
    *,
    candidate_sccs: list[dict[str, str]],
    similar_block: list[dict[str, Any]],
) -> dict[str, Any]:
    """V2 payload: extraction content cut at FULL_CONTENT_CHAR_CAP (60k), as measured."""
    cat = deps.catalog
    candidate_projects: Optional[list[dict[str, str]]] = None
    if inputs.chosen_project_id is None:
        candidate_projects = build_candidate_projects_for_bill(
            cat.candidate_projects,
            ambiguous_abbrevs=cat.ambiguous_project_abbrevs,
        )
    return {
        "vendor_name": inputs.vendor_name,
        "bill_text": truncate_bill_text(inputs.bill_text, budget=FULL_CONTENT_CHAR_CAP),
        "history_evidence": history_evidence_from_scc_counts(deps.history_rows),
        "candidate_sccs": candidate_sccs,
        "candidate_projects": candidate_projects,
        "similar_bills": similar_block,
    }


async def _default_run_cascade(
    payload: dict[str, Any],
    ladder: tuple[Rung, ...],
    transport_for: Optional[Callable[[str], Any]],
) -> CascadeResult:
    def validate(result: dict[str, Any]) -> tuple[bool, str]:
        return validate_bill_coding_result(result, payload=payload)

    task = dataclasses.replace(
        BILL_CODING_SUGGESTION_TASK_V2,
        validate=validate,
        ladder=ladder,
        threshold=BILL_CODING_THRESHOLD,
    )
    return await run_cascade(task, payload, transport_for=transport_for)


async def decide_bill_coding(
    inputs: BillCodingInputs,
    deps: BillCodingDeps,
) -> BillCodingDecision:
    mode: CodingMode = "A" if inputs.chosen_project_id is not None else "B"
    cat = deps.catalog

    neighbours = scoped_neighbours(inputs, deps.similar_service)
    merged, _added, similar_block, filtered, _drops = apply_similar_evidence(
        neighbours[:SIMILAR_BILLS_TOP_K],
        list(deps.candidate_sccs),
        as_of_catalog=cat.as_of_catalog,
        scc_by_id=cat.scc_by_id,
        ambiguous_numbers=cat.ambiguous_scc_numbers,
    )
    vote_scc, vote_conf = filtered_vote(
        neighbours,
        as_of_catalog=cat.as_of_catalog,
        scc_by_id=cat.scc_by_id,
        ambiguous_numbers=cat.ambiguous_scc_numbers,
    )

    def _decision(
        pick: FinalPick,
        *,
        primary: Optional[RungAnswer],
        escalate: Optional[RungAnswer],
        fallback: Optional[RungAnswer],
        escalation: Optional[DropEscalation],
        cascade: Optional[CascadeResult],
    ) -> BillCodingDecision:
        src, scc, proj, conf, model = pick
        return BillCodingDecision(
            source=src,
            mode=mode,
            sub_cost_code_id=scc,
            project_id=proj,
            confidence=conf,
            model=model,
            primary=primary,
            escalate=escalate,
            fallback=fallback,
            similar_vote_scc_id=vote_scc,
            similar_vote_confidence=vote_conf,
            similar_neighbour_count=len(filtered),
            escalation=escalation,
            stopped_reason=cascade.stopped_reason if cascade else None,
            cascades=(cascade,) if cascade else (),
            history=deps.history_suggestion,
        )

    # Mode A history-first short-circuit: no model call.
    if (
        mode == "A"
        and inputs.stage != "tick_escalate"
        and history_is_strong(deps.history_suggestion)
    ):
        pick = choose_final(
            mode=mode,
            history=deps.history_suggestion,
            primary=None,
            escalate=None,
            fallback=None,
            similar_vote_scc_id=vote_scc,
            similar_vote_confidence=vote_conf,
        )
        return _decision(
            pick, primary=None, escalate=None, fallback=None, escalation=None, cascade=None
        )

    payload = build_v2_payload(
        inputs, deps, candidate_sccs=merged, similar_block=similar_block
    )
    runner = deps.run_cascade or _default_run_cascade
    cascade = await runner(payload, _ladder_for_stage(inputs.stage), deps.transport_for)

    escalate_only = inputs.stage == "tick_escalate"
    primary, escalate, fallback = map_cascade_role_answers(
        cascade,
        scc_number_to_id=cat.scc_number_to_id,
        proj_abbrev_to_id=cat.proj_abbrev_to_id,
        escalate_only=escalate_only,
    )
    if escalate_only:
        # The intake's Haiku answer is the primary. Mode A history already lost at the
        # drop; in Mode B Sonnet's project P' keys history exactly as Haiku's P does.
        primary = inputs.prior_primary
    project_accessible = scope_project_accessible(inputs.scope)

    # Mode B: read history for the model's project off the event loop (the drop serves
    # live requests), then hand choose_final the prefetched answer.
    history_for_project = None
    text_project = mode_b_text_project(
        primary, escalate, fallback, project_accessible=project_accessible
    )
    if mode == "B" and text_project is not None and deps.history_for_project:
        history_p = await asyncio.to_thread(deps.history_for_project, text_project)

        def history_for_project(_project_id: int) -> Optional[BillSuggestion]:
            return history_p

    pick = choose_final(
        mode=mode,
        history=None if escalate_only and mode == "A" else deps.history_suggestion,
        primary=primary,
        escalate=escalate,
        fallback=fallback,
        similar_vote_scc_id=vote_scc,
        similar_vote_confidence=vote_conf,
        history_for_project=history_for_project,
        project_accessible=project_accessible,
    )

    escalation: Optional[DropEscalation] = None
    if inputs.stage == "drop":
        escalation = drop_escalation_marker(source=pick[0], primary=primary)

    return _decision(
        pick,
        primary=primary,
        escalate=escalate,
        fallback=fallback,
        escalation=escalation,
        cascade=cascade,
    )
