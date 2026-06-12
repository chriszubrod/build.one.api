# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

# Local Imports
from integrations.box.base.errors import (
    BoxError,
    BoxNotFoundError,
    BoxPermissionError,
)
from integrations.box.folder.business.service import BoxProjectFolderService
from shared.api.responses import item_response, list_response, raise_database_error
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1", tags=["api", "box-folder"])


class MapProjectRequest(BaseModel):
    project_public_id: str
    box_folder_id: str


@router.post("/box/map-project")
def box_map_project_router(
    body: MapProjectRequest,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_update")),
):
    """
    Map a dbo.Project to a Box folder. Verifies the service account can see
    the folder (GET folders/{id}) before persisting; a 404 from Box means the
    folder id is wrong or the service account hasn't been collaborated onto it.
    """
    try:
        result = BoxProjectFolderService().map_project(
            project_public_id=body.project_public_id,
            box_folder_id=body.box_folder_id,
        )
        return item_response(result)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        )
    except (BoxNotFoundError, BoxPermissionError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Box folder {body.box_folder_id} is not visible to the service "
                f"account (wrong id, or the folder has not been collaborated "
                f"with the service account): {error}"
            ),
        )
    except BoxError as error:
        logger.error(f"Box map-project failed: {error}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        )
    except HTTPException:
        raise
    except Exception as error:
        # Unique-key violations (folder already mapped to another project)
        # surface as 422; everything else re-raises.
        raise_database_error(error)


@router.get("/box/project-folders")
def box_project_folders_router(
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC)),
):
    """List all Project → Box-folder mappings."""
    return list_response(BoxProjectFolderService().list_mappings())
