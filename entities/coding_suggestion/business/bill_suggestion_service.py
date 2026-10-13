"""Deterministic bill coding suggestion (U-653a BITE 2)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Sequence

from entities.bill.business.model import Bill
from entities.bill_line_item.business.model import BillLineItem
from entities.coding_suggestion.business.hint_extractor import extract_bill_hints
from entities.coding_suggestion.business.model import VendorDominantSubCostCodeForBills
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.bill_line_item.business.service import BillLineItemService
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from shared.access import EntityNotAccessibleError, assert_can_access_project
from shared.authz import current_user_id

logger = logging.getLogger(__name__)

MODEL_VERSION = "u653a-history-v1"

_PROJECT_CODE_CONFIDENCE = Decimal("0.95")


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
    }
    return json.dumps(payload, sort_keys=True)


def chosen_project_id_from_lines(lines: Sequence[BillLineItem]) -> Optional[int]:
    project_ids = {line.project_id for line in lines if line.project_id is not None}
    if len(project_ids) == 1:
        return next(iter(project_ids))
    return None


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
        suggestion = engine.suggest(
            vendor_id=int(bill.vendor_id),
            project_id=project_id,
            memo=bill.memo,
            exclude_bill_id=bill.id,
        )
        svc = coding_suggestion_service or CodingSuggestionService()
        svc.upsert_for_bill(
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
    except Exception as error:
        logger.warning(
            "Bill coding suggestion record failed for bill %s: %s",
            getattr(bill, "public_id", None),
            type(error).__name__,
        )
