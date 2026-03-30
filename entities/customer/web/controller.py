# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.customer.business.service import CustomerService
from entities.contact.business.service import ContactService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/customer", tags=["web", "customer"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_customers(request: Request, current_user: dict = Depends(require_module_web(Modules.CUSTOMERS))):
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
async def create_customer(request: Request, current_user: dict = Depends(require_module_web(Modules.CUSTOMERS))):
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
async def view_customer(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.CUSTOMERS))):
    """
    View a customer.
    """
    customer = CustomerService().read_by_public_id(public_id=public_id)
    contacts = ContactService().read_by_customer_id(customer_id=customer.id)
    return templates.TemplateResponse(
        "customer/view.html",
        {
            "request": request,
            "customer": customer.to_dict(),
            "contacts": [c.to_dict() for c in contacts],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_customer(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.CUSTOMERS))):
    """
    Edit a customer.
    """
    customer = CustomerService().read_by_public_id(public_id=public_id)
    contacts = ContactService().read_by_customer_id(customer_id=customer.id)
    return templates.TemplateResponse(
        "customer/edit.html",
        {
            "request": request,
            "customer": customer.to_dict(),
            "contacts": [c.to_dict() for c in contacts],
            "parent_entity": "customer",
            "parent_id": customer.id,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
