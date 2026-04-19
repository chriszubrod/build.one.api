# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.company_info.api.schemas import QboCompanyInfoSync
from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.api.responses import list_response, item_response

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-company-info"])
service = QboCompanyInfoService()


@router.post("/sync/qbo-company-info")
def sync_qbo_company_info_router(body: QboCompanyInfoSync, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Sync CompanyInfo from QBO.
    """
    company_info = service.sync_from_qbo(realm_id=body.realm_id)
    return item_response(company_info.to_dict())


@router.get("/get/qbo-company-infos")
def get_qbo_company_infos_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO company infos.
    """
    company_infos = service.read_all()
    return list_response([company_info.to_dict() for company_info in company_infos])


@router.get("/get/qbo-company-info/{qbo_id}")
def get_qbo_company_info_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO company info by QBO ID.
    """
    company_info = service.read_by_qbo_id(qbo_id=qbo_id)
    return company_info.to_dict() if company_info else None


@router.get("/get/qbo-company-info/realm/{realm_id}")
def get_qbo_company_info_by_realm_id_router(realm_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO company info by realm ID.
    """
    company_info = service.read_by_realm_id(realm_id=realm_id)
    return company_info.to_dict() if company_info else None

