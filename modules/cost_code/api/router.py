# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, HTTPException, Depends

# Local Imports
from modules.cost_code.business.service import CostCodeService
from modules.cost_code.api.schemas import (
    CostCodeCreate,
    CostCodeUpdate,
)
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "cost-code"])
service = CostCodeService()


@router.post("/create/cost-code")
def create_cost_code_router(body: CostCodeCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new cost code.
    """
    cost_code = service.create(
        number=body.number,
        name=body.name,
        description=body.description
    )
    return cost_code.to_dict()


@router.get("/get/cost-codes")
def get_cost_codes_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all cost codes.
    """
    cost_codes = service.read_all()
    return [cost_code.to_dict() for cost_code in cost_codes]


@router.get("/get/cost-code/{public_id}")
def get_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a cost code by public ID.
    """
    cost_code = service.read_by_public_id(public_id=public_id)
    return cost_code.to_dict()


@router.put("/update/cost-code/{public_id}")
def update_cost_code_by_id_router(public_id: str, body: CostCodeUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a cost code by ID.
    """
    cost_code = service.update_by_public_id(public_id=public_id, cost_code=body)
    return cost_code.to_dict()


@router.delete("/delete/cost-code/{public_id}")
def delete_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Soft delete a cost code by ID.
    """
    cost_code = service.delete_by_public_id(public_id=public_id)
    return cost_code.to_dict()
