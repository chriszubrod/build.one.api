# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from integrations.intuit.qbo.vendorcredit.api.schemas import QboVendorCreditSyncRequest
from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "qbo", "vendorcredit"])


@router.post("/sync/qbo-vendorcredits")
def sync_qbo_vendor_credits_router(
    body: QboVendorCreditSyncRequest,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create")),
):
    """
    Sync VendorCredits from QBO to local cache and optionally to BillCredit module.
    """
    try:
        service = QboVendorCreditService()
        vendor_credits = service.sync_from_qbo(
            realm_id=body.realm_id,
            last_updated_time=body.last_updated_time,
            start_date=body.start_date,
            end_date=body.end_date,
            sync_to_modules=body.sync_to_modules,
        )
        return {
            "status": "success",
            "count": len(vendor_credits),
            "vendor_credits": [vc.to_dict() for vc in vendor_credits],
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/get/qbo-vendorcredits/realm/{realm_id}")
def get_qbo_vendor_credits_by_realm_router(
    realm_id: str,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC)),
):
    """
    Get all cached VendorCredits for a realm.
    """
    service = QboVendorCreditService()
    vendor_credits = service.read_by_realm_id(realm_id)
    return [vc.to_dict() for vc in vendor_credits]


@router.get("/get/qbo-vendorcredit/{id}")
def get_qbo_vendor_credit_by_id_router(
    id: int,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC)),
):
    """
    Get a cached VendorCredit by ID.
    """
    service = QboVendorCreditService()
    vendor_credit = service.read_by_id(id)
    if not vendor_credit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VendorCredit not found")
    return vendor_credit.to_dict()


@router.get("/get/qbo-vendorcredit/{id}/lines")
def get_qbo_vendor_credit_lines_router(
    id: int,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC)),
):
    """
    Get line items for a cached VendorCredit.
    """
    service = QboVendorCreditService()
    vendor_credit = service.read_by_id(id)
    if not vendor_credit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VendorCredit not found")
    
    lines = service.read_lines_by_vendor_credit_id(id)
    return [line.to_dict() for line in lines]


@router.get("/get/qbo-vendorcredit/qbo-id/{qbo_id}")
def get_qbo_vendor_credit_by_qbo_id_router(
    qbo_id: str,
    realm_id: str,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC)),
):
    """
    Get a cached VendorCredit by QBO ID and realm.
    """
    service = QboVendorCreditService()
    vendor_credit = service.read_by_qbo_id(qbo_id, realm_id)
    if not vendor_credit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VendorCredit not found")
    return vendor_credit.to_dict()
