"""Bill coding ladder V2 decision core (U-701b3): history, cascade roles, filtered vote.

One decision for the PDF drop (stage "drop") and the background tick ("tick_full" /
"tick_escalate"). Order (owner decisions 2026-10-28): strong history wins, else
Haiku (accepted at >= 0.70) -> Sonnet -> Foundry only when Claude gave no valid answer
-> the FILTERED similar-bills vote; if all of those give nothing the code stays blank.

Mode A (user chose the project): strong history for that project short-circuits before
any model call. Mode B (no project, U-676k "text project first"): the model runs first;
its project P then keys history - strong history FOR P overrides the code; with no
model project, strong vendor-wide history applies, as the drop path does.

M0 (U-745): on every decision the U-698 M0 model (no LLM) scores the same filtered
neighbour list the vote uses plus the vendor's strictly-earlier history; its pick and
calibrated probability are stored beside the vote (shadow). It abstains whenever the
filtered vote has no neighbours and never sets a project. With
``BillCodingDeps.m0_rung_enabled`` (setting ``bill_coding_m0_rung_enabled``) it replaces
the vote at the last rung (source "m0"); off = today's ladder. Any M0 failure -> no pick.

Similar-bills scope: decide_bill_coding does the neighbour lookup itself from
``inputs.scope`` (never from neighbours a caller hands in), so a caller cannot pass
neighbours gathered under a wider scope. The lookup is best-effort: no service, no
token hashes, an empty accessible set or any error -> no neighbours.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Literal, Optional, Sequence

from entities.coding_suggestion.business.bill_suggestion_service import (
    BillSuggestion,
    history_blocks_text_tier,
)
from entities.coding_suggestion.business.decision_model.features import QueryBill
from entities.coding_suggestion.business.decision_model.serve import (
    M0_READ_BOUND_SECONDS,
    M0Model,
    M0Priors,
    catalog_choosable,
    get_m0_model,
    m0_pick,
    neighbour_evidence,
)
from entities.coding_suggestion.business.similar_bills import (
    Neighbour,
    VisibilityFilter,
    apply_similar_evidence,
    choosable_scc_numbers,
    vote_over_filtered,
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
    is_skipped_error,
    run_cascade,
)
from intelligence.cascade.ladder_config import (
    DEFAULT_BILL_CODING_LADDER_V2,
    drop_ladder,
    escalate_ladder,
)

logger = logging.getLogger(__name__)

DecisionSource = Literal[
    "history", "primary", "escalate", "fallback", "similar_vote", "m0", "none", "reused_intake"
]
# The no-LLM last rung (the filtered vote, or M0 in its place): presented alike.
NO_LLM_LAST_RUNG_SOURCES: frozenset[str] = frozenset({"similar_vote", "m0"})
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
    # U-745 M0 shadow: always computed beside the vote (None = M0 abstained / failed).
    m0_scc_id: Optional[int] = None
    m0_confidence: Optional[Decimal] = None


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
    # U-745 M0: its DB inputs - a decision_model.serve.M0Priors, or an awaitable (task)
    # still running beside the cascade, used only at choose_final (drop: only if already
    # done; tick: waited up to M0_WAIT_TICK_SECONDS); None = no M0 pick - an optional model override (None = the
    # shipped model) and the rung switch.
    m0_priors: Optional[Any] = None
    m0_model: Optional[Any] = None
    m0_rung_enabled: bool = False


_RUNG_FIELDS = {
    "Model": "model",
    "SubCostCodeId": "sub_cost_code_id",
    "ProjectId": "project_id",
    "Confidence": "confidence",
    "Error": "error",
}


def rung_columns(
    prefix: str,
    answer: Optional[RungAnswer],
    fields: Sequence[str] = tuple(_RUNG_FIELDS),
) -> dict[str, Any]:
    """``{prefix+Field: value}`` for one rung answer (None values when absent) - the
    shared shape of the intake and text-suggestion per-rung columns."""
    return {
        f"{prefix}{field}": getattr(answer, _RUNG_FIELDS[field]) if answer else None
        for field in fields
    }


def history_is_strong(suggestion: Optional[BillSuggestion]) -> bool:
    return suggestion is not None and history_blocks_text_tier(suggestion)


def rung_error_code(error: Optional[str]) -> Optional[str]:
    """Short, provider-text-free code for the *Error columns."""
    if not error:
        return None
    if is_skipped_error(error):
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


def _answer_usable(answer: Optional[RungAnswer], *, fallback: bool = False) -> bool:
    """An answer that carries a resolved code OR a project (V1 retain_accessible_project);
    a Foundry answer only when accepted (>= threshold)."""
    if answer is None or answer.error:
        return False
    if answer.sub_cost_code_id is None and answer.project_id is None:
        return False
    if fallback:
        return answer.confidence is not None and float(answer.confidence) >= BILL_CODING_THRESHOLD
    return True


def mode_b_answer(
    primary: Optional[RungAnswer],
    escalate: Optional[RungAnswer],
    fallback: Optional[RungAnswer],
) -> Optional[tuple[DecisionSource, RungAnswer]]:
    """V1's single-answer rule: ONE rung answer (escalate > primary > accepted fallback,
    the first with a code or a project) supplies BOTH the code and the project - never
    one rung's code on another rung's project."""
    for source, answer, is_fallback in (
        ("escalate", escalate, False),
        ("primary", primary, False),
        ("fallback", fallback, True),
    ):
        if _answer_usable(answer, fallback=is_fallback):
            return source, answer
    return None


def mode_b_text_project(
    primary: Optional[RungAnswer],
    escalate: Optional[RungAnswer],
    fallback: Optional[RungAnswer],
    *,
    project_accessible: Callable[[int], bool],
) -> Optional[int]:
    """The single answer's project P after the access check; an inaccessible P is treated
    as no project (as V1)."""
    picked = mode_b_answer(primary, escalate, fallback)
    if picked is None:
        return None
    project_id = picked[1].project_id
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
    m0_scc_id: Optional[int] = None,
    m0_confidence: Optional[Decimal] = None,
    m0_rung_enabled: bool = False,
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
    U-745: with ``m0_rung_enabled`` and an M0 pick, M0 takes the vote's place at that
    last rung (source "m0", no project); M0 silent -> the vote as before.
    """
    pick = _model_pick(primary, escalate, fallback)
    if mode == "A":
        if history_is_strong(history):
            return "history", history.sub_cost_code_id, None, history.confidence, None
        if pick is not None:
            src, ans = pick
            return src, ans.sub_cost_code_id, None, ans.confidence, ans.model
    else:
        answer_pick = mode_b_answer(primary, escalate, fallback)
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
            # P came from answer_pick, so its code (if any) is the SAME answer's.
            src, ans = answer_pick
            if ans.sub_cost_code_id is not None:
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
        elif answer_pick is not None and answer_pick[1].sub_cost_code_id is not None:
            src, ans = answer_pick
            return src, ans.sub_cost_code_id, None, ans.confidence, ans.model
    if m0_rung_enabled and m0_scc_id is not None:
        return "m0", m0_scc_id, None, m0_confidence, None
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


