# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.address.business.service import AddressService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/address", tags=["web", "address"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_addresses(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all addresses.
    """
    addresses = AddressService().read_all()
    return templates.TemplateResponse(
        "address/list.html",
        {
            "request": request,
            "addresses": [address.to_dict() for address in addresses],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_address(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create address form.
    """
    return templates.TemplateResponse(
        "address/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_address(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View an address.
    """
    address = AddressService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "address/view.html",
        {
            "request": request,
            "address": address.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_address(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit an address.
    """
    address = AddressService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "address/edit.html",
        {
            "request": request,
            "address": address.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
