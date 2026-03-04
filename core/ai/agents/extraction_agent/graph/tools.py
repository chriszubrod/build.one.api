"""
Extraction Agent Tools
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def validate_extraction(
    vendor_name: Optional[str] = None,
    bill_number: Optional[str] = None,
    bill_date: Optional[str] = None,
    due_date: Optional[str] = None,
    total_amount: Optional[float] = None,
    line_items: Optional[list] = None,
) -> dict:
    """Validate extracted fields for internal consistency.

    Checks that dates are valid, bill_number has digits, amounts are reasonable,
    and line items sum to approximately the total. Returns a list of issues found.
    """
    issues = []

    # Check bill_number has digits
    if bill_number and not re.search(r'\d', str(bill_number)):
        issues.append("bill_number has no digits — likely not a valid invoice number")

    # Check dates are valid ISO format
    for field_name, date_val in [("bill_date", bill_date), ("due_date", due_date)]:
        if date_val:
            try:
                dt = datetime.strptime(date_val, "%Y-%m-%d")
                if dt.year < 2000 or dt.year > 2030:
                    issues.append(f"{field_name} year {dt.year} seems unlikely")
            except ValueError:
                issues.append(f"{field_name} '{date_val}' is not valid YYYY-MM-DD format")

    # Check total_amount is reasonable
    if total_amount is not None:
        if total_amount == 0:
            issues.append("total_amount is zero — verify this is correct")
        elif abs(total_amount) > 10_000_000:
            issues.append(f"total_amount {total_amount} is unusually large — verify")

    # Check line items sum vs total
    if line_items and total_amount:
        line_sum = 0
        for li in line_items:
            amt = li.get("amount")
            if amt is not None:
                try:
                    line_sum += float(amt)
                except (ValueError, TypeError):
                    pass
        if line_sum > 0 and abs(line_sum - total_amount) > 0.02 * abs(total_amount):
            issues.append(
                f"Line items sum ({line_sum:.2f}) differs from total ({total_amount:.2f}) "
                f"by more than 2% — check for missing items or tax"
            )

    # Check vendor_name
    if not vendor_name:
        issues.append("vendor_name is missing — critical field")

    if not issues:
        return {"valid": True, "issues": []}
    return {"valid": False, "issues": issues}


@tool
def lookup_vendor(vendor_name: str) -> dict:
    """Fuzzy-match a vendor name against the database.

    Returns matching vendors with confidence scores. Use this to verify
    the extracted vendor exists in the system.
    """
    try:
        from entities.vendor.business.service import VendorService
        svc = VendorService()

        # Try exact match first
        exact = svc.read_by_name(vendor_name)
        if exact:
            return {
                "found": True,
                "match_type": "exact",
                "vendor": {"public_id": exact.public_id, "name": exact.name},
                "confidence": 0.99,
            }

        # Fuzzy match
        all_vendors = svc.read_all()
        from difflib import SequenceMatcher
        matches = []
        query_lower = vendor_name.lower()
        for v in all_vendors:
            score = SequenceMatcher(None, query_lower, v.name.lower()).ratio()
            if score >= 0.5:
                matches.append({"public_id": v.public_id, "name": v.name, "score": round(score, 3)})
        matches.sort(key=lambda m: m["score"], reverse=True)

        if matches:
            return {"found": True, "match_type": "fuzzy", "matches": matches[:5]}
        return {"found": False, "message": f"No vendor match for '{vendor_name}'"}
    except Exception as e:
        logger.warning("Vendor lookup failed: %s", e)
        return {"found": False, "error": str(e)}


@tool
def lookup_project(query: str) -> dict:
    """Match a project by name, abbreviation, or address.

    Use this when you have a ship-to address, job site reference, or project
    name from the document.
    """
    try:
        from entities.project.business.service import ProjectService
        from difflib import SequenceMatcher
        svc = ProjectService()
        all_projects = svc.read_all()

        query_lower = query.lower()
        matches = []
        for p in all_projects:
            # Check name, abbreviation, and address
            candidates = [p.name.lower()]
            if getattr(p, "abbreviation", None):
                candidates.append(p.abbreviation.lower())

            best_score = max(
                SequenceMatcher(None, query_lower, c).ratio()
                for c in candidates
            )
            if best_score >= 0.4:
                matches.append({
                    "public_id": p.public_id,
                    "name": p.name,
                    "abbreviation": getattr(p, "abbreviation", None),
                    "score": round(best_score, 3),
                })
        matches.sort(key=lambda m: m["score"], reverse=True)

        if matches:
            return {"found": True, "matches": matches[:5]}
        return {"found": False, "message": f"No project match for '{query}'"}
    except Exception as e:
        logger.warning("Project lookup failed: %s", e)
        return {"found": False, "error": str(e)}


@tool
def lookup_payment_term(terms_string: str) -> dict:
    """Resolve a payment terms string (e.g., 'Net 30') to a database record."""
    try:
        from entities.payment_term.business.service import PaymentTermService
        from difflib import SequenceMatcher
        svc = PaymentTermService()

        exact = svc.read_by_name(terms_string)
        if exact:
            return {"found": True, "public_id": exact.public_id, "name": exact.name}

        all_terms = svc.read_all()
        matches = []
        query_lower = terms_string.lower()
        for t in all_terms:
            score = SequenceMatcher(None, query_lower, t.name.lower()).ratio()
            if score >= 0.5:
                matches.append({"public_id": t.public_id, "name": t.name, "score": round(score, 3)})
        matches.sort(key=lambda m: m["score"], reverse=True)

        if matches:
            return {"found": True, "matches": matches[:3]}
        return {"found": False}
    except Exception as e:
        return {"found": False, "error": str(e)}


@tool
def lookup_sub_cost_code(description: str) -> dict:
    """Match a cost code from a description of goods/services."""
    try:
        from entities.sub_cost_code.business.service import SubCostCodeService
        from difflib import SequenceMatcher
        svc = SubCostCodeService()
        all_sccs = svc.read_all()

        query_lower = description.lower()
        matches = []
        for scc in all_sccs:
            if not scc.name:
                continue
            label = f"{scc.number} {scc.name}" if scc.number else scc.name
            score = SequenceMatcher(None, query_lower, label.lower()).ratio()
            if score >= 0.3:
                matches.append({"id": scc.id, "name": scc.name, "number": scc.number, "score": round(score, 3)})
        matches.sort(key=lambda m: m["score"], reverse=True)

        if matches:
            return {"found": True, "matches": matches[:5]}
        return {"found": False}
    except Exception as e:
        return {"found": False, "error": str(e)}


@tool
def check_duplicate_bill(vendor_name: str, bill_number: str) -> dict:
    """Check if a bill with this number already exists for this vendor."""
    try:
        from entities.bill.business.service import BillService
        from entities.vendor.business.service import VendorService
        svc = BillService()
        vendor_svc = VendorService()

        vendor = vendor_svc.read_by_name(vendor_name)
        if not vendor:
            return {"is_duplicate": False, "message": "Vendor not found in DB — cannot check duplicates"}

        existing = svc.read_by_bill_number_and_vendor_public_id(
            bill_number=bill_number,
            vendor_public_id=vendor.public_id,
        )
        if existing:
            return {
                "is_duplicate": True,
                "existing_bill": {
                    "public_id": existing.public_id,
                    "bill_date": str(existing.bill_date) if existing.bill_date else None,
                    "total_amount": str(existing.total_amount) if existing.total_amount else None,
                },
            }
        return {"is_duplicate": False}
    except Exception as e:
        return {"is_duplicate": False, "error": str(e)}


@tool
def finalize_extraction(
    vendor_name: Optional[str] = None,
    bill_number: Optional[str] = None,
    bill_date: Optional[str] = None,
    due_date: Optional[str] = None,
    total_amount: Optional[float] = None,
    payment_terms: Optional[str] = None,
    ship_to_address: Optional[str] = None,
    memo: Optional[str] = None,
    project_name: Optional[str] = None,
    sub_cost_code_name: Optional[str] = None,
    is_billable: bool = True,
    line_items: Optional[list] = None,
    confidence: float = 0.0,
    reasoning: str = "",
) -> dict:
    """Submit the final extraction result.

    Call this after validation passes. Include all extracted fields and
    your overall confidence score.
    """
    return {
        "success": True,
        "extraction": {
            "vendor_name": vendor_name,
            "bill_number": bill_number,
            "bill_date": bill_date,
            "due_date": due_date,
            "total_amount": total_amount,
            "payment_terms": payment_terms,
            "ship_to_address": ship_to_address,
            "memo": memo,
            "project_name": project_name,
            "sub_cost_code_name": sub_cost_code_name,
            "is_billable": is_billable,
            "line_items": line_items or [],
        },
        "confidence": confidence,
        "reasoning": reasoning,
    }


# All tools for this agent
EXTRACTION_AGENT_TOOLS = [
    validate_extraction,
    lookup_vendor,
    lookup_project,
    lookup_payment_term,
    lookup_sub_cost_code,
    check_duplicate_bill,
    finalize_extraction,
]

TOOLS_BY_NAME = {t.name: t for t in EXTRACTION_AGENT_TOOLS}
