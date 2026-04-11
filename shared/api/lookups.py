# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query

# Local Imports
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "lookups"])

# Valid lookup keys
VALID_LOOKUPS = {
    "vendors",
    "projects",
    "sub_cost_codes",
    "cost_codes",
    "payment_terms",
    "customers",
    "vendor_types",
    "address_types",
    "roles",
    "modules",
}


def _get_vendors() -> list[dict]:
    from entities.vendor.business.service import VendorService
    vendors = VendorService().read_all()
    return [
        {
            "public_id": v.public_id,
            "name": v.name,
            "abbreviation": v.abbreviation,
            "is_contract_labor": v.is_contract_labor,
        }
        for v in vendors
    ]


def _get_projects() -> list[dict]:
    from entities.project.business.service import ProjectService
    projects = ProjectService().read_all()
    return [
        {
            "public_id": p.public_id,
            "name": p.name,
            "abbreviation": p.abbreviation,
        }
        for p in projects
    ]


def _get_sub_cost_codes() -> list[dict]:
    from entities.sub_cost_code.business.service import SubCostCodeService
    sccs = SubCostCodeService().read_all()
    return [
        {
            "public_id": s.public_id,
            "number": s.number,
            "name": s.name,
            "cost_code_id": s.cost_code_id,
        }
        for s in sccs
    ]


def _get_cost_codes() -> list[dict]:
    from entities.cost_code.business.service import CostCodeService
    codes = CostCodeService().read_all()
    return [
        {
            "public_id": c.public_id,
            "number": c.number,
            "name": c.name,
        }
        for c in codes
    ]


def _get_payment_terms() -> list[dict]:
    from entities.payment_term.business.service import PaymentTermService
    terms = PaymentTermService().read_all()
    return [
        {
            "public_id": t.public_id,
            "name": t.name,
            "due_days": t.due_days,
        }
        for t in terms
    ]


def _get_customers() -> list[dict]:
    from entities.customer.business.service import CustomerService
    customers = CustomerService().read_all()
    return [
        {
            "public_id": c.public_id,
            "name": c.name,
        }
        for c in customers
    ]


def _get_vendor_types() -> list[dict]:
    from entities.vendor_type.business.service import VendorTypeService
    types = VendorTypeService().read_all()
    return [
        {
            "public_id": t.public_id,
            "name": t.name,
        }
        for t in types
    ]


def _get_address_types() -> list[dict]:
    from entities.address_type.business.service import AddressTypeService
    types = AddressTypeService().read_all()
    return [
        {
            "public_id": t.public_id,
            "name": t.name,
            "display_order": t.display_order,
        }
        for t in types
    ]


def _get_roles() -> list[dict]:
    from entities.role.business.service import RoleService
    roles = RoleService().read_all()
    return [
        {
            "public_id": r.public_id,
            "name": r.name,
        }
        for r in roles
    ]


def _get_modules() -> list[dict]:
    from entities.module.business.service import ModuleService
    modules = ModuleService().read_all()
    return [
        {
            "public_id": m.public_id,
            "name": m.name,
            "route": m.route,
        }
        for m in modules
    ]


LOOKUP_FETCHERS = {
    "vendors": _get_vendors,
    "projects": _get_projects,
    "sub_cost_codes": _get_sub_cost_codes,
    "cost_codes": _get_cost_codes,
    "payment_terms": _get_payment_terms,
    "customers": _get_customers,
    "vendor_types": _get_vendor_types,
    "address_types": _get_address_types,
    "roles": _get_roles,
    "modules": _get_modules,
}


@router.get("/lookups")
def get_lookups(
    include: str = Query(..., description="Comma-separated lookup keys: vendors,projects,sub_cost_codes,cost_codes,payment_terms,customers,vendor_types,address_types,roles,modules"),
    current_user: dict = Depends(require_module_api(Modules.DASHBOARD)),
):
    """
    Return slim lookup data for dropdown/select fields.

    Pass ?include=vendors,projects,sub_cost_codes to fetch multiple datasets
    in a single request.
    """
    requested = [key.strip() for key in include.split(",") if key.strip()]
    invalid = set(requested) - VALID_LOOKUPS
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid lookup keys: {', '.join(sorted(invalid))}. Valid keys: {', '.join(sorted(VALID_LOOKUPS))}",
        )

    result = {}
    for key in requested:
        fetcher = LOOKUP_FETCHERS[key]
        result[key] = fetcher()

    return {"data": result}
