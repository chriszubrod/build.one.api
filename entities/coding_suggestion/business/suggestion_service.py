# Python Standard Library Imports
import logging
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

# Local Imports
from entities.coding_suggestion.business.hint_extractor import extract_hints
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from integrations.intuit.qbo.base.cost_code_resolver import (
    resolve_qbo_item_ref as _default_resolve_qbo_item_ref,
)
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService

logger = logging.getLogger(__name__)

_PROJECT_CODE_CONFIDENCE = Decimal("0.95")

# Stored in dbo.CodingSuggestion.SuggestionSource (joined with "+").
SOURCE_PROJECT_CODE = "code"
SOURCE_PROJECT_ADDRESS = "address"
SOURCE_SCC_SHORTHAND = "scc_shorthand"
SOURCE_VENDOR_HISTORY = "vendor_history"

PREFIX_MATCHED_PROJECT_CODE = "matched project code"
PREFIX_ADDRESS_MATCH = "address match"
PREFIX_COST_CODE_SHORTHAND = "cost-code shorthand"
PREFIX_VENDOR_CODED_SCC = "vendor coded to SCC"
PREFIX_PROJECT_UNRESOLVED = "project unresolved"
PREFIX_SCC_UNRESOLVED = "sub-cost-code unresolved"


def format_project_code_reason(project_hint: str) -> str:
    return f"{PREFIX_MATCHED_PROJECT_CODE} {project_hint}"


def format_address_match_reason(project_name: str) -> str:
    return f"{PREFIX_ADDRESS_MATCH} {project_name}"


def format_scc_shorthand_reason(scc_hint: str) -> str:
    return f"{PREFIX_COST_CODE_SHORTHAND} {scc_hint}"


