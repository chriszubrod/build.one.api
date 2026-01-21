# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.payment_term.business.service import PaymentTermService
from modules.auth.business.service import get_current_user_web as get_current_payment_term_web

router = APIRouter(prefix="/payment-term", tags=["web", "payment_term"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_payment_terms(request: Request, current_user: dict = Depends(get_current_payment_term_web)):
    """
    List all payment terms.
    """
    payment_terms = PaymentTermService().read_all()
    return templates.TemplateResponse(
        "payment_term/list.html",
        {
            "request": request,
            "payment_terms": payment_terms,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_payment_term(request: Request, current_user: dict = Depends(get_current_payment_term_web)):
    """
    Render create payment term form.
    """
    return templates.TemplateResponse(
        "payment_term/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_payment_term(request: Request, public_id: str, current_user: dict = Depends(get_current_payment_term_web)):
    """
    View a payment term.
    """
    try:
        payment_term = PaymentTermService().read_by_public_id(public_id=public_id)
        if not payment_term:
            raise HTTPException(status_code=404, detail="Payment term not found")
        return templates.TemplateResponse(
            "payment_term/view.html",
            {
                "request": request,
                "payment_term": payment_term.to_dict(),
                "current_user": current_user,
                "current_path": request.url.path,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{public_id}/edit")
async def edit_payment_term(request: Request, public_id: str, current_user: dict = Depends(get_current_payment_term_web)):
    """
    Edit a payment term.
    """
    try:
        payment_term = PaymentTermService().read_by_public_id(public_id=public_id)
        if not payment_term:
            raise HTTPException(status_code=404, detail="Payment term not found")
        return templates.TemplateResponse(
            "payment_term/edit.html",
            {
                "request": request,
                "payment_term": payment_term.to_dict(),
                "current_user": current_user,
                "current_path": request.url.path,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
