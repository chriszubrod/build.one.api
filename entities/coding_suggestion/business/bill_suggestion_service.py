"""Deterministic bill coding suggestion (U-653a BITE 2)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Mapping, Optional, Sequence

import config
from entities.bill.business.model import Bill
from entities.bill_line_item.business.model import BillLineItem
from entities.coding_suggestion.business.budget_check import (
    chosen_project_id_from_lines,
    evaluate_budget_for_suggestion,
    project_confident_for_suggestion,
)
from entities.coding_suggestion.business.hint_extractor import extract_bill_hints
from entities.coding_suggestion.business.model import VendorDominantSubCostCodeForBills
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import (
    BillLatestTextExtractionState,
    BillTextCodingSuggestionRepository,
    BillTextCodingSuggestionRow,
)
from entities.bill_line_item.business.service import BillLineItemService
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from shared.access import EntityNotAccessibleError, assert_can_access_project
from shared.authz import current_user_id

logger = logging.getLogger(__name__)

MODEL_VERSION = "u653a-history-v1"

_PROJECT_CODE_CONFIDENCE = Decimal("0.95")
HISTORY_STRONG_RAW_SCORE = Decimal("0.60")
_HISTORY_SOURCES = frozenset({"vendor_history", "vendor_project_history"})
TEXT_LLM_SUGGESTION_REASON = "Suggested from the bill's document text"
TextTierStatus = Literal["ready", "pending", "none"]
# Every value BillSuggestion.source can carry (API `source`). "similar_bills" is the
# U-701b4 filtered similar-bills vote (ladder V2 only).
SUGGESTION_SOURCE_SIMILAR_BILLS = "similar_bills"
BillSuggestionSource = Literal[
    "vendor_project_history", "vendor_history", "memo", "llm", "similar_bills", "none"
]
SIMILAR_BILLS_SUGGESTION_REASON = "Suggested from similar past bills"
# A drop's escalation older than this has no tick answer coming: stop reporting pending.
ESCALATION_PENDING_MAX_AGE = timedelta(hours=2)


@dataclass
class BillSuggestion:
    project_id: Optional[int]
    sub_cost_code_id: Optional[int]
    sub_cost_code_public_id: Optional[str]
    sub_cost_code_number: Optional[str]
    sub_cost_code_name: Optional[str]
    source: str
    reason: Optional[str]
    confidence: Optional[Decimal]
    raw_score: Optional[Decimal]
    top_count: Optional[int]
    second_count: Optional[int]
    total_count: Optional[int]
    model_version: str = MODEL_VERSION
    predicted_project: bool = False
    budget_check: Optional[str] = None
    budget_remaining: Optional[Decimal] = None
    budget_evidence: Optional[Mapping[str, Decimal]] = None
    text_used: bool = False
    text_mode: Optional[str] = None
    text_model: Optional[str] = None
    text_status: Optional[str] = None
    project_confidence: Optional[Decimal] = None
    # U-701b4 (ladder V2 only; None = not emitted): which ladder rung produced the answer,
    # and - for a background escalate that changed the drop's code - the drop's SCC it replaces.
    decision_source: Optional[str] = None
    supersedes_sub_cost_code_public_id: Optional[str] = None


def _smoothed_share(top_count: int, total_count: int) -> Decimal:
    return Decimal(top_count) / Decimal(total_count + 2)


def _history_reason(
    history: VendorDominantSubCostCodeForBills,
    *,
    source: str,
) -> str:
    scc_label = history.number or "SCC"
    if source == "vendor_project_history":
        return (
            f"{history.top_count} of {history.total_count} prior bills from this vendor "
            f"on this project used {scc_label}"
        )
    return (
        f"{history.top_count} of {history.total_count} prior bills from this vendor "
        f"used {scc_label}"
    )


def _from_history(
    history: VendorDominantSubCostCodeForBills,
    *,
    project_id: Optional[int],
    source: str,
    predicted_project: bool = False,
) -> BillSuggestion:
    raw_score = _smoothed_share(history.top_count, history.total_count)
    # raw_score equals confidence until U-653b calibration separates them.
    return BillSuggestion(
        project_id=project_id,
        sub_cost_code_id=history.top_sub_cost_code_id,
        sub_cost_code_public_id=history.public_id,
        sub_cost_code_number=history.number,
        sub_cost_code_name=history.name,
        source=source,
        reason=_history_reason(history, source=source),
        confidence=raw_score,
        raw_score=raw_score,
        top_count=history.top_count,
        second_count=history.second_count,
        total_count=history.total_count,
        predicted_project=predicted_project,
    )


def _none_suggestion(*, project_id: Optional[int] = None) -> BillSuggestion:
    return BillSuggestion(
        project_id=project_id,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )


def rollup_prefill_state(states: Sequence[Optional[str]]) -> Optional[str]:
    """Roll up line CodingPrefillState values for CodingSuggestion.PrefillState."""
    values = [state for state in states if state is not None]
    if not values:
        return None
    if "changed" in values:
        return "changed"
    if "prefilled" in values:
        return "prefilled"
    return "accepted"


def bill_suggestion_to_features_json(suggestion: BillSuggestion) -> str:
    """Allowlisted feature payload — no display names or free-text reason."""
    payload: dict[str, Any] = {
        "source": suggestion.source,
        "rung": suggestion.source,
        "top_count": suggestion.top_count,
        "second_count": suggestion.second_count,
        "total_count": suggestion.total_count,
        "raw_score": (
            str(suggestion.raw_score) if suggestion.raw_score is not None else None
        ),
        "confidence": (
            str(suggestion.confidence) if suggestion.confidence is not None else None
        ),
        "model_version": suggestion.model_version,
        "project_id": suggestion.project_id,
        "sub_cost_code_id": suggestion.sub_cost_code_id,
        "predicted_project": suggestion.predicted_project,
        "budget_check": suggestion.budget_check,
        "budget_remaining": (
            str(suggestion.budget_remaining)
            if suggestion.budget_remaining is not None
            else None
        ),
    }
    if suggestion.budget_evidence:
        for key, value in suggestion.budget_evidence.items():
            payload[key] = str(value)
    if suggestion.text_used:
        payload["text_used"] = True
        payload["text_mode"] = suggestion.text_mode
        payload["text_model"] = suggestion.text_model
    return json.dumps(payload, sort_keys=True)


def history_blocks_text_tier(suggestion: BillSuggestion) -> bool:
    """Strong vendor history SCC wins over stored text (U-654a §10.3 combined Mode A)."""
    if suggestion.sub_cost_code_id is None:
        return False
    if suggestion.source not in _HISTORY_SOURCES:
        return False
    raw = suggestion.raw_score
    return raw is not None and raw >= HISTORY_STRONG_RAW_SCORE


def _text_row_matches_current_extraction(
    text_row: BillTextCodingSuggestionRow,
    extraction: BillLatestTextExtractionState,
) -> bool:
    latest = extraction.latest_completed_extracted_datetime
    if latest is None:
        return False
    if text_row.extracted_datetime != latest:
        return False
    latest_att = extraction.latest_completed_attachment_id
    if latest_att is not None and text_row.attachment_id != latest_att:
        return False
    return True


def resolve_accessible_text_project_id(
    project_id: Optional[int],
    *,
    project_service: ProjectService,
) -> Optional[int]:
    if project_id is None:
        return None
    project = project_service.read_by_id(int(project_id))
    if project is None or project.id is None:
        return None
    try:
        assert_can_access_project(int(project.id))
    except EntityNotAccessibleError:
        return None
    return int(project.id)


def bill_suggestion_from_text_cascade(
    cascade,
    *,
    catalog,
    mode: str,
    chosen_project_id: Optional[int],
    project_service: ProjectService,
    sub_cost_code_service: SubCostCodeService,
) -> Optional[BillSuggestion]:
    from entities.coding_suggestion.business.bill_text_suggestion_service import MODEL_VERSION
    from intelligence.cascade.bill_coding_payload import resolve_text_prediction

    scc_id, proj_id = resolve_text_prediction(
        cascade,
        scc_number_to_id=catalog.scc_number_to_id,
        proj_abbrev_to_id=catalog.proj_abbrev_to_id,
    )
    if scc_id is None:
        return None
    scc = sub_cost_code_service.read_by_id(int(scc_id))
    if scc is None or sub_cost_code_service.is_holding_code(int(scc_id)):
        return None
    out_project = proj_id if mode == "B" else chosen_project_id
    out_project = resolve_accessible_text_project_id(
        out_project,
        project_service=project_service,
    )
    confidence = (
        Decimal(str(cascade.confidence)) if cascade.confidence is not None else None
    )
    winning_model = cascade.winning_rung.model if cascade.winning_rung else None
    return BillSuggestion(
        project_id=out_project,
        sub_cost_code_id=int(scc_id),
        sub_cost_code_public_id=str(scc.public_id) if scc.public_id else None,
        sub_cost_code_number=scc.number,
        sub_cost_code_name=scc.name,
        source="llm",
        reason=TEXT_LLM_SUGGESTION_REASON,
        confidence=confidence,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        model_version=MODEL_VERSION,
        predicted_project=mode == "B" and out_project is not None,
        text_used=True,
        text_mode=mode,
        text_model=winning_model,
    )


def _bill_suggestion_from_text_row(
    text_row: BillTextCodingSuggestionRow,
    *,
    sub_cost_code_service: SubCostCodeService,
    output_project_id: Optional[int],
    predicted_project: bool,
) -> Optional[BillSuggestion]:
    scc_id = text_row.sub_cost_code_id
    if scc_id is None:
        return None
    scc = sub_cost_code_service.read_by_id(int(scc_id))
    if scc is None:
        return None
    if sub_cost_code_service.is_holding_code(int(scc_id)):
        return None
    return BillSuggestion(
        project_id=output_project_id,
        sub_cost_code_id=int(scc_id),
        sub_cost_code_public_id=str(scc.public_id) if scc.public_id else None,
        sub_cost_code_number=scc.number,
        sub_cost_code_name=scc.name,
        source="llm",
        reason=TEXT_LLM_SUGGESTION_REASON,
        confidence=text_row.confidence,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        model_version=text_row.model_version,
        predicted_project=predicted_project,
        text_used=True,
        text_mode=text_row.mode,
        text_model=text_row.winning_model,
    )


def _text_row_terminal_for_current(
    text_row: Optional[BillTextCodingSuggestionRow],
    extraction: Optional[BillLatestTextExtractionState],
) -> bool:
    """A finished (non-pending) text row exists for the bill's current extraction."""
    return (
        text_row is not None
        and text_row.status != "pending"
        and extraction is not None
        and _text_row_matches_current_extraction(text_row, extraction)
    )


