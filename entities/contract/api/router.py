# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.contract.api.schemas import ContractCreate, ContractUpdate
from entities.contract.business.service import ContractService
from shared.api.responses import (
    list_response,
    item_response,
    raise_workflow_error,
    raise_not_found,
)
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import (
    ProcessEngine,
    TriggerContext,
    EventType,
    Channel,
)

# MINIMAL BY DESIGN: only enough surface to set + read a Project's Builder's-Fee
# rate (create / read-by-project / read-by-public-id / update). List-all and
# delete are deferred until the full contract model is designed. Contract is a
# Project-scoped entity, so its routes gate on Modules.PROJECTS.
router = APIRouter(prefix="/api/v1", tags=["api", "contract"])
service = ContractService()


def _dec(value):
    """Decimals transport as strings through the workflow payload — never float."""
    return str(value) if value is not None else None


@router.post("/create/contract")
def create_contract_router(
    body: ContractCreate,
    current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "project_id": body.project_id,
            "builders_fee_rate": _dec(body.builders_fee_rate),
        },
        workflow_type="contract_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create contract")
    return item_response(result.get("data"))


@router.get("/get/contracts/project/{project_id}")
def get_contracts_by_project_router(
    project_id: int,
    current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_read")),
):
    rows = service.read_by_project_id(project_id=project_id)
    return list_response([row.to_dict() for row in rows])


@router.get("/get/contract/{public_id}")
def get_contract_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_read")),
):
    row = service.read_by_public_id(public_id=public_id)
    if not row:
        raise_not_found("Contract")
    return item_response(row.to_dict())


@router.put("/update/contract/{public_id}")
def update_contract_router(
    public_id: str,
    body: ContractUpdate,
    current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_update")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "builders_fee_rate": _dec(body.builders_fee_rate),
        },
        workflow_type="contract_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update contract")
    if result.get("data") is None:
        raise_not_found("Contract")
    return item_response(result.get("data"))
