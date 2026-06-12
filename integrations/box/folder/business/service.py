# Python Standard Library Imports
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.box.base.client import BoxHttpClient
from integrations.box.base.errors import BoxConflictError, BoxError
from integrations.box.base.logger import get_box_logger
from integrations.box.folder.persistence.repo import (
    BoxFolderRepository,
    BoxProjectFolderRepository,
)
from shared.authz import current_user_id

logger = get_box_logger(__name__)


class BoxProjectFolderService:
    """
    Map dbo.Project rows to Box folders (1:1 both ways).

    Folder provisioning itself is manual/out-of-band — an operator creates
    (or picks) the folder in Box, collaborates the service account onto it,
    then maps it here. `map_project` proves visibility with a GET before
    persisting, so a mapping row always points at a folder the service
    account can actually upload into.
    """

    def __init__(
        self,
        folder_repo: Optional[BoxFolderRepository] = None,
        project_folder_repo: Optional[BoxProjectFolderRepository] = None,
    ):
        self.folder_repo = folder_repo or BoxFolderRepository()
        self.project_folder_repo = project_folder_repo or BoxProjectFolderRepository()

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #

    def map_project(self, *, project_public_id: str, box_folder_id: str) -> dict:
        """
        Create the Project → Box-folder mapping.

        1. Resolve dbo.Project by public id (404-style ValueError if missing).
        2. GET folders/{id} via BoxHttpClient — proves the service account can
           see the folder (BoxNotFoundError = not collaborated / wrong id;
           propagated for the router to surface cleanly).
        3. Best-effort metadata stamp on the folder (never fails the mapping).
        4. Persist `[box].[Folder]` (idempotent re-use) + `[box].[ProjectFolder]`.

        Re-mapping the same project to the same folder is idempotent;
        re-mapping to a DIFFERENT folder raises ValueError (delete first).
        """
        # Lazy import: entities.project pulls in the full entity stack — keep
        # integration-module import time light.
        from entities.project.business.service import ProjectService

        project = ProjectService().read_by_public_id(project_public_id)
        if not project:
            raise ValueError(f"Project not found: {project_public_id}")

        box_folder_id = str(box_folder_id).strip()

        existing = self.project_folder_repo.read_by_project_id(project.id)
        if existing:
            if existing.get("box_folder_id") == box_folder_id:
                # Idempotent re-map — return the existing mapping summary.
                existing["project_public_id"] = project_public_id
                return existing
            raise ValueError(
                f"Project {project.name!r} is already mapped to Box folder "
                f"{existing.get('box_folder_id')}; remove that mapping before re-mapping"
            )

        with BoxHttpClient() as client:
            # Visibility proof — BoxNotFoundError/BoxPermissionError propagate.
            folder_data = client.get(
                f"folders/{box_folder_id}",
                operation_name="box.folder.get",
            )
            self._stamp_project_metadata(
                client=client,
                box_folder_id=box_folder_id,
                project_public_id=project_public_id,
            )

        folder_name = folder_data.get("name") or f"folder-{box_folder_id}"
        parent = folder_data.get("parent") or {}
        parent_box_folder_id = str(parent["id"]) if parent.get("id") else None

        folder_row = self.folder_repo.read_by_box_folder_id(box_folder_id)
        if not folder_row:
            folder_row = self.folder_repo.create(
                box_folder_id=box_folder_id,
                name=folder_name,
                parent_box_folder_id=parent_box_folder_id,
            )

        mapping = self.project_folder_repo.create(
            project_id=project.id,
            box_folder_id=folder_row.id,
            created_by_user_id=current_user_id.get(),
        )

        logger.info(
            "box.folder.project_mapped",
            extra={
                "event_name": "box.folder.project_mapped",
                "project_id": project.id,
                "project_public_id": project_public_id,
                "box_folder_id": box_folder_id,
                "folder_name": folder_row.name,
                "mapping_public_id": mapping.public_id,
            },
        )

        return {
            "id": mapping.id,
            "public_id": mapping.public_id,
            "project_id": project.id,
            "project_public_id": project_public_id,
            "project_name": project.name,
            "box_folder_id": box_folder_id,
            "folder_name": folder_row.name,
        }

    def read_mapping_by_project_id(self, project_id: int) -> Optional[dict]:
        """Mapping dict ({"box_folder_id": str, "folder_name": str, ...}) or None."""
        return self.project_folder_repo.read_by_project_id(project_id)

    def list_mappings(self) -> List[dict]:
        """All Project → Box-folder mappings (joined to Folder + Project name)."""
        return self.project_folder_repo.read_all()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _stamp_project_metadata(
        *,
        client: BoxHttpClient,
        box_folder_id: str,
        project_public_id: str,
    ) -> None:
        """
        Best-effort: stamp the project public id onto the folder's global
        metadata so the linkage is visible from the Box side too. Conflict
        (already stamped) and any other BoxError (incl. write gate closed)
        never fail the mapping.
        """
        try:
            client.post(
                f"folders/{box_folder_id}/metadata/global/properties",
                json_body={"buildone_project_public_id": project_public_id},
                operation_name="box.folder.stamp_metadata",
            )
        except BoxConflictError:
            logger.info(
                "box.folder.metadata.already_stamped",
                extra={
                    "event_name": "box.folder.metadata.already_stamped",
                    "box_folder_id": box_folder_id,
                },
            )
        except BoxError as error:
            logger.warning(
                "box.folder.metadata.stamp_failed",
                extra={
                    "event_name": "box.folder.metadata.stamp_failed",
                    "box_folder_id": box_folder_id,
                    "error_class": type(error).__name__,
                },
            )