def compute_text_tier_status(
    *,
    history_blocked: bool,
    text_row: Optional[BillTextCodingSuggestionRow],
    extraction: Optional[BillLatestTextExtractionState],
    escalation_pending: bool = False,
) -> TextTierStatus:
    """Status when the text result is NOT applied: "pending" while one may still arrive.

    escalation_pending (U-701b4, ladder V2): the bill's drop marked its coding for the
    background tick (CodingEscalation set, recently). Until a terminal text row exists for
    the current extraction the tick's answer may still arrive, so an open editor keeps
    polling - but never when the bill has no current completed extraction (no tick row
    can come).
    """
    if (
        escalation_pending
        and not history_blocked
        and extraction is not None
        and extraction.latest_completed_extracted_datetime is not None
        and not _text_row_terminal_for_current(text_row, extraction)
    ):
        return "pending"
    if text_row is None:
        incomplete = extraction is not None and extraction.has_incomplete_extraction
        return "pending" if incomplete and not history_blocked else "none"
    if text_row.status == "pending":
        return "pending"
    if text_row.status != "done" or history_blocked or extraction is None:
        return "none"
    stale = not _text_row_matches_current_extraction(text_row, extraction)
    return "pending" if extraction.has_incomplete_extraction or stale else "none"


def apply_bill_text_tier_to_suggestion(
    history_suggestion: BillSuggestion,
    *,
    bill_id: Optional[int],
    request_project_id: Optional[int],
    text_repo: Optional[BillTextCodingSuggestionRepository] = None,
    sub_cost_code_service: Optional[SubCostCodeService] = None,
    project_service: Optional[ProjectService] = None,
    text_enabled: Optional[bool] = None,
    ladder_v2_enabled: Optional[bool] = None,
    intake_service: Any = None,
) -> tuple[BillSuggestion, TextTierStatus]:
    """Merge stored text-tier SCC (and optional Mode-B project) over cold/weak history.

    Ladder V2 (U-701b4, flag ON only): the result carries ``decision_source``; an escalate
    answer that differs from the drop's Haiku (primary) SCC carries
    ``supersedes_sub_cost_code_public_id``; a similar-bills vote is presented as source
    "similar_bills"; status stays "pending" while the drop's escalation is outstanding.
    """
    settings: Any = None
    if text_enabled is None or ladder_v2_enabled is None:
        settings = config.Settings()
    enabled = text_enabled if text_enabled is not None else settings.bill_coding_text_enabled
    if not enabled or bill_id is None:
        return history_suggestion, "none"
    v2 = (
        ladder_v2_enabled
        if ladder_v2_enabled is not None
        else getattr(settings, "bill_coding_ladder_v2_enabled", False)
    ) is True

    repository = text_repo or BillTextCodingSuggestionRepository()
    scc_svc = sub_cost_code_service or SubCostCodeService()
    proj_svc = project_service or ProjectService()

    history_blocked = history_blocks_text_tier(history_suggestion)
    try:
        text_row = repository.read_by_bill_id(int(bill_id))
        # A blocked status never consults the extraction, so skip that read.
        extraction = (
            None if history_blocked else repository.read_latest_text_extraction_state(int(bill_id))
        )
    except Exception as error:
        logger.warning(
            "bill text tier read failed bill_id=%s err=%s",
            bill_id,
            type(error).__name__,
        )
        return history_suggestion, "none"

    intake_cache: list[Any] = []

    def _intake() -> Any:
        if not intake_cache:
            intake_cache.append(_read_intake_for_bill(int(bill_id), intake_service))
        return intake_cache[0]

    def _history_only() -> tuple[BillSuggestion, TextTierStatus]:
        escalation_pending = False
        if v2:
            if history_blocked:
                history_suggestion.decision_source = "history"
            elif not _text_row_terminal_for_current(text_row, extraction):
                escalation_pending = _intake_escalation_pending(_intake(), extraction)
        return history_suggestion, compute_text_tier_status(
            history_blocked=history_blocked,
            text_row=text_row,
            extraction=extraction,
            escalation_pending=escalation_pending,
        )

    if history_blocked:
        return _history_only()

    usable_done = (
        text_row is not None
        and text_row.status == "done"
        and text_row.sub_cost_code_id is not None
        and _text_row_matches_current_extraction(text_row, extraction)
    )
    if not usable_done:
        return _history_only()

    project_confidence = history_suggestion.raw_score or history_suggestion.confidence
    if request_project_id is not None:
        output_project = request_project_id
        predicted_project = False
        project_confidence = None
    elif text_row.mode == "B":
        output_project = None
        predicted_project = False
        if text_row.project_id is not None:
            accessible = resolve_accessible_text_project_id(
                text_row.project_id,
                project_service=proj_svc,
            )
            if accessible is not None:
                output_project = accessible
                predicted_project = True
                project_confidence = text_row.confidence
    else:
        output_project = history_suggestion.project_id
        predicted_project = history_suggestion.predicted_project

    text_suggestion = _bill_suggestion_from_text_row(
        text_row,
        sub_cost_code_service=scc_svc,
        output_project_id=output_project,
        predicted_project=predicted_project,
    )
    if text_suggestion is None:
        return _history_only()

    text_suggestion.project_confidence = project_confidence
    if v2:
        _apply_ladder_v2_presentation(
            text_suggestion,
            text_row=text_row,
            intake=_intake,
            sub_cost_code_service=scc_svc,
        )
    return text_suggestion, "ready"


