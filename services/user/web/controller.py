# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from services.user.business.service import UserService
from services.auth.business.service import get_current_user_web

router = APIRouter(prefix="/user", tags=["web", "user"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_users(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all users.
    """
    users = UserService().read_all()
    return templates.TemplateResponse(
        "user/list.html",
        {
            "request": request,
            "users": users,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_user(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create user form.
    """
    return templates.TemplateResponse(
        "user/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_user(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a user.
    """
    user = UserService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "user/view.html",
        {
            "request": request,
            "user": user.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_user(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a user.
    """
    user = UserService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "user/edit.html",
        {
            "request": request,
            "user": user.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
