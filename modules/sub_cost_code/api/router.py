# Python Standard Library Imports

# Third-party Imports
from fastapi import FastAPI, APIRouter, HTTPException

# Local Imports
from modules.sub_cost_code.business.service import SubCostCodeService
from modules.sub_cost_code.api.schemas import (
    SubCostCodeCreate,
    SubCostCodeUpdate,
)
from shared.database import (
    DatabaseConcurrencyError,
    DatabaseError,
)

router = APIRouter(prefix="/api/v1", tags=["api", "sub-cost-code"])
service = SubCostCodeService()


def _handle_database_error(error: DatabaseError):
    message = str(error)
    lowered = message.lower()
    if isinstance(error, DatabaseConcurrencyError):
        raise HTTPException(status_code=409, detail=message)
    if 'parent cost code not found' in lowered:
        raise HTTPException(status_code=422, detail=message)
    if 'duplicate' in lowered or 'unique' in lowered or 'conflict' in lowered:
        raise HTTPException(status_code=409, detail=message)
    raise HTTPException(status_code=500, detail=message)


@router.post("/create/sub-cost-code")
def create_sub_cost_code_router(body: SubCostCodeCreate):
    """
    Create a new sub cost code.
    """
    try:
        sub_cost_code = service.create(
            cost_code_public_id=body.cost_code_public_id,
            number=body.number,
            name=body.name,
            description=body.description,
        )
        return sub_cost_code.to_dict()
    except DatabaseError as error:
        _handle_database_error(error)


@router.get("/get/sub-cost-codes")
def get_sub_cost_codes_router(cost_code_public_id: str | None = None):
    """
    Read all sub cost codes.
    """
    try:
        sub_cost_codes = service.read_all(cost_code_public_id=cost_code_public_id)
        return [sub_cost_code.to_dict() for sub_cost_code in sub_cost_codes]
    except DatabaseError as error:
        _handle_database_error(error)


@router.get("/get/sub-cost-code/{public_id}")
def get_sub_cost_code_by_public_id_router(public_id: str):
    """
    Read a sub cost code by public ID.
    """
    try:
        sub_cost_code = service.read_by_public_id(public_id=public_id)
        if not sub_cost_code:
            raise HTTPException(status_code=404, detail="Sub cost code not found.")
        return sub_cost_code.to_dict()
    except DatabaseError as error:
        _handle_database_error(error)


@router.put("/update/sub-cost-code/{public_id}")
def update_sub_cost_code_by_id_router(public_id: str, body: SubCostCodeUpdate):
    """
    Update a sub cost code by ID.
    """
    try:
        sub_cost_code = service.update_by_public_id(public_id=public_id, sub_cost_code=body)
        if not sub_cost_code:
            raise HTTPException(status_code=404, detail="Sub cost code not found.")
        return sub_cost_code.to_dict()
    except DatabaseError as error:
        _handle_database_error(error)


@router.delete("/delete/sub-cost-code/{public_id}")
def delete_sub_cost_code_by_public_id_router(public_id: str):
    """
    Soft delete a sub cost code by ID.
    """
    try:
        sub_cost_code = service.delete_by_public_id(public_id=public_id)
        if not sub_cost_code:
            raise HTTPException(status_code=404, detail="Sub cost code not found.")
        return sub_cost_code.to_dict()
    except DatabaseError as error:
        _handle_database_error(error)
