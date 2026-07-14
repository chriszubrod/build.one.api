# Python Standard Library Imports
import logging
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

# Local Imports
from entities.expense_coding_item.business.hint_extractor import extract_hints
from entities.expense_coding_item.business.service import ExpenseCodingItemService
from entities.expense_coding_item.persistence.repo import ExpenseCodingItemRepository
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService

logger = logging.getLogger(__name__)

_PROJECT_CODE_CONFIDENCE = Decimal("0.95")


class ExpenseCodingSuggestionService:
    """Deterministic suggestion engine for expense coding queue items (Phase B)."""

    def __init__(
        self,
        *,
        project_service: Optional[ProjectService] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
        coding_item_service: Optional[ExpenseCodingItemService] = None,
        suggestion_repo: Optional[ExpenseCodingItemRepository] = None,
        item_sub_cost_code_repo: Optional[ItemSubCostCodeRepository] = None,
        qbo_purchase_service: Optional[QboPurchaseService] = None,
    ):
        self.project_service = project_service or ProjectService()
        self.sub_cost_code_service = sub_cost_code_service or SubCostCodeService()
        self.coding_item_service = coding_item_service or ExpenseCodingItemService()
        self.suggestion_repo = suggestion_repo or ExpenseCodingItemRepository()
        self.item_sub_cost_code_repo = item_sub_cost_code_repo or ItemSubCostCodeRepository()
        self.qbo_purchase_service = qbo_purchase_service or QboPurchaseService()

    def suggest_for_item(self, item: Any) -> dict:
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
                project_reason = f"matched project code {hints['project_hint']}"
                project_source = "code"

        if project_id is None and hints.get("address_hint"):
            candidates = self.project_service.find_for_invoice(address_hint=hints["address_hint"])
            if candidates:
                top = candidates[0]
                project_id = top["project"]["id"]
                project_confidence = Decimal(str(top["confidence"]))
                project_reason = f"address match {top['project']['name']}"
                project_source = "address"

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
                scc_reason = f"cost-code shorthand {hints['scc_hint']}"
                scc_source = "scc_shorthand"

        if sub_cost_code_id is None and vendor_id is not None:
            history = self.suggestion_repo.read_vendor_dominant_sub_cost_code(vendor_id)
            if history is not None:
                sub_cost_code_id = history["sub_cost_code_id"]
                scc_confidence = Decimal(str(history["top_count"])) / Decimal(str(history["total_count"]))
                scc_reason = (
                    f"vendor coded to SCC {history['number']} "
                    f"in {history['top_count']}/{history['total_count']} priors"
                )
                scc_source = "vendor_history"

        if sub_cost_code_id is not None:
            mapping = self.item_sub_cost_code_repo.read_by_sub_cost_code_id(sub_cost_code_id)
            if mapping is None:
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
            }

        # Only the suggested path reads these — assemble after the flagged return.
        sources = [source for source in (project_source, scc_source) if source]
        reasons = [reason for reason in (project_reason, scc_reason) if reason]
        if project_id is None:
            reasons.append("project unresolved — needs cardholder")
        if sub_cost_code_id is None:
            reasons.append("sub-cost-code unresolved — needs cardholder")

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
        }

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
            # ExpenseCodingItem join — no per-row re-fetch needed.
            item = SimpleNamespace(
                public_id=row["coding_item_public_id"],
                private_note=row.get("private_note"),
                vendor_id=row.get("vendor_id"),
                sync_token=row.get("sync_token"),
            )
            result = self.suggest_for_item(item)

            if result["status"] == "suggested":
                self.coding_item_service.record_suggestion(
                    public_id=item.public_id,
                    suggested_project_id=result["project_id"],
                    suggested_sub_cost_code_id=result["sub_cost_code_id"],
                    suggested_description=result["description"],
                    suggestion_source=result["source"],
                    suggestion_reason=result["reason"],
                    suggestion_confidence=result["confidence"],
                    sync_token_at_suggest=item.sync_token,
                    status="suggested",
                )
                suggested_count += 1
            else:
                self.coding_item_service.record_flag(
                    public_id=item.public_id,
                    reason=result["reason"],
                    only_from_pending_like=True,  # batch: never clobber a human-advanced row
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
