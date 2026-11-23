"""Contract Labor coding shadow service (U-797a): enqueue hook + scheduler tick.

The entity contract (Owns / Sprocs) is in entities/contract_labor/business/service.py; the design and the
full gate / attempt rules are in docs/design/u797a-cl-coding-shadow.md. In short: shadow only (no line,
bill, QBO or email change); enqueue is flag-gated and never raises; history answers complete before any
gate; blocked model rows are deferred without an attempt; failures release with an attempt and stop the
tick, the third strike completes 'failed' with a class name; note text is never stored or logged.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Optional

import config
from entities.contract_labor.business.coding_shadow import (
    LABELLED_CL_STATUSES,
    LLM_GEN_PARAMS,
    LLM_MAX_TOKENS,
    LLM_SYSTEM,
    DayInputs,
    ModelAnswer,
    ShadowDecision,
    arm_a_resolved_codes,
    build_corpus_idf,
    build_prompt,
    decide,
    history_stats,
    is_history_strong,
    parse_model_response,
    sonnet_model_id,
    top_codes,
)
from intelligence.cascade import claude_budget

logger = logging.getLogger(__name__)

ModelCall = Callable[[str, str], Awaitable[Optional[str]]]

# A model call is made only while today's Claude spend is below this share of the live cap.
SPEND_HEADROOM_FRACTION = Decimal("0.70")

# A row that fails this many attempts (model unavailable, or an exception while processing it) is
# completed as 'failed' instead of being released again.
MAX_ATTEMPTS = 3

# U-797b1: the CL crew email waits this long, from the crew's latest submit, for its suggestion rows.
# Past it the email goes with whatever is there (a pending row shows 'suggestion pending when sent').
CL_SUGGESTION_WAIT_MINUTES = 30


async def _live_model_call(system: str, user: str) -> Optional[str]:
    """One Sonnet completion through the live transport (U-702 gate + metering). None = unavailable."""
    from intelligence.messages.types import Message, Text
    from intelligence.transport.base import TextDelta, TransportError
    from intelligence.transport.registry import get_transport

    transport = get_transport("anthropic")
    parts: list[str] = []
    async for ev in transport.stream(
        [Message(role="user", content=[Text(text=user)])],
        model=sonnet_model_id(),
        system=system,
        max_tokens=LLM_MAX_TOKENS,
        extra_body=dict(LLM_GEN_PARAMS),
    ):
        if isinstance(ev, TransportError):
            return None
        if isinstance(ev, TextDelta):
            parts.append(ev.text)
    return "".join(parts)


def spend_has_headroom(spend: Decimal, cap: Decimal) -> bool:
    """True while spend is below SPEND_HEADROOM_FRACTION of the cap. No cap configured = no limit."""
    if cap <= 0:
        return True
    return spend < cap * SPEND_HEADROOM_FRACTION


def _read_spend_headroom() -> bool:
    settings = config.Settings()
    cap = claude_budget.daily_cap_usd(settings)
    spend = claude_budget.get_today_anthropic_spend()
    return spend_has_headroom(spend, cap)


async def _spend_headroom_async() -> bool:
    return await asyncio.to_thread(_read_spend_headroom)


# --- IDF corpus cache: one build per UTC day per worker -------------------

_IDF_LOCK = threading.Lock()
_IDF_CACHE: dict[str, Any] = {"day": None, "idf": {}}


def reset_idf_cache() -> None:
    with _IDF_LOCK:
        _IDF_CACHE["day"] = None
        _IDF_CACHE["idf"] = {}


def _corpus_idf(repo: Any, today: date) -> dict[str, float]:
    with _IDF_LOCK:
        if _IDF_CACHE["day"] == today:
            return _IDF_CACHE["idf"]
        idf = build_corpus_idf(repo.read_idf_corpus())
        _IDF_CACHE["day"] = today
        _IDF_CACHE["idf"] = idf
        return idf


@dataclass(frozen=True)
class SuggestionDisplay:
    """What the CL review email shows for one crew member's suggestion (U-797b1). The snapshot stores these values.

    state: clean | split | no_suggestion | holding | failed | skipped | pending | no_row.
    code_id / code_label: the code shown in the suggested cell ('NN.NN Name'), None when no code is shown.
    split_shown: None when no completed row was read; otherwise whether the 'possible split' note was shown.
    """

    state: str
    code_id: Optional[int]
    code_label: Optional[str]
    suggestion_id: Optional[int]
    source: Optional[str]
    split_shown: Optional[bool]
    candidate_labels: tuple[str, ...] = ()


def suggestion_label(number: Any, name: Any) -> str:
    return f"{number or ''} {name or ''}".strip()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def crew_members(rows: Any) -> list:
    """The members the email and the hold count (H2, one rule): submitted, with a line on the project."""
    return [r for r in rows if r.contract_labor_status == "submitted" and r.has_submitted_project_line]


def crew_day_settled(rows: Any) -> bool:
    """True when no counted member is still pending. The hold, the sweep and the re-fire all use this."""
    return not any(r.status == "pending" for r in crew_members(rows))


def suggestion_hold(rows: Any, *, now: datetime, wait_minutes: int = CL_SUGGESTION_WAIT_MINUTES) -> bool:
    """True while the crew email should wait: a counted member is pending AND the latest submit among the counted
    members is less than wait_minutes old. No counted pending member, or no submit stamp, does not hold."""
    members = crew_members(rows)
    if not any(r.status == "pending" for r in members):
        return False
    stamps = [_as_utc(r.contract_labor_modified_datetime) for r in members if r.contract_labor_modified_datetime is not None]
    if not stamps:
        return False
    return _as_utc(now) - max(stamps) < timedelta(minutes=wait_minutes)


def suggestion_display(row: Any, labels: dict) -> SuggestionDisplay:
    """One crew member's display state from their suggestion row (None when the day has no row).

    labels maps a SubCostCode id to (Number, Name) for the split candidates."""
    if row is None:
        return SuggestionDisplay("no_row", None, None, None, None, None)
    if row.status in ("pending", "failed", "skipped"):
        state = row.status
        return SuggestionDisplay(state, None, None, None, None, None)
    if row.status != "done":
        return SuggestionDisplay("pending", None, None, None, None, None)
    if row.is_holding_code and row.sub_cost_code_id is not None:
        return SuggestionDisplay("holding", None, None, None, None, False)

    split = bool(row.may_split)
    candidates: tuple[str, ...] = ()
    if split:
        candidates = tuple(
            suggestion_label(*labels[code_id]) for code_id in row.model_split_code_ids if code_id in labels
        )
    code_ok = row.sub_cost_code_id is not None and row.decision_source in ("history", "model")
    label = suggestion_label(row.sub_cost_code_number, row.sub_cost_code_name) if code_ok else ""
    if not label:
        code_ok = False  # F7: a code with no number and no name is not a suggestion, and its id is not stored
    if code_ok:
        code_id = row.sub_cost_code_id
        suggestion_id, source = row.id, row.decision_source
        state = "split" if split else "clean"
    else:
        code_id, label, suggestion_id, source = None, None, None, None
        state = "split" if split else "no_suggestion"
    return SuggestionDisplay(state, code_id, label, suggestion_id, source, split, candidates)


class ContractLaborCodingShadowService:
    def __init__(self, *, settings: Any = None, repo: Any = None) -> None:
        self._settings = settings
        self._repo = repo

    @property
    def repo(self):
        if self._repo is None:
            from entities.contract_labor.persistence.coding_suggestion_repo import (
                ContractLaborCodingSuggestionRepository,
            )

            self._repo = ContractLaborCodingSuggestionRepository()
        return self._repo

    def enqueue_for_contract_labor(self, contract_labor_id: int) -> int:
        """Insert pending rows for the CL's projects. No-op when the flag is off. Never raises."""
        try:
            settings = self._settings if self._settings is not None else config.Settings()
            if not settings.cl_coding_shadow_enabled:
                return 0
            return int(self.repo.enqueue_for_contract_labor(contract_labor_id))
        except Exception as error:
            logger.warning(
                "cl_coding_shadow.enqueue_failed contract_labor_id=%s error=%s",
                contract_labor_id,
                type(error).__name__,
            )
            return 0


