# Python Standard Library Imports
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.box.base.client import BoxHttpClient
from integrations.box.base.errors import BoxConflictError, BoxError
from integrations.box.base.logger import get_box_logger
from integrations.box.excel.persistence.repo import BoxProjectWorkbookRepository
from shared.authz import current_user_id

logger = get_box_logger(__name__)


class BoxProjectWorkbookService:
    """
    Map dbo.Project rows to Box-hosted .xlsx workbooks (1:1).

    Mirrors `BoxProjectFolderService.map_project`: the workbook itself is
    created/picked out-of-band in Box and the service account collaborated onto
    it; `map_workbook` proves visibility with a GET before persisting, so a
    mapping row always points at a file the service account can actually
    download + upload-a-version on at drain time.

    The DETAILS tab of the mapped workbook is the Box mirror of the MS Graph
    Excel sync target — the drain handler downloads the .xlsx, inserts vendor
    cost-line rows into DETAILS with openpyxl, and uploads a new version.
    """

    def __init__(
        self,
        workbook_repo: Optional[BoxProjectWorkbookRepository] = None,
    ):
        self.workbook_repo = workbook_repo or BoxProjectWorkbookRepository()

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #

    def map_workbook(
        self,
        *,
        project_public_id: str,
        box_file_id: str,
        worksheet_name: str = "DETAILS",
    ) -> dict:
        """
        Create the Project → Box-workbook mapping.

        1. Resolve dbo.Project by public id (404-style ValueError if missing).
        2. GET files/{id}?fields=id,name via BoxHttpClient — proves the service
           account can see the workbook (BoxNotFoundError = not collaborated /
           wrong id; propagated for the router to surface cleanly).
        3. Best-effort metadata stamp on the file (never fails the mapping).
        4. Persist `[box].[ProjectWorkbook]` (upsert-shaped on ProjectId).

        Re-mapping the same project to the same file is idempotent;
        re-mapping to a DIFFERENT file raises ValueError (delete first).
        """
        # Lazy import: entities.project pulls in the full entity stack — keep
        # integration-module import time light.
        from entities.project.business.service import ProjectService

        project = ProjectService().read_by_public_id(project_public_id)
        if not project:
            raise ValueError(f"Project not found: {project_public_id}")

        box_file_id = str(box_file_id).strip()
        worksheet_name = (worksheet_name or "DETAILS").strip() or "DETAILS"

        existing = self.workbook_repo.read_by_project_id(project.id)
        if existing:
            if existing.get("box_file_id") == box_file_id:
                # Idempotent re-map — return the existing mapping summary.
                existing["project_public_id"] = project_public_id
                return existing
            raise ValueError(
                f"Project {project.name!r} is already mapped to Box workbook "
                f"{existing.get('box_file_id')}; remove that mapping before re-mapping"
            )

        with BoxHttpClient() as client:
            # Visibility proof — BoxNotFoundError/BoxPermissionError propagate.
            file_data = client.get(
                f"files/{box_file_id}",
                params={"fields": "id,name"},
                operation_name="box.excel.get",
            )
            self._stamp_project_metadata(
                client=client,
                box_file_id=box_file_id,
                project_public_id=project_public_id,
            )

        file_name = file_data.get("name") or f"workbook-{box_file_id}"

        mapping = self.workbook_repo.create(
            project_id=project.id,
            box_file_id=box_file_id,
            worksheet_name=worksheet_name,
            created_by_user_id=current_user_id.get(),
        )

        logger.info(
            "box.excel.project_mapped",
            extra={
                "event_name": "box.excel.project_mapped",
                "project_id": project.id,
                "project_public_id": project_public_id,
                "box_file_id": box_file_id,
                "worksheet_name": worksheet_name,
                "file_name": file_name,
                "mapping_public_id": (mapping or {}).get("public_id"),
            },
        )

        result = dict(mapping or {})
        result["project_public_id"] = project_public_id
        result["project_name"] = result.get("project_name") or project.name
        result["file_name"] = file_name
        return result

    def read_by_project_id(self, project_id: int) -> Optional[dict]:
        """Mapping dict ({"box_file_id": str, "worksheet_name": str, ...}) or None."""
        return self.workbook_repo.read_by_project_id(project_id)

    def list_mappings(self) -> List[dict]:
        """All Project → Box-workbook mappings (joined to dbo.Project name)."""
        return self.workbook_repo.read_all()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _stamp_project_metadata(
        *,
        client: BoxHttpClient,
        box_file_id: str,
        project_public_id: str,
    ) -> None:
        """
        Best-effort: stamp the project public id onto the file's global
        metadata so the linkage is visible from the Box side too. Conflict
        (already stamped) and any other BoxError (incl. write gate closed)
        never fail the mapping.
        """
        try:
            client.post(
                f"files/{box_file_id}/metadata/global/properties",
                json_body={"buildone_project_public_id": project_public_id},
                operation_name="box.excel.stamp_metadata",
            )
        except BoxConflictError:
            logger.info(
                "box.excel.metadata.already_stamped",
                extra={
                    "event_name": "box.excel.metadata.already_stamped",
                    "box_file_id": box_file_id,
                },
            )
        except BoxError as error:
            logger.warning(
                "box.excel.metadata.stamp_failed",
                extra={
                    "event_name": "box.excel.metadata.stamp_failed",
                    "box_file_id": box_file_id,
                    "error_class": type(error).__name__,
                },
            )
