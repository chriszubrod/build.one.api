"""Aggregate "ready" ContractLabor entries into per-vendor bill summaries.

Extracted from the Jinja `bills_page` web controller during Wave E3 so the
React Bills page can consume the same data through a single API call.
"""

# Python Standard Library Imports
from typing import Any, Optional

# Local Imports
from entities.contract_labor.business.service import ContractLaborService
from entities.contract_labor.persistence.line_item_repo import (
    ContractLaborLineItemRepository,
)
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.vendor.business.service import VendorService


def build_bills_summary(billing_period: Optional[str] = None) -> list[dict[str, Any]]:
    """Group ready ContractLabor entries by vendor with day-level aggregates.

    Returns one dict per vendor (or per employee_name when no vendor is set):
        vendor_id, vendor_name, employee_name, line_items[], line_items_summary[],
        total_hours, total_amount, min_date, max_date.
    """
    service = ContractLaborService()
    line_item_repo = ContractLaborLineItemRepository()

    projects = ProjectService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    project_map = {p.id: p for p in projects}
    scc_map = {s.id: s for s in sub_cost_codes}

    ready_entries = service.read_by_status(
        status="ready", billing_period_start=billing_period
    )

    vendor_groups: dict[Any, dict[str, Any]] = {}
    for entry in ready_entries:
        vendor_key = entry.bill_vendor_id or f"employee:{entry.employee_name}"

        if vendor_key not in vendor_groups:
            vendor_groups[vendor_key] = {
                "vendor_id": entry.bill_vendor_id,
                "vendor_name": None,
                "employee_name": entry.employee_name,
                "line_items": [],
                "by_date": {},
                "total_hours": 0.0,
                "total_amount": 0.0,
                "min_date": None,
                "max_date": None,
            }

        group = vendor_groups[vendor_key]
        line_items = line_item_repo.read_by_contract_labor_id(
            contract_labor_id=entry.id
        )

        for li in line_items:
            project = project_map.get(li.project_id)
            scc = scc_map.get(li.sub_cost_code_id)

            group["line_items"].append(
                {
                    "date": str(li.line_date or entry.work_date)
                    if (li.line_date or entry.work_date)
                    else None,
                    "sub_cost_code": scc.number if scc else "",
                    "description": li.description or "",
                    "amount": float(li.price or 0),
                    "project": (project.abbreviation or project.name)
                    if project
                    else "",
                    "entry_public_id": entry.public_id,
                }
            )
            group["total_amount"] += float(li.price or 0)

            date = li.line_date or entry.work_date
            if date:
                hours = float(li.hours or 0)
                rate = float(li.rate or 0)
                cost_before_markup = (hours / 8.0) * rate
                price_after_markup = float(li.price or 0)
                day_key = str(date)
                if day_key not in group["by_date"]:
                    group["by_date"][day_key] = {
                        "billed_hours": 0.0,
                        "cost_before_markup": 0.0,
                        "price_after_markup": 0.0,
                    }
                group["by_date"][day_key]["billed_hours"] += hours
                group["by_date"][day_key]["cost_before_markup"] += cost_before_markup
                group["by_date"][day_key]["price_after_markup"] += price_after_markup

        group["total_hours"] += float(entry.total_hours or 0)

        if entry.work_date:
            if not group["min_date"] or entry.work_date < group["min_date"]:
                group["min_date"] = entry.work_date
            if not group["max_date"] or entry.work_date > group["max_date"]:
                group["max_date"] = entry.work_date

    vendors = VendorService().read_all()
    vendor_map = {v.id: v.name for v in vendors}

    vendors_with_entries: list[dict[str, Any]] = []
    for data in vendor_groups.values():
        if data["vendor_id"]:
            data["vendor_name"] = vendor_map.get(
                data["vendor_id"], f"Vendor #{data['vendor_id']}"
            )
        else:
            data["vendor_name"] = data["employee_name"] or "Unknown"

        data["line_items_summary"] = [
            {
                "date": d,
                "billed_hours": round(v["billed_hours"], 2),
                "cost_before_markup": round(v["cost_before_markup"], 2),
                "price_after_markup": round(v["price_after_markup"], 2),
            }
            for d, v in sorted(data["by_date"].items())
        ]
        data.pop("by_date")

        if data["min_date"]:
            data["min_date"] = str(data["min_date"])
        if data["max_date"]:
            data["max_date"] = str(data["max_date"])

        vendors_with_entries.append(data)

    vendors_with_entries.sort(key=lambda x: (x["vendor_name"] or "").lower())
    return vendors_with_entries