def neighbour_lookup_possible(
    *,
    query_token_hashes: frozenset[int],
    amount: Optional[Decimal],
    company_id: Optional[int],
    accessible_project_ids: Optional[frozenset[int]],
) -> bool:
    """Whether a similar-bills lookup can return anything. No token hashes, no company or
    an empty accessible set -> no neighbours. SimilarBillsService has no band-less mode:
    an unknown amount scores every prior 0 (as U-701a's shadow, which returns nothing on
    "no_amount"), so no amount -> no lookup. No neighbours also means M0 abstains."""
    if not query_token_hashes or amount is None or company_id is None:
        return False
    return accessible_project_ids is None or bool(accessible_project_ids)


def scoped_neighbours(
    inputs: BillCodingInputs,
    similar_service: Optional[Any],
) -> list[Neighbour]:
    """Neighbours visible to inputs.scope; best-effort (never raises)."""
    scope = inputs.scope
    if similar_service is None or not neighbour_lookup_possible(
        query_token_hashes=inputs.query_token_hashes,
        amount=inputs.amount,
        company_id=scope.company_id,
        accessible_project_ids=scope.accessible_project_ids,
    ):
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


# U-745: how long the TICK waits, after its cascade, for M0's reads to land (the read
# bound). The drop never waits: it serves a live request under read_attachment_service's
# coding deadline, so it uses M0 only when the reads already finished; a late read still
# finishes and warms the cache.
M0_WAIT_TICK_SECONDS = M0_READ_BOUND_SECONDS