def _complete_fields(decision: ShadowDecision, model: Optional[str]) -> dict[str, Any]:
    split_codes = ",".join(str(s) for s in decision.model_split_codes) or None
    return {
        "status": "done",
        "decision_source": decision.decision_source,
        "sub_cost_code_id": decision.sub_cost_code_id,
        "confidence": decision.confidence,
        "may_split": decision.may_split,
        "model_proposal": decision.model_proposal,
        "model_split_codes": split_codes,
        "history_top_sub_cost_code_id": decision.history_top_sub_cost_code_id,
        "history_share": decision.history_share,
        "history_prior_count": decision.history_prior_count,
        "arm_a_code_count": decision.arm_a_code_count,
        "model": model,
        "failure_reason": decision.failure_reason,
    }


def _record_attempt_failure(repo: Any, claim: Any, *, model: Optional[str], reason: str) -> bool:
    """One failed attempt on a claimed row (model unavailable, or an exception while processing it).

    Attempts 1 and 2 release the row to pending with AttemptCount +1 (a fresh lease). The third strike
    completes it as 'failed' with the reason (a class name, or 'model_unavailable'). Returns True only when
    the row was completed (settled). Never raises.
    """
    try:
        if int(claim.attempt_count) + 1 >= MAX_ATTEMPTS:
            repo.complete(claim.id, status="failed", model=model, failure_reason=reason)
            return True
        repo.release(claim.id, count_attempt=True)
        return False
    except Exception as error:
        logger.error(
            "cl_coding_shadow.attempt_record_failed suggestion_id=%s error=%s",
            claim.id,
            type(error).__name__,
        )
        return False


