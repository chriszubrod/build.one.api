"""
Contract Labor Matching Agent Tools
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def search_vendors(name: str) -> dict:
    """Fuzzy-search vendors by name to match a contractor/employee name."""
    try:
        from entities.vendor.business.service import VendorService
        from difflib import SequenceMatcher
        svc = VendorService()

        # Exact match
        exact = svc.read_by_name(name)
        if exact:
            return {"found": True, "match_type": "exact",
                    "vendor": {"id": exact.id, "public_id": exact.public_id, "name": exact.name}}

        # Fuzzy
        all_vendors = svc.read_all()
        query_lower = name.lower()
        matches = []
        for v in all_vendors:
            score = SequenceMatcher(None, query_lower, v.name.lower()).ratio()
            if score >= 0.5:
                matches.append({"id": v.id, "public_id": v.public_id, "name": v.name, "score": round(score, 3)})
        matches.sort(key=lambda m: m["score"], reverse=True)

        if matches:
            return {"found": True, "match_type": "fuzzy", "matches": matches[:5]}
        return {"found": False}
    except Exception as e:
        return {"found": False, "error": str(e)}


@tool
def search_projects(job_name: str) -> dict:
    """Match a job/project name from the timesheet to a project in the system."""
    try:
        from entities.project.business.service import ProjectService
        from difflib import SequenceMatcher
        svc = ProjectService()
        all_projects = svc.read_all()

        query_lower = job_name.lower()
        matches = []
        for p in all_projects:
            candidates = [p.name.lower()]
            if getattr(p, "abbreviation", None):
                candidates.append(p.abbreviation.lower())
            best_score = max(SequenceMatcher(None, query_lower, c).ratio() for c in candidates)
            if best_score >= 0.4:
                matches.append({
                    "id": p.id, "public_id": p.public_id, "name": p.name,
                    "abbreviation": getattr(p, "abbreviation", None),
                    "score": round(best_score, 3),
                })
        matches.sort(key=lambda m: m["score"], reverse=True)

        if matches:
            return {"found": True, "matches": matches[:5]}
        return {"found": False}
    except Exception as e:
        return {"found": False, "error": str(e)}


@tool
def get_vendor_last_rate(vendor_id: int) -> dict:
    """Get the most recent hourly rate and markup for a vendor from prior contract labor entries."""
    try:
        from entities.contract_labor.persistence.repo import ContractLaborRepository
        repo = ContractLaborRepository()
        # Query recent entries for this vendor
        from shared.database import get_connection, call_procedure
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT TOP 1 HourlyRate, Markup FROM dbo.ContractLabor "
                "WHERE VendorId = ? AND HourlyRate IS NOT NULL "
                "ORDER BY WorkDate DESC",
                vendor_id,
            )
            row = cursor.fetchone()
            if row:
                return {
                    "found": True,
                    "hourly_rate": float(row.HourlyRate) if row.HourlyRate else None,
                    "markup": float(row.Markup) if row.Markup else None,
                }
        return {"found": False}
    except Exception as e:
        return {"found": False, "error": str(e)}


@tool
def get_vendor_common_projects(vendor_id: int) -> dict:
    """Get projects this vendor most commonly works on."""
    try:
        from shared.database import get_connection
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT TOP 5 p.Id, p.PublicId, p.Name, COUNT(*) AS EntryCount "
                "FROM dbo.ContractLabor cl "
                "JOIN dbo.Project p ON cl.ProjectId = p.Id "
                "WHERE cl.VendorId = ? "
                "GROUP BY p.Id, p.PublicId, p.Name "
                "ORDER BY COUNT(*) DESC",
                vendor_id,
            )
            rows = cursor.fetchall()
            if rows:
                return {
                    "found": True,
                    "projects": [
                        {"id": r.Id, "public_id": r.PublicId, "name": r.Name, "entry_count": r.EntryCount}
                        for r in rows
                    ],
                }
        return {"found": False}
    except Exception as e:
        return {"found": False, "error": str(e)}


@tool
def propose_match(
    entry_index: int,
    vendor_id: Optional[int] = None,
    vendor_name: Optional[str] = None,
    project_id: Optional[int] = None,
    project_name: Optional[str] = None,
    hourly_rate: Optional[float] = None,
    markup: Optional[float] = None,
    confidence: float = 0.0,
    reasoning: str = "",
) -> dict:
    """Submit a match proposal for a contract labor entry.

    Args:
        entry_index: Index of the entry being matched
        vendor_id: Matched vendor database ID
        vendor_name: Matched vendor name (for display)
        project_id: Matched project database ID
        project_name: Matched project name (for display)
        hourly_rate: Proposed hourly rate
        markup: Proposed markup percentage
        confidence: Overall match confidence (0.0-1.0)
        reasoning: Brief explanation of the match
    """
    return {
        "success": True,
        "entry_index": entry_index,
        "vendor_id": vendor_id,
        "vendor_name": vendor_name,
        "project_id": project_id,
        "project_name": project_name,
        "hourly_rate": hourly_rate,
        "markup": markup,
        "confidence": confidence,
        "reasoning": reasoning,
    }


CONTRACT_LABOR_TOOLS = [
    search_vendors,
    search_projects,
    get_vendor_last_rate,
    get_vendor_common_projects,
    propose_match,
]

TOOLS_BY_NAME = {t.name: t for t in CONTRACT_LABOR_TOOLS}