async def _await_m0_priors(source: Any, *, stage: DecisionStage) -> Optional[Any]:
    """M0Priors from ``source`` (a value, or a task still running beside the cascade);
    None when absent. Never raises; never cancels the read.

    Drop: no wait - the task's result only if it is already done (a read that has not
    landed is simply absent, logged at DEBUG). Tick: a shielded wait of at most
    M0_WAIT_TICK_SECONDS. A read that failed or was cancelled -> None with a WARNING."""
    if source is None or not inspect.isawaitable(source):
        return source
    if stage == "drop":
        if not isinstance(source, asyncio.Future) or not source.done():
            logger.debug("bill_coding_decision.m0_not_ready stage=drop")
            return None
        try:
            # A DONE future: result() re-raises its stored exception (CancelledError
            # included) - never the current task's cancellation.
            return source.result()
        except BaseException as error:
            logger.warning("bill_coding_decision.m0_not_ready err=%s", type(error).__name__)
            return None
    try:
        return await asyncio.wait_for(asyncio.shield(source), timeout=M0_WAIT_TICK_SECONDS)
    except Exception as error:
        logger.warning("bill_coding_decision.m0_not_ready err=%s", type(error).__name__)
        return None


def m0_shadow_pick(
    inputs: BillCodingInputs,
    *,
    catalog: BillCodingCatalog,
    priors: Optional[M0Priors],
    model: Optional[M0Model],
    vote_neighbours: Sequence[Neighbour],
    choosable_nums: frozenset[str],
) -> tuple[Optional[int], Optional[Decimal]]:
    """M0's (code, calibrated confidence) over the filtered vote's neighbours, or
    (None, None). Abstains when the vote has no neighbours (owner rule: never prefill
    from weak history alone). ``model`` None = the shipped model. Never raises: a
    missing/mismatched model, absent priors, a leakage guard or any error -> no pick (a
    WARNING with the exception type only)."""
    if priors is None or not vote_neighbours:
        return None, None
    try:
        model = model if model is not None else get_m0_model()
        if model is None:
            return None, None
        hashes = inputs.query_token_hashes
        query = QueryBill(
            bill_id=int(inputs.exclude_bill_id) if inputs.exclude_bill_id is not None else -1,
            vendor_id=int(inputs.vendor_id),
            created=inputs.as_of,
            amount=float(abs(inputs.amount)) if inputs.amount is not None else 0.0,
            project_id=inputs.chosen_project_id,
            has_text=bool(hashes),
            pdf_token_count=len(hashes),
        )
        pick = m0_pick(
            model,
            priors,
            query,
            mode="A" if inputs.chosen_project_id is not None else "B",
            neighbours=neighbour_evidence(vote_neighbours),
            choosable=catalog_choosable(
                choosable_nums=choosable_nums,
                scc_by_id=catalog.scc_by_id,
                ambiguous_numbers=catalog.ambiguous_scc_numbers,
            ),
        )
    except Exception as error:
        logger.warning("bill_coding_decision.m0_failed err=%s", type(error).__name__)
        return None, None
    if pick is None:
        return None, None
    return pick.scc_id, pick.confidence


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
    *,
    fallback_on_daily_cap: bool = False,
) -> CascadeResult:
    """fallback_on_daily_cap (U-701c): the Foundry fallback rungs still run when
    Claude's daily cap stopped a rung."""
    def validate(result: dict[str, Any]) -> tuple[bool, str]:
        return validate_bill_coding_result(result, payload=payload)

    task = dataclasses.replace(
        BILL_CODING_SUGGESTION_TASK_V2,
        validate=validate,
        ladder=ladder,
        threshold=BILL_CODING_THRESHOLD,
        fallback_on_daily_cap=fallback_on_daily_cap,
    )
    return await run_cascade(task, payload, transport_for=transport_for)