def _read_intake_for_bill(bill_id: int, intake_service: Any) -> Any:
    """The bill's intake row (failure-isolated: None on any error)."""
    try:
        if intake_service is None:
            from entities.bill_intake_prediction.business.service import (
                BillIntakePredictionService,
            )

            intake_service = BillIntakePredictionService()
        return intake_service.read_by_bill_id(int(bill_id))
    except Exception as error:
        logger.warning(
            "bill text tier intake read failed bill_id=%s err=%s",
            bill_id,
            type(error).__name__,
        )
        return None


def _intake_escalation_pending(
    intake: Any,
    extraction: Optional[BillLatestTextExtractionState],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """The drop marked this bill for the tick, for the attachment that is current now,
    and not longer ago than ESCALATION_PENDING_MAX_AGE (past that no tick row will come).

    The intake model carries no write timestamp; its extracted_datetime (the extraction
    the drop read, naive UTC like the DB) dates the escalation."""
    if intake is None or not getattr(intake, "coding_escalation", None):
        return False
    stamped = getattr(intake, "extracted_datetime", None)
    if stamped is None and extraction is not None:
        stamped = extraction.latest_completed_extracted_datetime
    if stamped is None:
        return False
    current = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if current - stamped > ESCALATION_PENDING_MAX_AGE:
        return False
    latest_att = extraction.latest_completed_attachment_id if extraction is not None else None
    intake_att = getattr(intake, "attachment_id", None)
    if latest_att is not None and intake_att is not None and int(intake_att) != int(latest_att):
        return False
    return True


def _apply_ladder_v2_presentation(
    suggestion: BillSuggestion,
    *,
    text_row: BillTextCodingSuggestionRow,
    intake: Any,
    sub_cost_code_service: SubCostCodeService,
) -> None:
    decision_source = getattr(text_row, "decision_source", None)
    suggestion.decision_source = decision_source
    # U-745: an M0 pick sits at the vote's rung and is presented the same way. (Lazy:
    # bill_coding_decision imports this module.)
    from entities.coding_suggestion.business.bill_coding_decision import (
        NO_LLM_LAST_RUNG_SOURCES,
    )

    if decision_source in NO_LLM_LAST_RUNG_SOURCES:
        suggestion.source = SUGGESTION_SOURCE_SIMILAR_BILLS
        suggestion.reason = SIMILAR_BILLS_SUGGESTION_REASON
        return
    if decision_source != "escalate" or text_row.sub_cost_code_id is None:
        return
    # Only the DROP's prefill can be superseded: the intake row's Primary (what the drop
    # wrote), never a full tick's own primary (that never reached the draft).
    intake_row = intake()
    primary_id = getattr(intake_row, "primary_sub_cost_code_id", None) if intake_row else None
    if primary_id is None or int(primary_id) == int(text_row.sub_cost_code_id):
        return
    try:
        primary_scc = sub_cost_code_service.read_by_id(int(primary_id))
    except Exception as error:
        logger.warning("bill text tier primary scc read failed err=%s", type(error).__name__)
        return
    if primary_scc is not None and primary_scc.public_id:
        suggestion.supersedes_sub_cost_code_public_id = str(primary_scc.public_id)


def apply_budget_check_to_suggestion(
    suggestion: BillSuggestion,
    lines: Sequence[BillLineItem],
    *,
    chosen_project_id: Optional[int] = None,
    amount_override: Optional[Decimal] = None,
) -> BillSuggestion:
    """Mutates suggestion in place with budget_check / budget_remaining (failure-isolated by callers)."""
    confident = project_confident_for_suggestion(
        suggestion.project_id,
        chosen_project_id=chosen_project_id,
        confidence=suggestion.confidence,
        project_confidence=suggestion.project_confidence,
    )
    result = evaluate_budget_for_suggestion(
        suggestion.project_id,
        suggestion.sub_cost_code_id,
        lines=lines,
        project_confident=confident,
        amount_override=amount_override,
    )
    suggestion.budget_check = result.check
    suggestion.budget_remaining = result.remaining
    suggestion.budget_evidence = dict(result.evidence) if result.evidence else None
    return suggestion


class BillCodingSuggester:
    """Pure orchestration over bill history + memo resolvers (injectable for tests)."""

    def __init__(
        self,
        *,
        coding_suggestion_service: Optional[CodingSuggestionService] = None,
        project_service: Optional[ProjectService] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
    ):
        self.coding_suggestion_service = coding_suggestion_service or CodingSuggestionService()
        self.project_service = project_service or ProjectService()
        self.sub_cost_code_service = sub_cost_code_service or SubCostCodeService()

    def suggest(
        self,
        *,
        vendor_id: int,
        project_id: Optional[int] = None,
        memo: Optional[str] = None,
        as_of=None,
        exclude_bill_id: Optional[int] = None,
    ) -> BillSuggestion:
        chosen_project = project_id
        output_project = chosen_project
        predicted_project = False
        history_svc = self.coding_suggestion_service

        lookup_project = chosen_project
        if lookup_project is None:
            project_history = history_svc.read_vendor_dominant_project_for_bills(
                vendor_id,
                as_of=as_of,
                exclude_bill_id=exclude_bill_id,
            )
            if project_history is not None:
                lookup_project = project_history.top_project_id
                output_project = lookup_project
                predicted_project = True

        if lookup_project is not None:
            history = history_svc.read_vendor_dominant_sub_cost_code_for_bills(
                vendor_id,
                project_id=lookup_project,
                as_of=as_of,
                exclude_bill_id=exclude_bill_id,
            )
            if history is not None:
                return _from_history(
                    history,
                    project_id=output_project,
                    source="vendor_project_history",
                    predicted_project=predicted_project,
                )

        history = history_svc.read_vendor_dominant_sub_cost_code_for_bills(
            vendor_id,
            project_id=None,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        )
        if history is not None:
            return _from_history(
                history,
                project_id=output_project,
                source="vendor_history",
                predicted_project=predicted_project,
            )

        memo_suggestion = self._suggest_from_memo(memo, output_project=output_project)
        if memo_suggestion is not None:
            return memo_suggestion

        return _none_suggestion(project_id=output_project)

    def suggest_with_text_tier(
        self,
        *,
        vendor_id: int,
        project_id: Optional[int] = None,
        memo: Optional[str] = None,
        as_of=None,
        exclude_bill_id: Optional[int] = None,
        bill_id: Optional[int] = None,
        text_repo: Optional[BillTextCodingSuggestionRepository] = None,
        text_enabled: Optional[bool] = None,
    ) -> tuple[BillSuggestion, TextTierStatus]:
        history = self.suggest(
            vendor_id=vendor_id,
            project_id=project_id,
            memo=memo,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        )
        return apply_bill_text_tier_to_suggestion(
            history,
            bill_id=bill_id,
            request_project_id=project_id,
            text_repo=text_repo,
            sub_cost_code_service=self.sub_cost_code_service,
            project_service=self.project_service,
            text_enabled=text_enabled,
        )

    def _suggest_from_memo(
        self,
        memo: Optional[str],
        *,
        output_project: Optional[int],
    ) -> Optional[BillSuggestion]:
        hints = extract_bill_hints(memo)
        project_id: Optional[int] = output_project

        project_confidence: Optional[Decimal] = None
        project_reason: Optional[str] = None

        if output_project is None and hints.get("project_hint"):
            project = self.project_service.read_by_abbreviation(hints["project_hint"])
            if project is not None and project.id is not None:
                try:
                    assert_can_access_project(int(project.id))
                    project_id = int(project.id)
                    project_confidence = _PROJECT_CODE_CONFIDENCE
                    project_reason = f"matched project code {hints['project_hint']}"
                except EntityNotAccessibleError:
                    pass

        if project_id is None and hints.get("address_hint"):
            candidates = self.project_service.find_for_invoice(address_hint=hints["address_hint"])
            if candidates:
                top = candidates[0]
                candidate_id = top["project"]["id"]
                try:
                    assert_can_access_project(int(candidate_id))
                    project_id = int(candidate_id)
                    project_confidence = Decimal(str(top["confidence"]))
                    project_reason = f"address match {top['project']['name']}"
                except EntityNotAccessibleError:
                    pass

        sub_cost_code_id: Optional[int] = None
        scc_number: Optional[str] = None
        scc_public_id: Optional[str] = None
        scc_name: Optional[str] = None
        scc_confidence: Optional[Decimal] = None
        scc_reason: Optional[str] = None

        if hints.get("scc_hint"):
            candidates = self.sub_cost_code_service.find_for_reply(hint=hints["scc_hint"])
            if candidates:
                top = candidates[0]
                scc = top["sub_cost_code"]
                sub_cost_code_id = int(scc["id"])
                scc_public_id = scc.get("public_id")
                scc_number = scc.get("number")
                scc_name = scc.get("name")
                scc_confidence = Decimal(str(top["confidence"]))
                scc_reason = f"cost-code shorthand {hints['scc_hint']}"

        if not project_reason and not scc_reason:
            return None

        reasons = [reason for reason in (project_reason, scc_reason) if reason]
        confidences = [
            value for value in (project_confidence, scc_confidence) if value is not None
        ]
        confidence = min(confidences) if confidences else None

        return BillSuggestion(
            project_id=project_id,
            sub_cost_code_id=sub_cost_code_id,
            sub_cost_code_public_id=scc_public_id,
            sub_cost_code_number=scc_number,
            sub_cost_code_name=scc_name,
            source="memo",
            reason="; ".join(reasons),
            confidence=confidence,
            raw_score=confidence,
            top_count=None,
            second_count=None,
            total_count=None,
        )


def record_bill_coding_suggestion_on_save(
    bill: Bill,
    *,
    suggester: Optional[BillCodingSuggester] = None,
    coding_suggestion_service: Optional[CodingSuggestionService] = None,
    line_item_service: Optional[BillLineItemService] = None,
) -> None:
    """Persist one CodingSuggestion row when a bill leaves draft (failure-isolated)."""
    try:
        if bill.id is None or not bill.vendor_id:
            return

        line_svc = line_item_service or BillLineItemService()
        lines = line_svc.read_by_bill_id(bill.id)
        project_id = chosen_project_id_from_lines(lines)
        engine = suggester or BillCodingSuggester()
        suggestion, _text_status = engine.suggest_with_text_tier(
            vendor_id=int(bill.vendor_id),
            project_id=project_id,
            memo=bill.memo,
            exclude_bill_id=bill.id,
            bill_id=bill.id,
        )
        try:
            apply_budget_check_to_suggestion(
                suggestion,
                lines,
                chosen_project_id=project_id,
            )
        except Exception as error:
            logger.warning(
                "Bill coding budget check failed for bill %s: %s",
                getattr(bill, "public_id", None),
                type(error).__name__,
            )
            suggestion.budget_check = None
            suggestion.budget_remaining = None
            suggestion.budget_evidence = None
        svc = coding_suggestion_service or CodingSuggestionService()
        upsert_kwargs: dict[str, Any] = dict(
            bill_id=bill.id,
            vendor_id=bill.vendor_id,
            suggested_project_id=suggestion.project_id,
            suggested_sub_cost_code_id=suggestion.sub_cost_code_id,
            suggestion_source=suggestion.source if suggestion.source != "none" else None,
            suggestion_reason=suggestion.reason,
            suggestion_confidence=suggestion.confidence,
            features_json=bill_suggestion_to_features_json(suggestion),
            model_version=suggestion.model_version,
            prefill_state=rollup_prefill_state([line.coding_prefill_state for line in lines]),
            created_by_user_id=current_user_id.get(),
            replace=True,
        )
        if suggestion.budget_check is not None:
            upsert_kwargs["budget_check"] = suggestion.budget_check
        if suggestion.budget_remaining is not None:
            upsert_kwargs["budget_remaining"] = suggestion.budget_remaining
        svc.upsert_for_bill(**upsert_kwargs)
    except Exception as error:
        logger.warning(
            "Bill coding suggestion record failed for bill %s: %s",
            getattr(bill, "public_id", None),
            type(error).__name__,
        )


_resolve_accessible_text_project_id = resolve_accessible_text_project_id
