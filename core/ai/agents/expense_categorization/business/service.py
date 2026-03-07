# Python Standard Library Imports
import json
import logging
from typing import Optional, List, Dict

# Local Imports
from core.ai.agents.expense_categorization.business.models import (
    CategorizationSuggestion,
    LineSuggestion,
)
from core.ai.agents.expense_categorization.persistence.repo import (
    VendorExpenseHistoryRepository,
)
from entities.vendor.business.service import VendorService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.project.business.service import ProjectService

logger = logging.getLogger(__name__)


class ExpenseCategorizationService:
    """
    AI-powered expense categorization service.
    Combines vendor history patterns with AI analysis to suggest
    SubCostCode and Project for uncategorized expense lines.
    """

    def __init__(self):
        self._history_repo = VendorExpenseHistoryRepository()
        self._vendor_service = VendorService()

    def suggest_for_lines_batch(
        self,
        lines: List[dict],
    ) -> Dict[int, LineSuggestion]:
        """
        Generate categorization suggestions for multiple uncategorized lines.
        Groups by vendor for efficient history queries and batched AI calls.

        Args:
            lines: List of dicts from QboPurchaseRepository.read_lines_needing_update()

        Returns:
            Dict mapping qbo_purchase_line_id -> LineSuggestion
        """
        if not lines:
            return {}

        # Load reference data
        all_vendors = self._vendor_service.read_all()
        all_sub_cost_codes = SubCostCodeService().read_all()
        all_projects = ProjectService().read_all()

        vendor_name_to_id = {}
        for v in all_vendors:
            if v.name:
                vendor_name_to_id[v.name.strip().lower()] = v.id

        # Group lines by vendor name
        vendor_groups: Dict[str, List[dict]] = {}
        for line in lines:
            vendor_name = (line.get("entity_ref_name") or "").strip()
            vendor_groups.setdefault(vendor_name, []).append(line)

        results: Dict[int, LineSuggestion] = {}

        for vendor_name, vendor_lines in vendor_groups.items():
            # Resolve vendor_id
            vendor_id = vendor_name_to_id.get(vendor_name.lower())

            # Get vendor history
            history = []
            if vendor_id:
                try:
                    history = self._history_repo.read_vendor_expense_history(
                        vendor_id=vendor_id, limit=20
                    )
                except Exception as e:
                    logger.warning("Failed to get vendor history for %s: %s", vendor_name, e)

            # Generate suggestions for each line in this vendor group
            for line in vendor_lines:
                line_id = line.get("qbo_purchase_line_id", 0)
                description = line.get("line_description") or ""
                amount = line.get("line_amount")

                suggestions = self._build_suggestions(
                    vendor_name=vendor_name,
                    vendor_id=vendor_id,
                    description=description,
                    amount=amount,
                    history=history,
                    sub_cost_codes=all_sub_cost_codes,
                    projects=all_projects,
                )

                results[line_id] = LineSuggestion(
                    qbo_purchase_line_id=line_id,
                    suggestions=suggestions,
                    vendor_name=vendor_name,
                    description=description,
                    amount=amount,
                )

        # If there are lines needing AI analysis, run batch AI suggestion
        lines_needing_ai = [
            (line_id, ls)
            for line_id, ls in results.items()
            if not ls.suggestions or ls.suggestions[0].confidence < 0.80
        ]

        if lines_needing_ai:
            try:
                ai_suggestions = self._get_ai_suggestions_batch(
                    line_suggestions=lines_needing_ai,
                    sub_cost_codes=all_sub_cost_codes,
                    projects=all_projects,
                    vendor_histories={
                        vn: self._history_repo.read_vendor_expense_history(
                            vendor_id=vendor_name_to_id.get(vn.lower()), limit=10
                        )
                        if vendor_name_to_id.get(vn.lower())
                        else []
                        for vn in {
                            ls.vendor_name or ""
                            for _, ls in lines_needing_ai
                        }
                    },
                )
                # Merge AI suggestions with existing
                for line_id, ai_sugs in ai_suggestions.items():
                    if line_id in results:
                        existing = results[line_id].suggestions
                        # Add AI suggestions that are better than existing
                        for ai_sug in ai_sugs:
                            if not existing or ai_sug.confidence > existing[0].confidence:
                                results[line_id].suggestions = [ai_sug] + existing
                            else:
                                results[line_id].suggestions.append(ai_sug)
            except Exception as e:
                logger.warning("AI suggestion batch failed: %s", e)

        return results

    def _build_suggestions(
        self,
        vendor_name: str,
        vendor_id: Optional[int],
        description: str,
        amount: Optional[float],
        history: List[dict],
        sub_cost_codes: list,
        projects: list,
    ) -> List[CategorizationSuggestion]:
        """Build suggestions from vendor history patterns."""
        suggestions = []

        if not history:
            return suggestions

        total_usage = sum(h.get("usage_count", 0) for h in history)

        for entry in history[:5]:  # Top 5 history entries
            usage_count = entry.get("usage_count", 0)
            frequency_ratio = usage_count / total_usage if total_usage > 0 else 0

            # Confidence based on frequency and description match
            confidence = min(0.50 + (frequency_ratio * 0.40), 0.90)

            # Boost confidence if description matches historical descriptions
            sample_descriptions = entry.get("sample_descriptions", [])
            if description and sample_descriptions:
                desc_lower = description.lower()
                for sample in sample_descriptions:
                    if sample and desc_lower in sample.lower() or sample.lower() in desc_lower:
                        confidence = min(confidence + 0.10, 0.95)
                        break

            suggestions.append(CategorizationSuggestion(
                sub_cost_code_id=entry.get("sub_cost_code_id"),
                sub_cost_code_number=entry.get("sub_cost_code_number"),
                sub_cost_code_name=entry.get("sub_cost_code_name"),
                project_id=entry.get("project_id"),
                project_name=entry.get("project_name"),
                project_abbreviation=entry.get("project_abbreviation"),
                confidence=round(confidence, 2),
                source="vendor_history",
                reasoning=f"Used {usage_count} time(s) for this vendor",
            ))

        # Sort by confidence descending
        suggestions.sort(key=lambda s: s.confidence, reverse=True)
        return suggestions

    def _get_ai_suggestions_batch(
        self,
        line_suggestions: List[tuple],
        sub_cost_codes: list,
        projects: list,
        vendor_histories: Dict[str, List[dict]],
    ) -> Dict[int, List[CategorizationSuggestion]]:
        """
        Use AI to analyze descriptions and suggest categorizations.
        Batches all lines into a single AI call for efficiency.
        """
        try:
            from integrations.azure.ai.openai_client import AzureOpenAIClient
        except ImportError:
            logger.warning("AzureOpenAIClient not available for AI suggestions")
            return {}

        # Build reference data for prompt
        scc_list = "\n".join(
            f"- {scc.number}: {scc.name}"
            for scc in sub_cost_codes
            if scc.number and scc.name
        )

        project_list = "\n".join(
            f"- {p.abbreviation or p.name}: {p.name}"
            for p in projects
            if p.name
        )

        # Build lines for analysis
        lines_for_prompt = []
        for line_id, ls in line_suggestions:
            vendor_name = ls.vendor_name or "Unknown"
            history = vendor_histories.get(vendor_name, [])
            history_text = ""
            if history:
                history_items = [
                    f"  - {h.get('sub_cost_code_number', '?')} ({h.get('sub_cost_code_name', '?')})"
                    f" / Project: {h.get('project_abbreviation') or h.get('project_name') or 'None'}"
                    f" — used {h.get('usage_count', 0)} times"
                    for h in history[:5]
                ]
                history_text = "\n".join(history_items)

            lines_for_prompt.append({
                "line_id": line_id,
                "vendor": vendor_name,
                "description": ls.description or "",
                "amount": ls.amount,
                "history": history_text,
            })

        if not lines_for_prompt:
            return {}

        system_prompt = f"""You are a construction expense categorization assistant. Given credit card or bank account expense transactions, suggest the most appropriate SubCostCode and Project for each.

Available SubCostCodes (cost categories):
{scc_list}

Available Projects (construction jobs):
{project_list}

Rules:
- Match based on the vendor name, description, and amount
- Use the vendor's past history as the strongest signal
- If no history exists, infer from the description and vendor name
- Return your best guess even if uncertain, with appropriate confidence
- Confidence should be 0.0-1.0 where 1.0 = certain"""

        lines_text = ""
        for i, line in enumerate(lines_for_prompt):
            lines_text += f"\nTransaction {line['line_id']}:\n"
            lines_text += f"  Vendor: {line['vendor']}\n"
            lines_text += f"  Description: {line['description']}\n"
            lines_text += f"  Amount: ${line['amount']:.2f}\n" if line["amount"] else ""
            if line["history"]:
                lines_text += f"  Vendor History:\n{line['history']}\n"

        user_prompt = f"""Categorize these transactions. Return a JSON object where each key is the line_id and the value has sub_cost_code_number, project_abbreviation (or null), confidence, and reasoning.

{lines_text}

Return valid JSON only, like:
{{
  "123": {{"sub_cost_code_number": "18.01", "project_abbreviation": "HP", "confidence": 0.85, "reasoning": "..."}},
  "456": {{"sub_cost_code_number": "55.00", "project_abbreviation": null, "confidence": 0.60, "reasoning": "..."}}
}}"""

        try:
            client = AzureOpenAIClient()
            response = client.chat_completion_with_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )

            if not response:
                return {}

            # Build lookup maps
            scc_by_number = {
                scc.number: scc for scc in sub_cost_codes if scc.number
            }
            project_by_abbrev = {
                p.abbreviation.lower(): p
                for p in projects
                if p.abbreviation
            }
            project_by_name = {
                p.name.lower(): p for p in projects if p.name
            }

            results: Dict[int, List[CategorizationSuggestion]] = {}

            for line_id_str, suggestion_data in response.items():
                try:
                    line_id = int(line_id_str)
                except (ValueError, TypeError):
                    continue

                scc_number = suggestion_data.get("sub_cost_code_number")
                proj_abbrev = suggestion_data.get("project_abbreviation")
                confidence = float(suggestion_data.get("confidence", 0.5))
                reasoning = suggestion_data.get("reasoning", "")

                # Resolve SubCostCode
                scc = scc_by_number.get(scc_number)

                # Resolve Project
                proj = None
                if proj_abbrev:
                    proj = project_by_abbrev.get(proj_abbrev.lower())
                    if not proj:
                        proj = project_by_name.get(proj_abbrev.lower())

                results[line_id] = [CategorizationSuggestion(
                    sub_cost_code_id=scc.id if scc else None,
                    sub_cost_code_number=scc.number if scc else scc_number,
                    sub_cost_code_name=scc.name if scc else None,
                    project_id=proj.id if proj else None,
                    project_name=proj.name if proj else None,
                    project_abbreviation=proj.abbreviation if proj else proj_abbrev,
                    confidence=round(confidence, 2),
                    source="ai",
                    reasoning=reasoning,
                )]

            return results

        except Exception as e:
            logger.warning("AI categorization call failed: %s", e)
            return {}

    def apply_batch(
        self,
        categorizations: List[dict],
        realm_id: str,
        push_to_qbo: bool = True,
    ) -> dict:
        """
        Apply categorization to multiple uncategorized QBO purchase lines.

        For each line:
        1. Sync QBO purchase to expense (create/update)
        2. Update expense line item with SubCostCode + Project
        3. Optionally push corrections back to QBO

        Args:
            categorizations: List of dicts with keys:
                - qbo_purchase_id (int)
                - qbo_purchase_line_id (int)
                - sub_cost_code_id (int)
                - project_public_id (str, optional)
            realm_id: QBO realm ID for push-back
            push_to_qbo: Whether to push corrections to QBO

        Returns:
            Dict with applied_count, errors, expense_public_ids, qbo_push_results
        """
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
        from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
        from entities.expense_line_item.business.service import ExpenseLineItemService

        qbo_service = QboPurchaseService()
        connector = PurchaseExpenseConnector()
        eli_service = ExpenseLineItemService()

        applied_count = 0
        errors = []
        expense_public_ids = []
        qbo_push_results = []

        # Group categorizations by purchase ID to batch process
        purchase_lines_map: Dict[int, List[dict]] = {}
        for cat in categorizations:
            purchase_id = cat.get("qbo_purchase_id")
            if not purchase_id:
                errors.append({
                    "line_id": cat.get("qbo_purchase_line_id"),
                    "error": "Missing qbo_purchase_id",
                })
                continue
            purchase_lines_map.setdefault(purchase_id, []).append(cat)

        for purchase_id, cat_lines in purchase_lines_map.items():
            try:
                # Get the full purchase
                purchase = qbo_service.read_by_id(purchase_id)
                if not purchase:
                    for cl in cat_lines:
                        errors.append({
                            "line_id": cl.get("qbo_purchase_line_id"),
                            "error": "Purchase not found",
                        })
                    continue

                # Get all lines for this purchase
                all_purchase_lines = qbo_service.read_lines_by_qbo_purchase_id(purchase_id)

                # Sync purchase to expense
                expense = connector.sync_from_qbo_purchase(
                    qbo_purchase=purchase,
                    qbo_purchase_lines=all_purchase_lines,
                )

                if not expense or not expense.public_id:
                    for cl in cat_lines:
                        errors.append({
                            "line_id": cl.get("qbo_purchase_line_id"),
                            "error": "Failed to sync purchase to expense",
                        })
                    continue

                if expense.public_id not in expense_public_ids:
                    expense_public_ids.append(expense.public_id)

                # Update each expense line item with categorization
                for cl in cat_lines:
                    line_id = cl.get("qbo_purchase_line_id")
                    try:
                        # Find the purchase line object
                        purchase_line = next(
                            (pl for pl in all_purchase_lines if pl.id == line_id),
                            None,
                        )
                        if not purchase_line:
                            errors.append({"line_id": line_id, "error": "Purchase line not found"})
                            continue

                        # Find the expense line item linked to this purchase line
                        from integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo import (
                            PurchaseLineExpenseLineItemRepository,
                        )
                        pleli_repo = PurchaseLineExpenseLineItemRepository()
                        mapping = pleli_repo.read_by_qbo_purchase_line_id(line_id)

                        if not mapping:
                            errors.append({"line_id": line_id, "error": "No expense line item mapping found"})
                            continue

                        # Get the expense line item
                        eli = eli_service.read_by_id(mapping.expense_line_item_id)
                        if not eli or not eli.public_id:
                            errors.append({"line_id": line_id, "error": "Expense line item not found"})
                            continue

                        # Update with categorization
                        eli_service.update_by_public_id(
                            public_id=eli.public_id,
                            row_version=eli.row_version,
                            sub_cost_code_id=cl.get("sub_cost_code_id"),
                            project_public_id=cl.get("project_public_id"),
                        )
                        applied_count += 1

                    except Exception as e:
                        errors.append({"line_id": line_id, "error": str(e)})

                # Push back to QBO if requested
                if push_to_qbo:
                    try:
                        from entities.expense.business.service import ExpenseService
                        fresh_expense = ExpenseService().read_by_public_id(expense.public_id)
                        connector.sync_to_qbo_purchase(
                            expense=fresh_expense,
                            realm_id=realm_id,
                        )
                        qbo_push_results.append({
                            "expense_public_id": expense.public_id,
                            "success": True,
                        })
                    except Exception as e:
                        logger.warning(
                            "QBO push-back failed for expense %s: %s",
                            expense.public_id, e,
                        )
                        qbo_push_results.append({
                            "expense_public_id": expense.public_id,
                            "success": False,
                            "error": str(e),
                        })

            except Exception as e:
                logger.error("Error processing purchase %d: %s", purchase_id, e)
                for cl in cat_lines:
                    errors.append({
                        "line_id": cl.get("qbo_purchase_line_id"),
                        "error": str(e),
                    })

        return {
            "applied_count": applied_count,
            "errors": errors,
            "expense_public_ids": expense_public_ids,
            "qbo_push_results": qbo_push_results,
        }
