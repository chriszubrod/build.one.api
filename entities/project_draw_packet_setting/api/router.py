# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.project.business.service import ProjectService
from entities.project_draw_packet_setting.api.schemas import DrawPacketSettingUpdate
from entities.project_draw_packet_setting.business.model import resolve_blocks
from entities.project_draw_packet_setting.business.service import ProjectDrawPacketSettingService
from shared.api.responses import (
    item_response,
    parse_public_id,
    raise_database_error,
    raise_not_found,
    raise_workflow_error,
)
from shared.database import RowVersionConflictError
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

# U-792: which optional blocks print on a project's Application for Payment. API-only
# for now (no web screen). Project-scoped, so the routes gate on Modules.PROJECTS and
# resolve the project through ProjectService's actor-scoped read (an inaccessible
# project reads as missing → 404).
router = APIRouter(prefix="/api/v1", tags=["api", "project_draw_packet_setting"])
service = ProjectDrawPacketSettingService()


def _project_or_404(public_id: str):
    project = ProjectService().read_by_public_id(parse_public_id(public_id, "public_id"))
    if not project:
        raise_not_found("Project")
    return project


def _payload(project, setting) -> dict:
    return {
        "project_public_id": str(project.public_id),
        "row_version": setting.row_version if setting else None,
        "stored": {
            "show_contractor_statement": setting.show_contractor_statement if setting else None,
            "show_owner_approval": setting.show_owner_approval if setting else None,
            "show_architect_certificate": setting.show_architect_certificate if setting else None,
            "show_notary": setting.show_notary if setting else None,
        },
        "effective": resolve_blocks(setting).to_dict(),
    }


@router.get("/get/project/{public_id}/draw-packet-settings")
def get_draw_packet_settings_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_read")),
):
    project = _project_or_404(public_id)
    return item_response(_payload(project, service.read_by_project_id(project.id)))


@router.put("/update/project/{public_id}/draw-packet-settings")
def update_draw_packet_settings_router(
    public_id: str,
    body: DrawPacketSettingUpdate,
    current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_update")),
):
    project = _project_or_404(public_id)
    try:
        setting = service.upsert(
            project.id,
            row_version=body.row_version,
            show_contractor_statement=body.show_contractor_statement,
            show_owner_approval=body.show_owner_approval,
            show_architect_certificate=body.show_architect_certificate,
            show_notary=body.show_notary,
        )
    except RowVersionConflictError as error:
        raise_database_error(error)
    except ValueError as error:
        raise_workflow_error(str(error), "Invalid draw packet settings")
    return item_response(_payload(project, setting))
