# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, HTTPException, Depends, status

# Local Imports
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.sub_cost_code.business.alias_service import SubCostCodeAliasService
from entities.sub_cost_code.api.schemas import (
    SubCostCodeCreate,
    SubCostCodeUpdate,
    SubCostCodeAliasCreate,
    SubCostCodeAliasUpdate,
)
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "sub-cost-code"])
service = SubCostCodeService()
alias_service = SubCostCodeAliasService()


@router.post("/create/sub-cost-code")
def create_sub_cost_code_router(body: SubCostCodeCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new sub cost code.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "number": body.number,
            "name": body.name,
            "description": body.description,
            "cost_code_id": body.cost_code_id,
        },
        workflow_type="sub_cost_code_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create sub cost code")
        )
    
    return result.get("data")


@router.get("/get/sub-cost-codes")
def get_sub_cost_codes_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all sub cost codes.
    """
    sub_cost_codes = service.read_all()
    if not sub_cost_codes:
        raise HTTPException(status_code=404, detail="Sub cost codes not found.")
    return [sub_cost_code.to_dict() for sub_cost_code in sub_cost_codes]


@router.get("/get/sub-cost-code/{public_id}")
def get_sub_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a sub cost code by public ID.
    """
    sub_cost_code = service.read_by_public_id(public_id=public_id)
    if not sub_cost_code:
        raise HTTPException(status_code=404, detail="Sub cost code not found.")
    return sub_cost_code.to_dict()


@router.put("/update/sub-cost-code/{public_id}")
def update_sub_cost_code_by_id_router(public_id: str, body: SubCostCodeUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a sub cost code by ID.
    
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
            "number": body.number,
            "name": body.name,
            "description": body.description,
            "cost_code_id": body.cost_code_id,
        },
        workflow_type="sub_cost_code_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update sub cost code")
        )
    
    return result.get("data")


@router.delete("/delete/sub-cost-code/{public_id}")
def delete_sub_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Soft delete a sub cost code by ID.
    
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
        workflow_type="sub_cost_code_delete",
    )

    result = TriggerRouter().route_instant(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete sub cost code")
        )

    return result.get("data")


# --- Sub Cost Code Alias Endpoints ---


@router.post("/create/sub-cost-code-alias")
def create_sub_cost_code_alias_router(body: SubCostCodeAliasCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new sub cost code alias.
    """
    try:
        alias = alias_service.create(
            sub_cost_code_id=body.sub_cost_code_id,
            alias=body.alias,
            source=body.source,
        )
        return alias.to_dict()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/get/sub-cost-code-aliases/{sub_cost_code_id}")
def get_sub_cost_code_aliases_router(sub_cost_code_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read all aliases for a sub cost code.
    """
    aliases = alias_service.read_by_sub_cost_code_id(sub_cost_code_id=sub_cost_code_id)
    return [alias.to_dict() for alias in aliases]


@router.delete("/delete/sub-cost-code-alias/{public_id}")
def delete_sub_cost_code_alias_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a sub cost code alias by public ID.
    """
    alias = alias_service.delete_by_public_id(public_id=public_id)
    if not alias:
        raise HTTPException(status_code=404, detail="Sub cost code alias not found.")
    return alias.to_dict()
