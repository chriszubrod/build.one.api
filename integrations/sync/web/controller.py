# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from integrations.sync.business.service import SyncService
from services.auth.business.service import get_current_user_web as get_current_sync_web

router = APIRouter(prefix="/sync", tags=["web", "sync"])
templates = Jinja2Templates(directory="templates/sync")


@router.get("/list")
async def list_syncs(request: Request, current_user: dict = Depends(get_current_sync_web)):
    """
    List all sync records.
    """
    syncs = SyncService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "syncs": syncs,
            "current_user": current_user,
        },
    )


@router.get("/create")
async def create_sync(request: Request, current_user: dict = Depends(get_current_sync_web)):
    """
    Render create sync form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.get("/{public_id}")
async def view_sync(request: Request, public_id: str, current_user: dict = Depends(get_current_sync_web)):
    """
    View a sync record.
    """
    sync = SyncService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "sync": sync.to_dict(),
            "current_user": current_user,
        },
    )


@router.get("/{public_id}/edit")
async def edit_sync(request: Request, public_id: str, current_user: dict = Depends(get_current_sync_web)):
    """
    Edit a sync record.
    """
    sync = SyncService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "sync": sync.to_dict(),
            "current_user": current_user,
        },
    )