def _fire_crew_drafts(contract_labor_id: int) -> None:
    """Re-run the CL crew drafts for this contract labor's work date. Claim-deduped, so a double fire is safe."""
    from entities.contract_labor.business.service import ContractLaborService
    from entities.review.business.cl_notification_service import ContractLaborReviewNotificationService

    cl = ContractLaborService().repo.read_by_id(contract_labor_id)
    if cl is None:
        return
    ContractLaborReviewNotificationService().enqueue_drafts(contract_labor=cl)


def _safe_refire(refire: Callable[[int], None], contract_labor_id: int) -> bool:
    try:
        refire(contract_labor_id)
        return True
    except Exception as error:
        logger.error(
            "cl_coding_shadow.refire_failed contract_labor_id=%s error=%s",
            contract_labor_id,
            type(error).__name__,
        )
        return False


# U-797b1 round 2: the sweep and the completion re-fire act only on MARKED (project, date) holds
# (dbo.ContractLaborReviewHold). A fire that does not delete its marker is one attempt; the fifth gives up.
MAX_HOLD_FIRES = 5
HOLD_WINDOW_DAYS = 7


def _crew_day_settled(repo: Any, claim: Any) -> bool:
    """True when no counted member of this crew day is pending. Never raises."""
    try:
        rows, _labels = repo.read_suggestions_for_project_day(claim.project_id, claim.work_date)
    except Exception as error:
        logger.error("cl_coding_shadow.settled_read_failed suggestion_id=%s error=%s", claim.id, type(error).__name__)
        return False
    return crew_day_settled(rows)


def _read_hold(repo: Any, project_id: int, work_date: Any) -> Any:
    try:
        return repo.read_review_hold(project_id, work_date)
    except Exception as error:
        logger.error("cl_review_hold.read_failed project_id=%s error=%s", project_id, type(error).__name__)
        return None


def _date_waits_on_a_submit(repo: Any, work_date: Any) -> bool:
    """True while any ContractLabor on the date is still pending_review (the email's date gate would refuse), or
    when that count cannot be read. A fire then could not send, so it is not attempted and not counted."""
    try:
        return repo.count_pending_review(work_date) > 0
    except Exception as error:
        logger.error("cl_review_hold.pending_read_failed error=%s", type(error).__name__)
        return True


