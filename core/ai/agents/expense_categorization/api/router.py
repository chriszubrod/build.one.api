# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, BackgroundTasks, Depends

# Local Imports
from core.ai.agents.expense_categorization.api.schemas import (
    SuggestBatchRequest,
    SuggestBatchResponse,
    LineSuggestionItem,
    SuggestionItem,
    ApplyBatchRequest,
    ApplyBatchResponse,
    MatchReceiptsRequest,
    MatchReceiptsResponse,
    LinkPurchaseRequest,
    LinkPurchaseResponse,
)
from entities.auth.business.service import get_current_user_api

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/expense-categorization",
    tags=["api", "expense-categorization"],
)


@router.post("/suggest-batch", response_model=SuggestBatchResponse)
def suggest_batch(
    request: SuggestBatchRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get AI-powered categorization suggestions for all uncategorized QBO purchase lines.
    Returns SubCostCode + Project suggestions with confidence scores.
    """
    try:
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
        from core.ai.agents.expense_categorization.business.service import ExpenseCategorizationService

        # Get uncategorized lines
        qbo_service = QboPurchaseService()
        lines = qbo_service.get_lines_needing_update(realm_id=request.realm_id)

        if not lines:
            return SuggestBatchResponse(
                status_code=200,
                message="No uncategorized lines found",
                suggestions={},
            )

        # Get suggestions
        cat_service = ExpenseCategorizationService()
        results = cat_service.suggest_for_lines_batch(lines=lines)

        # Convert to response format
        suggestions = {}
        for line_id, line_sug in results.items():
            suggestions[str(line_id)] = LineSuggestionItem(
                qbo_purchase_line_id=line_sug.qbo_purchase_line_id,
                vendor_name=line_sug.vendor_name,
                description=line_sug.description,
                amount=line_sug.amount,
                suggestions=[
                    SuggestionItem(
                        sub_cost_code_id=s.sub_cost_code_id,
                        sub_cost_code_number=s.sub_cost_code_number,
                        sub_cost_code_name=s.sub_cost_code_name,
                        project_id=s.project_id,
                        project_name=s.project_name,
                        project_abbreviation=s.project_abbreviation,
                        confidence=s.confidence,
                        source=s.source,
                        reasoning=s.reasoning,
                    )
                    for s in line_sug.suggestions
                ],
            )

        return SuggestBatchResponse(
            status_code=200,
            message=f"Generated suggestions for {len(suggestions)} lines",
            suggestions=suggestions,
        )

    except Exception as e:
        logger.error("Error generating categorization suggestions: %s", e)
        return SuggestBatchResponse(
            status_code=500,
            message=f"Error: {str(e)}",
            suggestions={},
        )


@router.post("/apply-batch", response_model=ApplyBatchResponse)
def apply_batch(
    request: ApplyBatchRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Apply categorization (SubCostCode + Project) to multiple uncategorized lines.
    Optionally pushes corrections back to QBO.
    """
    try:
        from core.ai.agents.expense_categorization.business.service import ExpenseCategorizationService

        cat_service = ExpenseCategorizationService()
        result = cat_service.apply_batch(
            categorizations=[c.model_dump() for c in request.categorizations],
            realm_id=request.realm_id,
            push_to_qbo=request.push_to_qbo,
        )

        applied = result.get("applied_count", 0)
        errors = result.get("errors", [])
        status = 200 if not errors else 207

        return ApplyBatchResponse(
            status_code=status,
            message=f"Applied {applied} categorizations"
            + (f" with {len(errors)} errors" if errors else ""),
            applied_count=applied,
            errors=errors,
            expense_public_ids=result.get("expense_public_ids", []),
            qbo_push_results=result.get("qbo_push_results", []),
        )

    except Exception as e:
        logger.error("Error applying batch categorization: %s", e)
        return ApplyBatchResponse(
            status_code=500,
            message=f"Error: {str(e)}",
        )


@router.post("/match-receipts", response_model=MatchReceiptsResponse)
def match_receipts(
    request: MatchReceiptsRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Match receipts (QBO Attachables + inbox emails) to uncategorized expense lines.
    """
    try:
        from core.ai.agents.expense_categorization.business.receipt_matcher import ReceiptMatcherService
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService

        # Get uncategorized lines
        qbo_service = QboPurchaseService()
        lines = qbo_service.get_lines_needing_update(realm_id=request.realm_id)

        matcher = ReceiptMatcherService()
        all_matches = []

        # Match QBO Attachables (high confidence — receipt already linked in QBO)
        try:
            attachable_matches = matcher.match_qbo_attachables_to_uncategorized(
                lines=lines,
                realm_id=request.realm_id,
            )
            all_matches.extend(attachable_matches)
        except Exception as e:
            logger.warning("QBO attachable matching failed: %s", e)

        # Match inbox emails (fuzzy matching by vendor, amount, date)
        try:
            # Only match lines that don't already have an attachable match
            matched_line_ids = {m["qbo_purchase_line_id"] for m in all_matches}
            unmatched_lines = [
                l for l in lines
                if l.get("qbo_purchase_line_id") not in matched_line_ids
            ]
            inbox_matches = matcher.match_inbox_to_uncategorized(unmatched_lines)
            all_matches.extend(inbox_matches)
        except Exception as e:
            logger.warning("Inbox email matching failed: %s", e)

        from core.ai.agents.expense_categorization.api.schemas import ReceiptMatchItem
        match_items = [
            ReceiptMatchItem(**m) for m in all_matches
        ]

        return MatchReceiptsResponse(
            status_code=200,
            message=f"Found {len(match_items)} receipt matches",
            matches=match_items,
        )

    except Exception as e:
        logger.error("Error matching receipts: %s", e)
        return MatchReceiptsResponse(
            status_code=500,
            message=f"Error: {str(e)}",
        )


@router.post("/link-purchase", response_model=LinkPurchaseResponse)
def link_purchase(
    request: LinkPurchaseRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Link an inbox-created draft expense to an uncategorized QBO purchase.
    Creates mappings, syncs line items, and pushes to QBO.
    """
    try:
        from entities.expense.business.service import ExpenseService
        from entities.expense_line_item.business.service import ExpenseLineItemService
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
        from integrations.intuit.qbo.purchase.connector.expense.business.service import (
            PurchaseExpenseConnector,
        )
        from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
            PurchaseLineExpenseLineItemConnector,
        )

        expense_service = ExpenseService()
        eli_service = ExpenseLineItemService()
        qbo_service = QboPurchaseService()
        pe_connector = PurchaseExpenseConnector()
        pleli_connector = PurchaseLineExpenseLineItemConnector()

        # 1. Validate expense exists
        expense = expense_service.read_by_public_id(request.expense_public_id)
        if not expense:
            return LinkPurchaseResponse(
                status_code=404,
                message="Expense not found",
            )

        expense_id = int(expense.id)

        # 2. Check no existing mapping
        existing = pe_connector.get_mapping_by_expense_id(expense_id)
        if existing:
            return LinkPurchaseResponse(
                status_code=409,
                message="Expense is already linked to a QBO purchase",
                expense_public_id=expense.public_id,
                has_qbo_purchase_mapping=True,
            )

        # 3. Validate QBO purchase exists
        qbo_purchase = qbo_service.read_by_id(request.qbo_purchase_id)
        if not qbo_purchase:
            return LinkPurchaseResponse(
                status_code=404,
                message="QBO purchase not found",
            )

        # 4. Create PurchaseExpense mapping
        pe_connector.create_mapping(
            expense_id=expense_id,
            qbo_purchase_id=request.qbo_purchase_id,
        )

        # 5. Create line-level mappings (positional 1:1)
        qbo_lines = qbo_service.read_lines_by_qbo_purchase_id(request.qbo_purchase_id)
        expense_line_items = eli_service.read_by_expense_id(expense_id=expense_id)

        for idx, qbo_line in enumerate(qbo_lines):
            if idx < len(expense_line_items):
                eli = expense_line_items[idx]
                try:
                    pleli_connector.create_mapping(
                        expense_line_item_id=int(eli.id),
                        qbo_purchase_line_id=qbo_line.id,
                    )
                except ValueError as e:
                    logger.warning("Line mapping skipped: %s", e)

        # 6. Mark expense as non-draft
        expense_service.update_by_public_id(
            expense.public_id,
            row_version=expense.row_version,
            is_draft=False,
        )

        # 7. Push back to QBO (AccountBased → ItemBased)
        qbo_push_result = None
        try:
            fresh_expense = expense_service.read_by_public_id(expense.public_id)
            pe_connector.sync_to_qbo_purchase(
                expense=fresh_expense,
                realm_id=request.realm_id,
            )
            qbo_push_result = {"success": True}
        except Exception as e:
            logger.warning(
                "QBO push-back failed for expense %s: %s",
                expense.public_id, e,
            )
            qbo_push_result = {"success": False, "error": str(e)}

        return LinkPurchaseResponse(
            status_code=200,
            message="Successfully linked expense to QBO purchase",
            expense_public_id=expense.public_id,
            has_qbo_purchase_mapping=True,
            qbo_push_result=qbo_push_result,
        )

    except Exception as e:
        logger.error("Error linking purchase: %s", e)
        return LinkPurchaseResponse(
            status_code=500,
            message=f"Error: {str(e)}",
        )
