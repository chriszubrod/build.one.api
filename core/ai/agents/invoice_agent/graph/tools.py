"""
Invoice Composition Agent Tools
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def get_billable_items(project_public_id: str) -> dict:
    """Retrieve billable line items for a project that haven't been invoiced yet."""
    try:
        from entities.invoice.business.service import InvoiceService
        svc = InvoiceService()
        result = svc.get_billable_items_for_project(project_public_id)
        if not result:
            return {"found": False, "message": "No billable items found"}

        items = []
        for item in result:
            items.append({
                "source_type": getattr(item, "source_type", None),
                "description": getattr(item, "description", None),
                "amount": str(getattr(item, "amount", None)) if getattr(item, "amount", None) else None,
                "date": str(getattr(item, "date", None)) if getattr(item, "date", None) else None,
                "vendor_name": getattr(item, "vendor_name", None),
                "cost_code": getattr(item, "cost_code_name", None),
                "is_billable": getattr(item, "is_billable", True),
            })

        return {"found": True, "count": len(items), "items": items}
    except Exception as e:
        return {"found": False, "error": str(e)}


@tool
def get_project_details(project_public_id: str) -> dict:
    """Get project information for context."""
    try:
        from entities.project.business.service import ProjectService
        svc = ProjectService()
        project = svc.read_by_public_id(project_public_id)
        if not project:
            return {"found": False}
        return {
            "found": True,
            "project": {
                "name": project.name,
                "abbreviation": getattr(project, "abbreviation", None),
                "customer_name": getattr(project, "customer_name", None),
                "status": getattr(project, "status", None),
            },
        }
    except Exception as e:
        return {"found": False, "error": str(e)}


@tool
def get_previous_invoices(project_public_id: str) -> dict:
    """Get previous invoices for this project to understand formatting patterns."""
    try:
        from entities.invoice.business.service import InvoiceService
        svc = InvoiceService()
        invoices = svc.read_by_project_public_id(project_public_id)
        if not invoices:
            return {"found": False, "message": "No prior invoices"}

        return {
            "found": True,
            "count": len(invoices),
            "invoices": [
                {
                    "invoice_number": getattr(inv, "invoice_number", None),
                    "invoice_date": str(getattr(inv, "invoice_date", None)) if getattr(inv, "invoice_date", None) else None,
                    "total_amount": str(getattr(inv, "total_amount", None)) if getattr(inv, "total_amount", None) else None,
                }
                for inv in invoices[:5]  # Last 5
            ],
        }
    except Exception as e:
        return {"found": False, "error": str(e)}


@tool
def propose_grouping(
    groups: list,
    total_amount: float,
    summary: str,
) -> dict:
    """Submit a proposed invoice grouping.

    Args:
        groups: List of dicts with {description, items (list of indices), amount, reasoning}
        total_amount: Total invoice amount
        summary: Brief description of the proposed invoice
    """
    return {
        "success": True,
        "groups": groups,
        "total_amount": total_amount,
        "summary": summary,
    }


INVOICE_TOOLS = [
    get_billable_items,
    get_project_details,
    get_previous_invoices,
    propose_grouping,
]

TOOLS_BY_NAME = {t.name: t for t in INVOICE_TOOLS}