def split_suggestion_reason(
    suggestion_reason: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Split a persisted SuggestionReason into project- and SCC-facing fragments."""
    if not suggestion_reason:
        return None, None
    project_part: Optional[str] = None
    scc_part: Optional[str] = None
    for piece in suggestion_reason.split(";"):
        trimmed = piece.strip()
        if not trimmed or trimmed.startswith(PREFIX_PROJECT_UNRESOLVED) or trimmed.startswith(
            PREFIX_SCC_UNRESOLVED
        ):
            continue
        if trimmed.startswith(PREFIX_MATCHED_PROJECT_CODE) or trimmed.startswith(PREFIX_ADDRESS_MATCH):
            project_part = trimmed
        elif trimmed.startswith(PREFIX_COST_CODE_SHORTHAND) or trimmed.startswith(PREFIX_VENDOR_CODED_SCC):
            scc_part = trimmed
    return project_part, scc_part


class CodingSuggestionEngine:
    """Deterministic suggestion engine for expense coding queue items (Phase B)."""

    def __init__(
        self,
        *,
        project_service: Optional[ProjectService] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
        coding_item_service: Optional[CodingSuggestionService] = None,
        suggestion_repo: Optional[CodingSuggestionRepository] = None,
        resolve_qbo_item_ref=None,
        qbo_purchase_service: Optional[QboPurchaseService] = None,
    ):
        self.project_service = project_service or ProjectService()
        self.sub_cost_code_service = sub_cost_code_service or SubCostCodeService()
        self.coding_item_service = coding_item_service or CodingSuggestionService()
        self.suggestion_repo = suggestion_repo or CodingSuggestionRepository()
        # U-307c: resolves "SubCostCode has a writable QBO item" dbo-natively
        # (dbo.SubCostCode.QboId IS NOT NULL) instead of an ItemSubCostCode
        # mapping-table read -- injectable so tests don't need a live resolver.
        self.resolve_qbo_item_ref = resolve_qbo_item_ref or _default_resolve_qbo_item_ref
        self.qbo_purchase_service = qbo_purchase_service or QboPurchaseService()

    def suggest_for_item(self, item: Any, realm_id: Optional[str] = None) -> dict:
        """Suggest project / sub-cost-code / description for one queue item."""
        memo_text = _item_attr(item, "private_note") or _item_attr(item, "memo_text")
        vendor_id = _item_attr(item, "vendor_id")
        hints = extract_hints(memo_text)

        project_id: Optional[int] = None
        project_confidence: Optional[Decimal] = None
        project_reason: Optional[str] = None
        project_source: Optional[str] = None

        if hints.get("project_hint"):
            project = self.project_service.read_by_abbreviation(hints["project_hint"])
            if project is not None:
                project_id = project.id
                project_confidence = _PROJECT_CODE_CONFIDENCE
                project_reason = format_project_code_reason(hints["project_hint"])
                project_source = SOURCE_PROJECT_CODE

        if project_id is None and hints.get("address_hint"):
            candidates = self.project_service.find_for_invoice(address_hint=hints["address_hint"])
            if candidates:
                top = candidates[0]
                project_id = top["project"]["id"]
                project_confidence = Decimal(str(top["confidence"]))
                project_reason = format_address_match_reason(top["project"]["name"])
                project_source = SOURCE_PROJECT_ADDRESS

        sub_cost_code_id: Optional[int] = None
        scc_confidence: Optional[Decimal] = None
        scc_reason: Optional[str] = None
        scc_source: Optional[str] = None

        if hints.get("scc_hint"):
            candidates = self.sub_cost_code_service.find_for_reply(hint=hints["scc_hint"])
            if candidates:
                top = candidates[0]
                sub_cost_code_id = top["sub_cost_code"]["id"]
                scc_confidence = Decimal(str(top["confidence"]))
                scc_reason = format_scc_shorthand_reason(hints["scc_hint"])
                scc_source = SOURCE_SCC_SHORTHAND

        if sub_cost_code_id is None and vendor_id is not None:
            history = self.suggestion_repo.read_vendor_dominant_sub_cost_code(vendor_id)
            if history is not None:
                sub_cost_code_id = history["sub_cost_code_id"]
                scc_confidence = Decimal(str(history["top_count"])) / Decimal(str(history["total_count"]))
                scc_reason = (
                    f"{PREFIX_VENDOR_CODED_SCC} {history['number']} "
                    f"in {history['top_count']}/{history['total_count']} priors"
                )
                scc_source = SOURCE_VENDOR_HISTORY

        if (
            sub_cost_code_id is not None
            and self.sub_cost_code_service.is_holding_code(sub_cost_code_id) is True
        ):
            sub_cost_code_id = None
            scc_confidence = None
            scc_reason = None
            scc_source = None

        if sub_cost_code_id is not None:
            qbo_item_ref = self.resolve_qbo_item_ref(sub_cost_code_id, realm_id)
            if qbo_item_ref is None:
                sub_cost_code_id = None
                scc_confidence = None
                scc_reason = None
                scc_source = None

        if project_id is None and sub_cost_code_id is None:
            return {
                "status": "flagged",
                "project_id": None,
                "sub_cost_code_id": None,
                "description": hints.get("clean_description"),
                "source": None,
                "reason": (
                    "needs cardholder follow-up: no project or cost-code signal in memo/vendor history"
                ),
                "confidence": None,
                "project_source": project_source,
                "project_confidence": project_confidence,
                "project_reason": project_reason,
                "scc_source": scc_source,
                "scc_confidence": scc_confidence,
                "scc_reason": scc_reason,
            }

        # Only the suggested path reads these — assemble after the flagged return.
        sources = [source for source in (project_source, scc_source) if source]
        reasons = [reason for reason in (project_reason, scc_reason) if reason]
        if project_id is None:
            reasons.append(f"{PREFIX_PROJECT_UNRESOLVED} — needs cardholder")
        if sub_cost_code_id is None:
            reasons.append(f"{PREFIX_SCC_UNRESOLVED} — needs cardholder")

        confidences = [value for value in (project_confidence, scc_confidence) if value is not None]
        overall_confidence = min(confidences) if confidences else None

        return {
            "status": "suggested",
            "project_id": project_id,
            "sub_cost_code_id": sub_cost_code_id,
            "description": hints.get("clean_description"),
            "source": "+".join(sources) if sources else None,
            "reason": "; ".join(reasons),
            "confidence": overall_confidence,
            "project_source": project_source,
            "project_confidence": project_confidence,
            "project_reason": project_reason,
            "scc_source": scc_source,
            "scc_confidence": scc_confidence,
            "scc_reason": scc_reason,
        }

    def persist_result(
        self,
        *,
        public_id: str,
        result: dict,
        sync_token: Optional[str] = None,
        only_from_pending_like: bool = True,
    ):
        """Persist one engine result as suggested or flagged (shared by batch + on-demand)."""
        if result.get("status") == "suggested":
            return self.coding_item_service.record_suggestion(
                public_id=public_id,
                suggested_project_id=result.get("project_id"),
                suggested_sub_cost_code_id=result.get("sub_cost_code_id"),
                suggested_description=result.get("description"),
                suggestion_source=result.get("source"),
                suggestion_reason=result.get("reason"),
                suggestion_confidence=result.get("confidence"),
                sync_token_at_suggest=sync_token,
                status="suggested",
            )
        return self.coding_item_service.record_flag(
            public_id=public_id,
            reason=result.get("reason") or "needs cardholder follow-up",
            only_from_pending_like=only_from_pending_like,
        )

    def suggest_pending(
        self,
        realm_id: Optional[str] = None,
        max_items: int = 200,
    ) -> dict:
        """Process pending coding queue rows and persist suggestions or flags."""
        queue_rows = self.qbo_purchase_service.get_expense_coding_queue(realm_id=realm_id)
        eligible = [
            row
            for row in queue_rows
            if row.get("coding_status") == "pending"
            and row.get("coding_item_public_id") is not None
            and row.get("suggestion_source") is None
        ]

        to_process = eligible[:max_items]
        remaining = max(len(eligible) - len(to_process), 0)
        if remaining > 0:
            logger.info(
                "Expense coding suggestion batch capped at %s; %s pending items remain.",
                max_items,
                remaining,
            )

        suggested_count = 0
        flagged_count = 0

        for row in to_process:
            # vendor_id (dbo keyspace) is projected by the queue read-model via the
            # CodingSuggestion join — no per-row re-fetch needed.
            item = SimpleNamespace(
                public_id=row["coding_item_public_id"],
                private_note=row.get("private_note"),
                vendor_id=row.get("vendor_id"),
                sync_token=row.get("sync_token"),
            )
            result = self.suggest_for_item(item, realm_id=realm_id)

            if result["status"] == "suggested":
                self.persist_result(
                    public_id=item.public_id,
                    result=result,
                    sync_token=item.sync_token,
                    only_from_pending_like=True,
                )
                suggested_count += 1
            else:
                self.persist_result(
                    public_id=item.public_id,
                    result=result,
                    sync_token=item.sync_token,
                    only_from_pending_like=True,
                )
                flagged_count += 1

        return {
            "processed": len(to_process),
            "suggested": suggested_count,
            "flagged": flagged_count,
            "remaining": remaining,
        }


def _item_attr(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)
