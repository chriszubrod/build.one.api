# Python Standard Library Imports
import logging
from typing import Optional
from uuid import UUID

# Third-party Imports
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

# Local Imports
from entities.bill.business.service import BillService
from entities.coding_suggestion.business.bill_suggestion_service import BillCodingSuggester
from entities.project.business.service import ProjectService
from entities.vendor.business.service import VendorService
from shared.access import EntityNotAccessibleError, assert_can_access_project
from shared.api.responses import item_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "bill-coding"])


class BillCodingSuggestRequest(BaseModel):
    vendor_public_id: UUID
    project_public_id: Optional[UUID] = None
    memo: Optional[str] = None
    bill_public_id: Optional[UUID] = None


def _suggestion_to_api_payload(suggestion) -> dict:
    project_public_id = None
    project_name = None
    if suggestion.project_id is not None:
        project = ProjectService().read_by_id(suggestion.project_id)
        if project is not None:
            project_public_id = project.public_id
            project_name = project.name

    confidence = (
        str(suggestion.confidence) if suggestion.confidence is not None else None
    )
    return {
        "project_public_id": project_public_id,
        "project_name": project_name,
        "sub_cost_code_public_id": suggestion.sub_cost_code_public_id,
        "sub_cost_code_number": suggestion.sub_cost_code_number,
        "sub_cost_code_name": suggestion.sub_cost_code_name,
        "source": suggestion.source,
        "reason": suggestion.reason,
        "confidence": confidence,
        "model_version": suggestion.model_version,
    }


@router.post("/bill/coding/suggest")
def suggest_bill_coding_router(
    body: BillCodingSuggestRequest,
    _: dict = Depends(require_module_api(Modules.BILLS, "can_read")),
):
    vendor = VendorService().read_by_public_id(str(body.vendor_public_id))
    if vendor is None or vendor.id is None:
        raise_not_found("Vendor")

    project_id: Optional[int] = None
    if body.project_public_id:
        project = ProjectService().read_by_public_id(str(body.project_public_id))
        if project is None or project.id is None:
            raise_not_found("Project")
        try:
            assert_can_access_project(int(project.id))
        except EntityNotAccessibleError:
            raise_not_found("Project")
        project_id = int(project.id)

    exclude_bill_id: Optional[int] = None
    if body.bill_public_id:
        try:
            bill = BillService().read_by_public_id(str(body.bill_public_id))
        except EntityNotAccessibleError:
            raise_not_found("Bill")
        if bill is None or bill.id is None:
            raise_not_found("Bill")
        exclude_bill_id = bill.id

    suggestion = BillCodingSuggester().suggest(
        vendor_id=int(vendor.id),
        project_id=project_id,
        memo=body.memo,
        exclude_bill_id=exclude_bill_id,
    )
    return item_response(_suggestion_to_api_payload(suggestion))
