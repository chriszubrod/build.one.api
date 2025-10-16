# Python Standard Library Imports

# Third-party Imports
from fastapi import FastAPI, APIRouter, HTTPException, Depends

# Local Imports
from modules.sub_cost_code.business.service import SubCostCodeService
from modules.sub_cost_code.api.schemas import (
    SubCostCodeCreate,
    SubCostCodeUpdate,
)
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "sub-cost-code"])
service = SubCostCodeService()


@router.post("/create/sub-cost-code")
def create_sub_cost_code_router(body: SubCostCodeCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new sub cost code.
    """
    sub_cost_code = SubCostCodeService().create(
        number=body.number,
        name=body.name,
        description=body.description,
        cost_code_id=body.cost_code_id,
    )
    return sub_cost_code.to_dict()


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
    """
    sub_cost_code = service.update_by_public_id(public_id=public_id, sub_cost_code=body)
    if not sub_cost_code:
        raise HTTPException(status_code=404, detail="Sub cost code not found.")
    return sub_cost_code.to_dict()


@router.delete("/delete/sub-cost-code/{public_id}")
def delete_sub_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Soft delete a sub cost code by ID.
    """
    sub_cost_code = service.delete_by_public_id(public_id=public_id)
    if not sub_cost_code:
        raise HTTPException(status_code=404, detail="Sub cost code not found.")
    return sub_cost_code.to_dict()