def _record_hold_fire(repo: Any, hold: Any) -> None:
    """Count one fire of a marker. A fire that deleted the marker counts nothing. The fifth non-deleting fire
    gives up: the marker stops being fired and an error is logged with ids only. Never raises."""
    try:
        fire = repo.record_review_hold_fire(hold.id)
    except Exception as error:
        logger.error("cl_review_hold.record_failed hold_id=%s error=%s", hold.id, type(error).__name__)
        return
    if fire is not None and fire.gave_up_datetime is not None and fire.fire_attempts == MAX_HOLD_FIRES:
        logger.error(
            "cl_review_hold.gave_up hold_id=%s project_id=%s attempts=%s: the crew email was not sent by the "
            "sweep; check the send (no ReconciliationIssue recorder exists for this area)",
            hold.id, hold.project_id, fire.fire_attempts,
        )


def _after_terminal(repo: Any, claim: Any, fire: Callable[[int], None], summary: dict[str, Any],
                    fired_dates: set) -> None:
    """Completion re-fire (U-797b1). A settled crew day whose (project, date) is MARKED is refired once and the
    attempt recorded. An unmarked day is never fired, and a gave-up marker is not fired again."""
    if not _crew_day_settled(repo, claim):
        return
    hold = _read_hold(repo, claim.project_id, claim.work_date)
    if hold is None or hold.gave_up_datetime is not None:
        return
    if _date_waits_on_a_submit(repo, claim.work_date):
        return
    fired = _safe_refire(fire, claim.contract_labor_id)
    fired_dates.add(claim.work_date)  # one fire per work date per tick: the sweep skips this date
    _record_hold_fire(repo, hold)
    if fired:
        summary["refired"] += 1


async def _process_claim(
    claim: Any,
    *,
    repo: Any,
    line_repo: Any,
    cap: int,
    model_call: ModelCall,
    attempt: dict[str, Optional[str]],
    stopped: Callable[[], Awaitable[bool]],
    headroom: Callable[[], Awaitable[bool]],
    today: date,
) -> str:
    """Run the ladder for one claim.

    Returns 'done' (completed), 'deferred' (a model rung was needed but blocked: released with a
    lease) or 'unavailable' (the model did not answer: released, and the tick should stop).
    """
    inputs = repo.read_inputs(claim.contract_labor_id, claim.project_id)
    if inputs is None:
        repo.complete(
            claim.id,
            status="failed",
            failure_reason="missing_contract_labor",
        )
        return "done"

    if inputs.own_status in LABELLED_CL_STATUSES:
        # The crew day is already coded: a prediction now would leak the final answer.
        repo.complete(claim.id, status="skipped", failure_reason="own_cl_coded")
        return "done"

    if claim.vendor_id is not None:
        project_counts, vendor_counts = line_repo.read_sub_cost_code_counts(
            vendor_id=claim.vendor_id,
            project_id=claim.project_id,
            as_of_date=claim.work_date.isoformat(),
            exclude_contract_labor_id=claim.contract_labor_id,
        )
    else:
        project_counts, vendor_counts = {}, {}

    history = history_stats(project_counts)
    idf = _corpus_idf(repo, today)
    arm_a = arm_a_resolved_codes(inputs.day_note_text, inputs.precedent_days, claim.work_date, idf=idf)

    # History first: a strong answer needs no model call, so no gate below may block it.
    if is_history_strong(history):
        decision = decide(history=history, model=None, arm_a_codes=arm_a, total_hours=inputs.total_hours)
        repo.complete(claim.id, **_complete_fields(decision, model=None))
        return "done"

    catalog = {c.id: c for c in inputs.catalog}
    user_text, allowed = build_prompt(
        DayInputs(
            line_date=claim.work_date,
            project_id=claim.project_id,
            total_hours=inputs.total_hours,
            note_text=inputs.day_note_text,
        ),
        inputs.precedent_days,
        catalog,
        top_codes(vendor_counts),
    )
    if not allowed:
        # No candidate codes: the model rung is not needed, so no call and nothing to defer.
        decision = decide(
            history=history,
            model=ModelAnswer(proposal=None, status="no_catalog"),
            arm_a_codes=arm_a,
            total_hours=inputs.total_hours,
        )
        repo.complete(claim.id, **_complete_fields(decision, model=None))
        return "done"

    # The model rung is needed. Each gate defers the row rather than failing it.
    if await stopped() or not await headroom() or repo.count_calls_today() >= cap:
        repo.release(claim.id)
        return "deferred"

    attempt["model"] = sonnet_model_id()  # counted against the cap even if the reply is bad
    text = await model_call(LLM_SYSTEM, user_text)
    if text is None:
        # The caller records the attempt failure (release with an attempt, or the third strike).
        return "unavailable"
    answer = parse_model_response(text, allowed, inputs.total_hours)
    decision = decide(history=history, model=answer, arm_a_codes=arm_a, total_hours=inputs.total_hours)
    repo.complete(claim.id, **_complete_fields(decision, model=attempt.get("model")))
    return "done"


