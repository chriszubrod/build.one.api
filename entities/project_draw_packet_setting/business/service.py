"""Entity contract — ProjectDrawPacketSetting.   (maintained with the code; checked by tests/test_entity_docstrings.py)

Owns
  dbo.ProjectDrawPacketSetting: one row per Project switching the optional blocks of the
  draw packet's Application for Payment page (contractor statement, owner approval,
  architect certificate, notary) on or off (U-792).
Invariants
  RBAC: Modules.PROJECTS require_module_api; every path assert_can_access_project on the ProjectId.
  Defaults live in business/model.py; a missing row or a NULL switch means the default.
  Concurrency: the first write carries no row_version; every later write must carry the
    current one. A stale / missing / unexpected token writes nothing → RowVersionConflictError (409).
  A write is a full replace: a switch left out of the request resets to the default.
External writes
  None (database only).
Sprocs
  ReadProjectDrawPacketSettingByProjectId
  UpsertProjectDrawPacketSetting
"""
# Python Standard Library Imports
import base64
import binascii
from typing import Optional

# Local Imports
from entities.project_draw_packet_setting.business.model import (
    DrawPacketBlocks,
    ProjectDrawPacketSetting,
    resolve_blocks,
)
from entities.project_draw_packet_setting.persistence.repo import ProjectDrawPacketSettingRepository
from shared.access import assert_can_access_project
from shared.authz import current_user_id
from shared.database import RowVersionConflictError


class ProjectDrawPacketSettingService:
    def __init__(self, repo: Optional[ProjectDrawPacketSettingRepository] = None):
        self.repo = repo or ProjectDrawPacketSettingRepository()

    def read_by_project_id(self, project_id: int) -> Optional[ProjectDrawPacketSetting]:
        assert_can_access_project(project_id)
        return self.repo.read_by_project_id(project_id)

    def blocks_for_project(self, project_id: int) -> DrawPacketBlocks:
        """The resolved switches for a project — the defaults when no row exists."""
        return resolve_blocks(self.read_by_project_id(project_id))

    def upsert(
        self,
        project_id: int,
        *,
        row_version: Optional[str],
        show_contractor_statement: Optional[bool] = None,
        show_owner_approval: Optional[bool] = None,
        show_architect_certificate: Optional[bool] = None,
        show_notary: Optional[bool] = None,
    ) -> ProjectDrawPacketSetting:
        assert_can_access_project(project_id)
        try:
            token = base64.b64decode(row_version, validate=True) if row_version else None
        except (binascii.Error, ValueError) as error:
            raise ValueError("row_version is not valid base64") from error
        if token is not None and len(token) != 8:
            raise ValueError("row_version must decode to 8 bytes")
        written = self.repo.upsert(
            project_id=project_id,
            row_version_bytes=token,
            show_contractor_statement=show_contractor_statement,
            show_owner_approval=show_owner_approval,
            show_architect_certificate=show_architect_certificate,
            show_notary=show_notary,
            created_by_user_id=current_user_id.get(),
        )
        if written is None:
            raise RowVersionConflictError(
                "Draw packet settings were changed by someone else (or the row_version "
                "does not match): reload and retry."
            )
        return written
