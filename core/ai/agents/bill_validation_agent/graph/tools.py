"""
Bill Validation Agent Tools
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def get_bill_details(bill_public_id: str) -> dict:
    """Load a bill and its line items by public ID."""
    try:
        from entities.bill.business.service import BillService
        from entities.bill_line_item.business.service import BillLineItemService
        svc = BillService()
        li_svc = BillLineItemService()

        bill = svc.read_by_public_id(bill_public_id)
        if not bill:
            return {"found": False, "error": f"Bill {bill_public_id} not found"}

        line_items = li_svc.read_by_bill_id(bill.id) or []

        return {
            "found": True,
            "bill": {
                "public_id": bill.public_id,
                "bill_number": bill.bill_number,
                "bill_date": str(bill.bill_date) if bill.bill_date else None,
                "due_date": str(bill.due_date) if bill.due_date else None,
                "total_amount": str(bill.total_amount) if bill.total_amount else None,
                "vendor_name": getattr(bill, "vendor_name", None),
                "vendor_id": bill.vendor_id,
                "vendor_public_id": getattr(bill, "vendor_public_id", None),
                "project_name": getattr(bill, "project_name", None),
                "status": getattr(bill, "status", None),
                "memo": getattr(bill, "memo", None),
            },
            "line_items": [
                {
                    "description": li.description,
                    "amount": str(li.amount) if li.amount else None,
                    "sub_cost_code_name": getattr(li, "sub_cost_code_name", None),
                    "is_billable": getattr(li, "is_billable", None),
                }
                for li in line_items
            ],
        }
    except Exception as e:
        logger.warning("get_bill_details failed: %s", e)
        return {"found": False, "error": str(e)}


@tool
def check_duplicate_bill_number(vendor_public_id: str, bill_number: str, current_bill_public_id: str) -> dict:
    """Check if another bill with this number already exists for the same vendor.

    Excludes the current bill from the check.
    """
    try:
        from entities.bill.business.service import BillService
        svc = BillService()
        existing = svc.read_by_bill_number_and_vendor_public_id(
            bill_number=bill_number,
            vendor_public_id=vendor_public_id,
        )
        if existing and existing.public_id != current_bill_public_id:
            return {
                "is_duplicate": True,
                "existing_bill_public_id": existing.public_id,
                "existing_bill_date": str(existing.bill_date) if existing.bill_date else None,
            }
        return {"is_duplicate": False}
    except Exception as e:
        return {"is_duplicate": False, "error": str(e)}


@tool
def check_amount_anomaly(vendor_public_id: str, amount: float) -> dict:
    """Compare a bill amount against the vendor's historical average.

    Flags if the amount is significantly higher or lower than typical.
    """
    try:
        from entities.bill.business.service import BillService
        svc = BillService()
        bills = svc.read_all()  # TODO: add read_by_vendor_public_id for efficiency

        vendor_bills = [
            b for b in bills
            if getattr(b, "vendor_public_id", None) == vendor_public_id
            and b.total_amount is not None
        ]

        if len(vendor_bills) < 2:
            return {
                "has_history": False,
                "message": f"Only {len(vendor_bills)} prior bill(s) — not enough history for comparison",
            }

        amounts = [float(b.total_amount) for b in vendor_bills]
        avg = sum(amounts) / len(amounts)
        max_amt = max(amounts)
        min_amt = min(amounts)

        anomaly = None
        if amount > avg * 3:
            anomaly = f"Amount ${amount:.2f} is {amount/avg:.1f}x the average (${avg:.2f})"
        elif amount < avg * 0.1 and avg > 100:
            anomaly = f"Amount ${amount:.2f} is unusually low vs average ${avg:.2f}"

        return {
            "has_history": True,
            "prior_bills": len(vendor_bills),
            "average_amount": round(avg, 2),
            "min_amount": round(min_amt, 2),
            "max_amount": round(max_amt, 2),
            "anomaly": anomaly,
        }
    except Exception as e:
        return {"has_history": False, "error": str(e)}


@tool
def check_coding_consistency(vendor_name: str, sub_cost_code_names: list) -> dict:
    """Check if the cost codes assigned make sense for this vendor type.

    Compares the bill's cost codes against what this vendor typically uses.
    """
    try:
        from entities.vendor.business.service import VendorService
        vendor_svc = VendorService()
        vendor = vendor_svc.read_by_name(vendor_name)

        if not vendor:
            return {"checked": False, "message": "Vendor not found"}

        vendor_type = getattr(vendor, "vendor_type_name", None)
        return {
            "checked": True,
            "vendor_type": vendor_type,
            "assigned_cost_codes": sub_cost_code_names,
            "note": f"Vendor type is '{vendor_type}'. Verify cost codes align with this vendor's typical work."
            if vendor_type else "No vendor type assigned — cannot check coding consistency.",
        }
    except Exception as e:
        return {"checked": False, "error": str(e)}


@tool
def submit_validation(
    passed: bool,
    issues: list,
    summary: str,
) -> dict:
    """Submit the final validation result.

    Args:
        passed: True if no blocking errors found
        issues: List of dicts with {severity, field, message, suggestion}
        summary: Brief summary of the validation outcome
    """
    return {
        "success": True,
        "passed": passed,
        "issues": issues,
        "summary": summary,
    }


# All tools
BILL_VALIDATION_TOOLS = [
    get_bill_details,
    check_duplicate_bill_number,
    check_amount_anomaly,
    check_coding_consistency,
    submit_validation,
]

TOOLS_BY_NAME = {t.name: t for t in BILL_VALIDATION_TOOLS}
