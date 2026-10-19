# Python Standard Library Imports
import logging
from uuid import UUID

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.bill.business.service import BillService
from entities.bill_intake_prediction.business.service import BillIntakePredictionService
from shared.access import EntityNotAccessibleError
from shared.api.responses import item_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "bill"])


@router.get("/bill/{public_id}/intake-prediction")
def read_bill_intake_prediction_router(
    public_id: UUID,
    _: dict = Depends(require_module_api(Modules.BILLS, "can_read")),
):
    try:
        bill = BillService().read_by_public_id(public_id=str(public_id))
    except EntityNotAccessibleError:
        raise_not_found("Bill")
    if bill is None or bill.id is None:
        raise_not_found("Bill")

    intake_prediction_service = BillIntakePredictionService()
    prediction = intake_prediction_service.read_by_bill_id(int(bill.id))
    if prediction is None:
        raise_not_found("Bill intake prediction")

    payload = intake_prediction_service.to_api_payload(prediction)
    return item_response(payload)
