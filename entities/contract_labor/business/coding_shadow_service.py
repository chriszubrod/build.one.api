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
from datetime import date, datetime, timezone
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


def _record_attempt_failure(repo: Any, claim: Any, *, model: Optional[str], reason: str) -> None:
    """One failed attempt on a claimed row (model unavailable, or an exception while processing it).

    Attempts 1 and 2 release the row to pending with AttemptCount +1 (a fresh lease). The third strike
    completes it as 'failed' with the reason (a class name, or 'model_unavailable'). Never raises.
    """
    try:
        if int(claim.attempt_count) + 1 >= MAX_ATTEMPTS:
            repo.complete(claim.id, status="failed", model=model, failure_reason=reason)
        else:
            repo.release(claim.id, count_attempt=True)
    except Exception as error:
        logger.error(
            "cl_coding_shadow.attempt_record_failed suggestion_id=%s error=%s",
            claim.id,
            type(error).__name__,
        )


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


async def run_cl_coding_tick(
    *,
    limit: int = 1,
    settings: Any = None,
    repo: Any = None,
    line_item_repo: Any = None,
    budget_stopped: Optional[Callable[[], Awaitable[bool]]] = None,
    spend_headroom: Optional[Callable[[], Awaitable[bool]]] = None,
    model_call: Optional[ModelCall] = None,
) -> dict[str, Any]:
    cfg = settings if settings is not None else config.Settings()
    if not cfg.cl_coding_shadow_enabled:
        return {"skipped": "disabled"}

    stopped = budget_stopped if budget_stopped is not None else claude_budget.is_claude_day_stopped_async
    headroom = spend_headroom if spend_headroom is not None else _spend_headroom_async

    if repo is None:
        from entities.contract_labor.persistence.coding_suggestion_repo import (
            ContractLaborCodingSuggestionRepository,
        )

        repo = ContractLaborCodingSuggestionRepository()
    if line_item_repo is None:
        from entities.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository

        line_item_repo = ContractLaborLineItemRepository()

    caller = model_call if model_call is not None else _live_model_call
    cap = int(cfg.cl_coding_shadow_daily_call_cap)
    today = datetime.now(timezone.utc).date()
    summary: dict[str, Any] = {"claimed": 0, "completed": 0, "failed": 0, "deferred": 0, "unavailable": 0}
    seen: set[int] = set()

    for _ in range(max(1, int(limit))):
        claim = repo.claim_next()
        if claim is None:
            break
        if claim.id in seen:
            break  # never process the same row twice in one tick
        seen.add(claim.id)
        summary["claimed"] += 1
        attempt: dict[str, Optional[str]] = {"model": None}
        try:
            outcome = await _process_claim(
                claim,
                repo=repo,
                line_repo=line_item_repo,
                cap=cap,
                model_call=caller,
                attempt=attempt,
                stopped=stopped,
                headroom=headroom,
                today=today,
            )
        except Exception as error:
            # Infrastructure or row fault: an attempt failure, then stop the tick.
            summary["failed"] += 1
            logger.error("cl_coding_shadow.row_failed suggestion_id=%s error=%s", claim.id, type(error).__name__)
            _record_attempt_failure(repo, claim, model=attempt.get("model"), reason=type(error).__name__)
            break

        if outcome == "unavailable":
            summary["unavailable"] += 1
            _record_attempt_failure(repo, claim, model=attempt.get("model"), reason="model_unavailable")
            break  # the model is down: stop the tick
        if outcome == "deferred":
            summary["deferred"] += 1
            continue  # a blocked row does not block the rows behind it
        summary["completed"] += 1

    return summary


def run_cl_coding_tick_sync(**kwargs: Any) -> dict[str, Any]:
    """Sync entry for the admin endpoint's worker thread."""
    return asyncio.run(run_cl_coding_tick(**kwargs))


__all__ = [
    "ContractLaborCodingShadowService",
    "SPEND_HEADROOM_FRACTION",
    "run_cl_coding_tick",
    "run_cl_coding_tick_sync",
    "spend_has_headroom",
]
