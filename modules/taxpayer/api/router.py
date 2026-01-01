# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from modules.taxpayer.api.schemas import TaxpayerCreate, TaxpayerUpdate
from modules.taxpayer.business.service import TaxpayerService
from modules.auth.business.service import get_current_user_api as get_current_taxpayer_api

router = APIRouter(prefix="/api/v1", tags=["api", "taxpayer"])
service = TaxpayerService()


@router.post("/create/taxpayer")
def create_taxpayer_router(body: TaxpayerCreate, current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Create a new taxpayer.
    """
    try:
        taxpayer = service.create(
            entity_name=body.entity_name,
            business_name=body.business_name,
            classification=body.classification,
            taxpayer_id_number=body.taxpayer_id_number,
        )
        return taxpayer.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/taxpayers")
def get_taxpayers_router(current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Read all taxpayers.
    """
    taxpayers = service.read_all()
    return [taxpayer.to_dict() for taxpayer in taxpayers]


@router.get("/get/taxpayer/{public_id}")
def get_taxpayer_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Read a taxpayer by public ID.
    """
    taxpayer = service.read_by_public_id(public_id=public_id)
    return taxpayer.to_dict()


@router.put("/update/taxpayer/{public_id}")
def update_taxpayer_by_public_id_router(public_id: str, body: TaxpayerUpdate, current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Update a taxpayer by public ID.
    """
    try:
        taxpayer = service.update_by_public_id(public_id=public_id, taxpayer=body)
        return taxpayer.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/taxpayer/{public_id}")
def delete_taxpayer_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Delete a taxpayer by public ID.
    """
    taxpayer = service.delete_by_public_id(public_id=public_id)
    return taxpayer.to_dict()