async def decide_bill_coding(
    inputs: BillCodingInputs,
    deps: BillCodingDeps,
) -> BillCodingDecision:
    mode: CodingMode = "A" if inputs.chosen_project_id is not None else "B"
    cat = deps.catalog

    neighbours = scoped_neighbours(inputs, deps.similar_service)
    choosable_nums = choosable_scc_numbers(cat.as_of_catalog)
    merged, _added, similar_block, filtered, _drops = apply_similar_evidence(
        neighbours[:SIMILAR_BILLS_TOP_K],
        list(deps.candidate_sccs),
        as_of_catalog=cat.as_of_catalog,
        scc_by_id=cat.scc_by_id,
        ambiguous_numbers=cat.ambiguous_scc_numbers,
        choosable_nums=choosable_nums,
    )
    # Filtered ONCE (all neighbours, not the model's top SIMILAR_BILLS_TOP_K): the vote
    # and M0 read the same list.
    _m, _a, _b, vote_neighbours, _d = apply_similar_evidence(
        neighbours,
        [],
        as_of_catalog=cat.as_of_catalog,
        scc_by_id=cat.scc_by_id,
        ambiguous_numbers=cat.ambiguous_scc_numbers,
        choosable_nums=choosable_nums,
    )
    vote_scc, vote_conf = vote_over_filtered(vote_neighbours)
    # M0 is needed only at choose_final: its reads (deps.m0_priors may be a still-running
    # task) are consulted AFTER the cascade / history short-circuit (drop: never waits).
    async def _resolve_m0() -> tuple[Optional[int], Optional[Decimal]]:
        priors = await _await_m0_priors(deps.m0_priors, stage=inputs.stage)
        return m0_shadow_pick(
            inputs,
            catalog=cat,
            priors=priors,
            model=deps.m0_model,
            vote_neighbours=vote_neighbours,
            choosable_nums=choosable_nums,
        )

    def _decision(
        pick: FinalPick,
        *,
        m0_scc: Optional[int],
        m0_conf: Optional[Decimal],
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
            m0_scc_id=m0_scc,
            m0_confidence=m0_conf,
        )

    # Mode A history-first short-circuit: no model call.
    if (
        mode == "A"
        and inputs.stage != "tick_escalate"
        and history_is_strong(deps.history_suggestion)
    ):
        m0_scc, m0_conf = await _resolve_m0()
        pick = choose_final(
            mode=mode,
            history=deps.history_suggestion,
            primary=None,
            escalate=None,
            fallback=None,
            similar_vote_scc_id=vote_scc,
            similar_vote_confidence=vote_conf,
            m0_scc_id=m0_scc,
            m0_confidence=m0_conf,
            m0_rung_enabled=deps.m0_rung_enabled,
        )
        return _decision(
            pick,
            m0_scc=m0_scc,
            m0_conf=m0_conf,
            primary=None,
            escalate=None,
            fallback=None,
            escalation=None,
            cascade=None,
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

    m0_scc, m0_conf = await _resolve_m0()
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
        m0_scc_id=m0_scc,
        m0_confidence=m0_conf,
        m0_rung_enabled=deps.m0_rung_enabled,
    )

    escalation: Optional[DropEscalation] = None
    if inputs.stage == "drop":
        escalation = drop_escalation_marker(source=pick[0], primary=primary)

    return _decision(
        pick,
        m0_scc=m0_scc,
        m0_conf=m0_conf,
        primary=primary,
        escalate=escalate,
        fallback=fallback,
        escalation=escalation,
        cascade=cascade,
    )
