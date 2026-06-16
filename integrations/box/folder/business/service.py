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


# Document-class routing (mirrors the SharePoint per-module folder split).
# Vendor AP documents (bills / expenses / bill credits) file to the project's
# "14 - Invoices" folder; our customer invoice packets file to "15 - Draw
# Requests". A project has at most one Box folder per class.
DOC_CLASS_INVOICES = "invoices"
DOC_CLASS_DRAW_REQUESTS = "draw_requests"
VALID_DOC_CLASSES = (DOC_CLASS_INVOICES, DOC_CLASS_DRAW_REQUESTS)


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

    def map_project(
        self,
        *,
        project_public_id: str,
        box_folder_id: str,
        doc_class: str = DOC_CLASS_INVOICES,
    ) -> dict:
        """
        Create the (Project, doc_class) → Box-folder mapping.

        1. Resolve dbo.Project by public id (404-style ValueError if missing).
        2. GET folders/{id} via BoxHttpClient — proves the service account can
           see the folder (BoxNotFoundError = not collaborated / wrong id;
           propagated for the router to surface cleanly).
        3. Best-effort metadata stamp on the folder (never fails the mapping).
        4. Persist `[box].[Folder]` (idempotent re-use) + `[box].[ProjectFolder]`.

        `doc_class` routes documents: 'invoices' (bill/expense/credit → the
        project's "14 - Invoices" folder) or 'draw_requests' (invoice packets →
        "15 - Draw Requests"). A project can hold one mapping per class.

        Re-mapping the same (project, class) to the same folder is idempotent;
        re-mapping that pair to a DIFFERENT folder raises ValueError (delete
        first). A different class for the same project is an independent row.
        """
        doc_class = (doc_class or DOC_CLASS_INVOICES).strip().lower()
        if doc_class not in VALID_DOC_CLASSES:
            raise ValueError(
                f"Invalid doc_class {doc_class!r}; expected one of {VALID_DOC_CLASSES}"
            )

        # Lazy import: entities.project pulls in the full entity stack — keep
        # integration-module import time light.
        from entities.project.business.service import ProjectService

        project = ProjectService().read_by_public_id(project_public_id)
        if not project:
            raise ValueError(f"Project not found: {project_public_id}")

        box_folder_id = str(box_folder_id).strip()

        existing = self.project_folder_repo.read_by_project_id_and_doc_class(
            project.id, doc_class
        )
        if existing:
            if existing.get("box_folder_id") == box_folder_id:
                # Idempotent re-map — return the existing mapping summary.
                existing["project_public_id"] = project_public_id
                return existing
            raise ValueError(
                f"Project {project.name!r} is already mapped to Box folder "
                f"{existing.get('box_folder_id')} for doc_class {doc_class!r}; "
                f"remove that mapping before re-mapping"
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
            doc_class=doc_class,
            created_by_user_id=current_user_id.get(),
        )

        logger.info(
            "box.folder.project_mapped",
            extra={
                "event_name": "box.folder.project_mapped",
                "project_id": project.id,
                "project_public_id": project_public_id,
                "box_folder_id": box_folder_id,
                "doc_class": doc_class,
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
            "doc_class": doc_class,
            "folder_name": folder_row.name,
        }

    def read_mapping_by_project_id(self, project_id: int) -> Optional[dict]:
        """
        The project's mapping (AP/'invoices' folder when multiple classes
        exist). Prefer `read_mapping_by_project_id_and_class` for routing.
        """
        return self.project_folder_repo.read_by_project_id(project_id)

    def read_mapping_by_project_id_and_class(
        self, project_id: int, doc_class: str
    ) -> Optional[dict]:
        """Routing-aware mapping for one (project, doc_class), or None."""
        return self.project_folder_repo.read_by_project_id_and_doc_class(
            project_id, doc_class
        )

    def list_mappings(self) -> List[dict]:
        """All Project → Box-folder mappings (joined to Folder + Project name)."""
        return self.project_folder_repo.read_all()

    def unmap_project(self, *, project_public_id: str, doc_class: str) -> dict:
        """
        Remove the (Project, doc_class) → Box-folder mapping so it can be
        re-mapped to a different folder (the recovery path the `map_project`
        "remove that mapping before re-mapping" error points at). The
        `[box].[Folder]` registry row is intentionally left in place (cheap,
        reusable, and possibly shared by other projects).

        Raises ValueError if the project or the (project, class) mapping does
        not exist.
        """
        doc_class = (doc_class or DOC_CLASS_INVOICES).strip().lower()
        if doc_class not in VALID_DOC_CLASSES:
            raise ValueError(
                f"Invalid doc_class {doc_class!r}; expected one of {VALID_DOC_CLASSES}"
            )

        from entities.project.business.service import ProjectService

        project = ProjectService().read_by_public_id(project_public_id)
        if not project:
            raise ValueError(f"Project not found: {project_public_id}")

        existing = self.project_folder_repo.read_by_project_id_and_doc_class(
            project.id, doc_class
        )
        if not existing:
            raise ValueError(
                f"No {doc_class!r} Box-folder mapping for project {project.name!r}"
            )

        self.project_folder_repo.delete_by_id(
            id=existing["id"], row_version=existing["row_version"]
        )
        logger.info(
            "box.folder.project_unmapped",
            extra={
                "event_name": "box.folder.project_unmapped",
                "project_id": project.id,
                "project_public_id": project_public_id,
                "doc_class": doc_class,
                "box_folder_id": existing.get("box_folder_id"),
                "mapping_public_id": existing.get("public_id"),
            },
        )
        existing["project_public_id"] = project_public_id
        return existing

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
