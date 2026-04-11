# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.company.api.schemas import CompanyCreate, CompanyUpdate
from entities.company.business.service import CompanyService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "company"])


@router.post("/create/company")
def create_company_router(body: CompanyCreate, current_user: dict = Depends(require_module_api(Modules.COMPANIES, "can_create"))):
    """
    Create a new company.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "website": body.website,
        },
        workflow_type="company_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create company")
    
    return item_response(result.get("data"))


@router.get("/get/companies")
def get_companies_router(current_user: dict = Depends(require_module_api(Modules.COMPANIES))):
    """
    Read all companies.
    """
    companies = CompanyService().read_all()
    return list_response([company.to_dict() for company in companies])


@router.get("/get/company/{public_id}")
def get_company_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.COMPANIES))):
    """
    Read a company by public ID.
    """
    company = CompanyService().read_by_public_id(public_id=public_id)
    return item_response(company.to_dict())


@router.put("/update/company/{public_id}")
def update_company_by_public_id_router(public_id: str, body: CompanyUpdate, current_user: dict = Depends(require_module_api(Modules.COMPANIES, "can_update"))):
    """
    Update a company by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "name": body.name,
            "website": body.website,
        },
        workflow_type="company_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update company")
    
    return item_response(result.get("data"))


@router.delete("/delete/company/{public_id}")
def delete_company_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.COMPANIES, "can_delete"))):
    """
    Delete a company by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="company_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete company")
    
    return item_response(result.get("data"))
