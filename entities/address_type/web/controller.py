# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.address_type.business.service import AddressTypeService
from entities.auth.business.service import get_current_user_web

router = APIRouter(prefix="/address_type", tags=["web", "address_type"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_address_types(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all address types.
    """
    address_types = AddressTypeService().read_all()
    return templates.TemplateResponse(
        "address_type/list.html",
        {
            "request": request,
            "address_types": [address_type.to_dict() for address_type in address_types],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_address_type(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create address type form.
    """
    return templates.TemplateResponse(
        "address_type/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_address_type(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View an address type.
    """
    address_type = AddressTypeService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "address_type/view.html",
        {
            "request": request,
            "address_type": address_type.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_address_type(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit an address type.
    """
    address_type = AddressTypeService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "address_type/edit.html",
        {
            "request": request,
            "address_type": address_type.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
