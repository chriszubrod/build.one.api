# Python Standard Library Imports
import logging
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Optional
from uuid import UUID

# Third-party Imports
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator

# Local Imports
from entities.bill.business.service import BillService
from entities.bill_line_item.business.service import BillLineItemService
from entities.coding_suggestion.business.bill_suggestion_service import (
    BillCodingSuggester,
    apply_budget_check_to_suggestion,
)
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
    amount: Optional[Decimal] = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _stringify_non_finite_amount(cls, data):
        # JSON 1e400 decodes to float inf. Pydantic echoes the raw input in the
        # 422 body, and inf/nan there is not JSON-serializable -> HTTP 500. As a
        # string it still fails _coerce_amount, but the error body renders.
        if isinstance(data, dict):
            amount = data.get("amount")
            if isinstance(amount, float) and not isfinite(amount):
                data = {**data, "amount": str(amount)}
        return data

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, value):
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            dec = value
        else:
            try:
                dec = Decimal(str(value))
            except InvalidOperation as exc:
                raise ValueError("amount must be a decimal number") from exc
        if not dec.is_finite():
            raise ValueError("amount must be a finite decimal number")
        return dec


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
        "budget_check": suggestion.budget_check,
        "budget_remaining": (
            str(suggestion.budget_remaining)
            if suggestion.budget_remaining is not None
            else None
        ),
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
    budget_lines = []
    if body.bill_public_id:
        try:
            bill = BillService().read_by_public_id(str(body.bill_public_id))
        except EntityNotAccessibleError:
            raise_not_found("Bill")
        if bill is None or bill.id is None:
            raise_not_found("Bill")
        exclude_bill_id = bill.id
        budget_lines = BillLineItemService().read_by_bill_id(bill.id)

    suggestion = BillCodingSuggester().suggest(
        vendor_id=int(vendor.id),
        project_id=project_id,
        memo=body.memo,
        exclude_bill_id=exclude_bill_id,
    )

    try:
        apply_budget_check_to_suggestion(
            suggestion,
            budget_lines,
            # Caller-chosen only when the request named it, never from the Bill's lines.
            chosen_project_id=project_id,
            amount_override=body.amount,
        )
    except Exception as error:
        logger.warning(
            "Bill coding budget check failed: %s",
            type(error).__name__,
        )
        suggestion.budget_check = None
        suggestion.budget_remaining = None

    return item_response(_suggestion_to_api_payload(suggestion))
