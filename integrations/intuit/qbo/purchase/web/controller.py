# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

# Local Imports
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from entities.auth.business.service import get_current_user_web as get_current_qbo_purchase_web

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qbo-purchase", tags=["web", "qbo-purchase"])
templates = Jinja2Templates(directory="templates")
service = QboPurchaseService()


@router.get("/needing-update")
async def needing_update_list(
    request: Request,
    realm_id: Optional[str] = None,
    current_user: dict = Depends(get_current_qbo_purchase_web),
):
    """
    List QBO purchase lines with AccountRefName = 'NEED TO UPDATE' and no ExpenseLineItem link.
    """
    rows = service.get_lines_needing_update(realm_id=realm_id)
    return templates.TemplateResponse(
        "qbo-purchase/needing_update.html",
        {
            "request": request,
            "rows": rows,
            "realm_id": realm_id,
            "current_user": current_user,
        },
    )


@router.get("/needing-update/open/{qbo_purchase_id}")
async def open_needing_update(
    request: Request,
    qbo_purchase_id: int,
    current_user: dict = Depends(get_current_qbo_purchase_web),
):
    """
    Ensure Expense and ExpenseLineItems exist for this QBO purchase, then redirect to expense edit.
    """
    purchase = service.read_by_id(id=qbo_purchase_id)
    if not purchase:
        raise HTTPException(status_code=404, detail=f"QBO purchase with id {qbo_purchase_id} not found")
    lines = service.read_lines_by_qbo_purchase_id(qbo_purchase_id=qbo_purchase_id)
    try:
        connector = PurchaseExpenseConnector()
        expense = connector.sync_from_qbo_purchase(purchase, lines)
        return RedirectResponse(url=f"/expense/edit/{expense.public_id}", status_code=303)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error ensuring expense from QBO purchase {qbo_purchase_id}")
        raise HTTPException(status_code=500, detail=str(e))
