# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.project_draw_packet_setting.business.model import ProjectDrawPacketSetting
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


def _bit(value) -> Optional[bool]:
    return None if value is None else bool(value)


class ProjectDrawPacketSettingRepository:
    """pyodbc + stored procedures for dbo.ProjectDrawPacketSetting."""

    def _from_db(self, row: pyodbc.Row) -> Optional[ProjectDrawPacketSetting]:
        if not row:
            return None
        try:
            return ProjectDrawPacketSetting(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId is not None else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                created_by_user_id=row.CreatedByUserId,
                project_id=row.ProjectId,
                show_contractor_statement=_bit(row.ShowContractorStatement),
                show_owner_approval=_bit(row.ShowOwnerApproval),
                show_architect_certificate=_bit(row.ShowArchitectCertificate),
                show_notary=_bit(row.ShowNotary),
            )
        except Exception as error:
            logger.error(f"Error mapping project draw packet setting: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> Optional[ProjectDrawPacketSetting]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectDrawPacketSettingByProjectId",
                    params={"ProjectId": project_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading project draw packet setting: {error}")
            raise map_database_error(error)

    def upsert(
        self,
        *,
        project_id: int,
        row_version_bytes: Optional[bytes],
        show_contractor_statement: Optional[bool],
        show_owner_approval: Optional[bool],
        show_architect_certificate: Optional[bool],
        show_notary: Optional[bool],
        created_by_user_id: Optional[int],
    ) -> Optional[ProjectDrawPacketSetting]:
        """None = nothing written (stale / missing / unexpected row version)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertProjectDrawPacketSetting",
                    params={
                        "ProjectId": project_id,
                        "RowVersion": row_version_bytes,
                        "ShowContractorStatement": show_contractor_statement,
                        "ShowOwnerApproval": show_owner_approval,
                        "ShowArchitectCertificate": show_architect_certificate,
                        "ShowNotary": show_notary,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error upserting project draw packet setting: {error}")
            raise map_database_error(error)