async def _claim_loop(
    *,
    limit: int,
    cfg: Any,
    repo: Any,
    line_repo: Any,
    caller: ModelCall,
    stopped: Callable[[], Awaitable[bool]],
    headroom: Callable[[], Awaitable[bool]],
    fire: Callable[[int], None],
    summary: dict[str, Any],
    fired_dates: set,
) -> None:
    """The shadow claim loop. Every error is contained, so the caller's sweep always runs (U-797b1 F2)."""
    cap = int(cfg.cl_coding_shadow_daily_call_cap)
    today = datetime.now(timezone.utc).date()
    seen: set[int] = set()

    for _ in range(max(1, int(limit))):
        try:
            claim = repo.claim_next()
        except Exception as error:
            logger.error("cl_coding_shadow.claim_failed error=%s", type(error).__name__)
            break
        if claim is None:
            break
        if claim.id in seen:
            break  # never process the same row twice in one tick
        seen.add(claim.id)
        summary["claimed"] += 1
        attempt: dict[str, Optional[str]] = {"model": None}
        try:
            outcome = await _process_claim_call(claim, repo, line_repo, cap, caller, attempt, stopped, headroom, today)
        except Exception as error:
            # Infrastructure or row fault: an attempt failure, then stop the tick.
            summary["failed"] += 1
            logger.error("cl_coding_shadow.row_failed suggestion_id=%s error=%s", claim.id, type(error).__name__)
            if _record_attempt_failure(repo, claim, model=attempt.get("model"), reason=type(error).__name__):
                _after_terminal(repo, claim, fire, summary, fired_dates)
            break

        if outcome == "unavailable":
            summary["unavailable"] += 1
            if _record_attempt_failure(repo, claim, model=attempt.get("model"), reason="model_unavailable"):
                _after_terminal(repo, claim, fire, summary, fired_dates)
            break  # the model is down: stop the tick
        if outcome == "deferred":
            summary["deferred"] += 1
            continue  # a blocked row does not block the rows behind it
        summary["completed"] += 1
        _after_terminal(repo, claim, fire, summary, fired_dates)


async def _process_claim_call(claim, repo, line_repo, cap, caller, attempt, stopped, headroom, today):
    """Runs the ladder for one claim. Kept as its own function so the tests can stand in for the ladder."""
    return await _process_claim(
        claim,
        repo=repo,
        line_repo=line_repo,
        cap=cap,
        model_call=caller,
        attempt=attempt,
        stopped=stopped,
        headroom=headroom,
        today=today,
    )


