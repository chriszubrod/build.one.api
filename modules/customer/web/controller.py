# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.customer.business.service import CustomerService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/customer", tags=["web", "customer"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_customers(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all customers.
    """
    customers = CustomerService().read_all()
    return templates.TemplateResponse(
        "customer/list.html",
        {
            "request": request,
            "customers": customers,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_customer(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create customer form.
    """
    return templates.TemplateResponse(
        "customer/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_customer(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a customer.
    """
    customer = CustomerService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "customer/view.html",
        {
            "request": request,
            "customer": customer.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_customer(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a customer.
    """
    customer = CustomerService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "customer/edit.html",
        {
            "request": request,
            "customer": customer.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
