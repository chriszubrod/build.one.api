# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.account.api.schemas import QboAccountSync
from integrations.intuit.qbo.account.business.service import QboAccountService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.api.responses import list_response, item_response

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-account"])
service = QboAccountService()


@router.post("/sync/qbo-accounts")
def sync_qbo_accounts_router(body: QboAccountSync, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Sync Accounts from QBO.
    """
    accounts = service.sync_from_qbo(
        realm_id=body.realm_id,
        last_updated_time=body.last_updated_time,
    )
    return list_response([account.to_dict() for account in accounts])


@router.get("/get/qbo-accounts/realm/{realm_id}")
def get_qbo_accounts_by_realm_id_router(realm_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO accounts by realm ID.
    """
    accounts = service.read_by_realm_id(realm_id=realm_id)
    return list_response([account.to_dict() for account in accounts])


@router.get("/get/qbo-account/qbo-id/{qbo_id}")
def get_qbo_account_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO account by QBO ID.
    """
    account = service.read_by_qbo_id(qbo_id=qbo_id)
    return account.to_dict() if account else None


@router.get("/get/qbo-accounts")
def get_qbo_accounts_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO accounts.
    """
    accounts = service.read_all()
    return list_response([account.to_dict() for account in accounts])


@router.get("/get/qbo-account/{id}")
def get_qbo_account_by_id_router(id: int, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO account by ID.
    """
    account = service.read_by_id(id=id)
    return account.to_dict() if account else None