def _sweep_review_holds(repo: Any, fire: Callable[[int], None], summary: dict[str, Any], fired_dates: set) -> None:
    """U-797b1 sweep, every tick whatever the flags. Prunes markers whose day already has a claim, then fires each
    DUE marker's date once (due: held longer than the wait, or the crew has settled). Every fire of a marker is
    counted, a failed send included. Never raises."""
    try:
        repo.prune_review_holds()
        holds = repo.read_review_holds_due(days=HOLD_WINDOW_DAYS)
    except Exception as error:
        logger.error("cl_review_hold.sweep_read_failed error=%s", type(error).__name__)
        return
    now = datetime.now(timezone.utc)
    by_date: dict = {}
    for hold in holds:
        if hold.contract_labor_id is None or hold.gave_up_datetime is not None or hold.work_date in fired_dates:
            continue
        if not _hold_is_due(repo, hold, now):
            continue
        by_date.setdefault(hold.work_date, []).append(hold)
    for work_date, due in by_date.items():
        fired_dates.add(work_date)
        if _safe_refire(fire, due[0].contract_labor_id):
            summary["swept"] += 1
        for hold in due:
            _record_hold_fire(repo, hold)


def _hold_is_due(repo: Any, hold: Any, now: datetime) -> bool:
    if _date_waits_on_a_submit(repo, hold.work_date):
        return False
    if now - _as_utc(hold.held_datetime) >= timedelta(minutes=CL_SUGGESTION_WAIT_MINUTES):
        return True
    try:
        rows, _labels = repo.read_suggestions_for_project_day(hold.project_id, hold.work_date)
    except Exception as error:
        logger.error("cl_review_hold.due_read_failed hold_id=%s error=%s", hold.id, type(error).__name__)
        return False
    return crew_day_settled(rows)


async def run_cl_coding_tick(
    *,
    limit: int = 1,
    settings: Any = None,
    repo: Any = None,
    line_item_repo: Any = None,
    budget_stopped: Optional[Callable[[], Awaitable[bool]]] = None,
    spend_headroom: Optional[Callable[[], Awaitable[bool]]] = None,
    model_call: Optional[ModelCall] = None,
    refire: Optional[Callable[[int], None]] = None,
) -> dict[str, Any]:
    cfg = settings if settings is not None else config.Settings()
    if repo is None:
        from entities.contract_labor.persistence.coding_suggestion_repo import (
            ContractLaborCodingSuggestionRepository,
        )

        repo = ContractLaborCodingSuggestionRepository()
    fire = refire if refire is not None else _fire_crew_drafts
    fired_dates: set = set()
    summary: dict[str, Any] = {
        "claimed": 0, "completed": 0, "failed": 0, "deferred": 0, "unavailable": 0, "refired": 0, "swept": 0,
    }

    if cfg.cl_coding_shadow_enabled:
        stopped = budget_stopped if budget_stopped is not None else claude_budget.is_claude_day_stopped_async
        headroom = spend_headroom if spend_headroom is not None else _spend_headroom_async
        if line_item_repo is None:
            from entities.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository

            line_item_repo = ContractLaborLineItemRepository()
        caller = model_call if model_call is not None else _live_model_call
        try:
            await _claim_loop(limit=limit, cfg=cfg, repo=repo, line_repo=line_item_repo, caller=caller,
                              stopped=stopped, headroom=headroom, fire=fire, summary=summary,
                              fired_dates=fired_dates)
        except Exception as error:
            logger.error("cl_coding_shadow.tick_failed error=%s", type(error).__name__)
    else:
        summary["skipped"] = "disabled"

    _sweep_review_holds(repo, fire, summary, fired_dates)
    return summary


def run_cl_coding_tick_sync(**kwargs: Any) -> dict[str, Any]:
    """Sync entry for the admin endpoint's worker thread."""
    return asyncio.run(run_cl_coding_tick(**kwargs))


__all__ = [
    "CL_SUGGESTION_WAIT_MINUTES",
    "ContractLaborCodingShadowService",
    "SPEND_HEADROOM_FRACTION",
    "SuggestionDisplay",
    "run_cl_coding_tick",
    "run_cl_coding_tick_sync",
    "spend_has_headroom",
    "crew_day_settled",
    "crew_members",
    "suggestion_display",
    "suggestion_hold",
    "suggestion_label",
]
