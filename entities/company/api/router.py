# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.company.api.schemas import CompanyCreate, CompanyUpdate
from entities.company.business.service import CompanyService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "company"])


@router.post("/create/company")
def create_company_router(body: CompanyCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new company.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "website": body.website,
        },
        workflow_type="company_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create company")
        )
    
    return result.get("data")


@router.get("/get/companies")
def get_companies_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all companies.
    """
    companies = CompanyService().read_all()
    return [company.to_dict() for company in companies]


@router.get("/get/company/{public_id}")
def get_company_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a company by public ID.
    """
    company = CompanyService().read_by_public_id(public_id=public_id)
    return company.to_dict()


@router.put("/update/company/{public_id}")
def update_company_by_public_id_router(public_id: str, body: CompanyUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a company by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
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
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update company")
        )
    
    return result.get("data")


@router.delete("/delete/company/{public_id}")
def delete_company_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a company by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="company_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete company")
        )
    
    return result.get("data")
