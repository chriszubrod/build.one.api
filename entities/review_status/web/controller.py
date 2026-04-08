# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.review_status.business.service import ReviewStatusService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/review-status", tags=["web", "review_status"])
templates = Jinja2Templates(directory="templates/review_status")


@router.get("/list")
async def list_review_statuses(
    request: Request,
    current_user: dict = Depends(require_module_web(Modules.REVIEW_STATUSES)),
):
    """
    List all review statuses.
    """
    review_statuses = ReviewStatusService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "review_statuses": review_statuses,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_review_status(
    request: Request,
    current_user: dict = Depends(require_module_web(Modules.REVIEW_STATUSES)),
):
    """
    Render create review status form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_review_status(
    request: Request,
    public_id: str,
    current_user: dict = Depends(require_module_web(Modules.REVIEW_STATUSES)),
):
    """
    View a review status.
    """
    review_status = ReviewStatusService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "review_status": review_status.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_review_status(
    request: Request,
    public_id: str,
    current_user: dict = Depends(require_module_web(Modules.REVIEW_STATUSES)),
):
    """
    Edit a review status.
    """
    review_status = ReviewStatusService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "review_status": review_status.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
