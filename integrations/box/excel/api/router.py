# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

# Local Imports
from integrations.box.base.errors import (
    BoxError,
    BoxNotFoundError,
    BoxPermissionError,
)
from integrations.box.excel.business.mapping_service import BoxProjectWorkbookService
from shared.api.responses import item_response, list_response, raise_database_error
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1", tags=["api", "box-excel"])


class MapWorkbookRequest(BaseModel):
    project_public_id: str
    box_file_id: str
    worksheet_name: Optional[str] = None


@router.post("/box/map-workbook")
def box_map_workbook_router(
    body: MapWorkbookRequest,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_update")),
):
    """
    Map a dbo.Project to a Box-hosted .xlsx workbook. Verifies the service
    account can see the file (GET files/{id}) before persisting; a 404 from Box
    means the file id is wrong or the service account hasn't been collaborated
    onto it.
    """
    try:
        result = BoxProjectWorkbookService().map_workbook(
            project_public_id=body.project_public_id,
            box_file_id=body.box_file_id,
            worksheet_name=body.worksheet_name or "DETAILS",
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
                f"Box file {body.box_file_id} is not visible to the service "
                f"account (wrong id, or the file has not been collaborated "
                f"with the service account): {error}"
            ),
        )
    except BoxError as error:
        logger.error(f"Box map-workbook failed: {error}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        )
    except HTTPException:
        raise
    except Exception as error:
        # Unique-key violations (project already mapped to another workbook)
        # surface as 422; everything else re-raises.
        raise_database_error(error)


@router.get("/box/project-workbooks")
def box_project_workbooks_router(
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC)),
):
    """List all Project → Box-workbook mappings."""
    return list_response(BoxProjectWorkbookService().list_mappings())
