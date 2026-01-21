# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from modules.payment_term.api.schemas import PaymentTermCreate, PaymentTermUpdate
from modules.payment_term.business.service import PaymentTermService
from modules.auth.business.service import get_current_user_api as get_current_payment_term_api

router = APIRouter(prefix="/api/v1", tags=["api", "payment-term"])
service = PaymentTermService()


@router.post("/create/payment-term")
def create_payment_term_router(body: PaymentTermCreate, current_user: dict = Depends(get_current_payment_term_api)):
    """
    Create a new payment term.
    """
    try:
        payment_term = service.create(
            name=body.name,
            description=body.description,
            discount_percent=body.discount_percent,
            discount_days=body.discount_days,
            due_days=body.due_days,
        )
        return payment_term.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/payment-terms")
def get_payment_terms_router(current_user: dict = Depends(get_current_payment_term_api)):
    """
    Read all payment terms.
    """
    try:
        payment_terms = service.read_all()
        return [payment_term.to_dict() for payment_term in payment_terms]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/payment-term/{public_id}")
def get_payment_term_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_payment_term_api)):
    """
    Read a payment term by public ID.
    """
    try:
        payment_term = service.read_by_public_id(public_id=public_id)
        if not payment_term:
            raise HTTPException(status_code=404, detail="Payment term not found")
        return payment_term.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/payment-term/{public_id}")
def update_payment_term_by_public_id_router(public_id: str, body: PaymentTermUpdate, current_user: dict = Depends(get_current_payment_term_api)):
    """
    Update a payment term by public ID.
    """
    try:
        payment_term = service.update_by_public_id(public_id=public_id, payment_term=body)
        if not payment_term:
            raise HTTPException(status_code=404, detail="Payment term not found")
        return payment_term.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/payment-term/{public_id}")
def delete_payment_term_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_payment_term_api)):
    """
    Delete a payment term by public ID.
    """
    try:
        payment_term = service.delete_by_public_id(public_id=public_id)
        if not payment_term:
            raise HTTPException(status_code=404, detail="Payment term not found")
        return payment_term.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
