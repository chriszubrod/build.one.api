# Python Standard Library Imports
import base64
import logging
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.attachable.business.model import QboAttachable
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboAttachableRepository:
    """
    Repository for QboAttachable persistence operations.
    """

    def __init__(self):
        """Initialize the QboAttachableRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboAttachable]:
        """
        Convert a database row into a QboAttachable dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboAttachable(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                sync_token=getattr(row, "SyncToken", None),
                realm_id=getattr(row, "RealmId", None),
                file_name=getattr(row, "FileName", None),
                note=getattr(row, "Note", None),
                category=getattr(row, "Category", None),
                content_type=getattr(row, "ContentType", None),
                size=getattr(row, "Size", None),
                file_access_uri=getattr(row, "FileAccessUri", None),
                temp_download_uri=getattr(row, "TempDownloadUri", None),
                entity_ref_type=getattr(row, "EntityRefType", None),
                entity_ref_value=getattr(row, "EntityRefValue", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo attachable mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo attachable mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: str,
        sync_token: Optional[str],
        realm_id: str,
        file_name: Optional[str],
        note: Optional[str],
        category: Optional[str],
        content_type: Optional[str],
        size: Optional[int],
        file_access_uri: Optional[str],
        temp_download_uri: Optional[str],
        entity_ref_type: Optional[str],
        entity_ref_value: Optional[str],
    ) -> QboAttachable:
        """
        Create a new QboAttachable.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "FileName": file_name,
                        "Note": note,
                        "Category": category,
                        "ContentType": content_type,
                        "Size": size,
                        "FileAccessUri": file_access_uri,
                        "TempDownloadUri": temp_download_uri,
                        "EntityRefType": entity_ref_type,
                        "EntityRefValue": entity_ref_value,
                    }
                    logger.debug(f"Calling CreateQboAttachable with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboAttachable",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo attachable did not return a row.")
                        raise map_database_error(Exception("create qbo attachable failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo attachable: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboAttachable]:
        """
        Read a QboAttachable by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAttachableById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo attachable by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboAttachable]:
        """
        Read a QboAttachable by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAttachableByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo attachable by QBO ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboAttachable]:
        """
        Read a QboAttachable by QBO ID and realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAttachableByQboIdAndRealmId",
                        params={"QboId": qbo_id, "RealmId": realm_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo attachable by QBO ID and realm ID: {error}")
            raise map_database_error(error)

    def read_by_entity_ref(
        self, entity_ref_type: str, entity_ref_value: str, realm_id: str
    ) -> List[QboAttachable]:
        """
        Read QboAttachables by entity reference.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAttachablesByEntityRef",
                        params={
                            "EntityRefType": entity_ref_type,
                            "EntityRefValue": entity_ref_value,
                            "RealmId": realm_id,
                        },
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo attachables by entity ref: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> List[QboAttachable]:
        """
        Read all QboAttachables by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAttachablesByRealmId",
                        params={"RealmId": realm_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo attachables by realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        sync_token: Optional[str],
        realm_id: str,
        file_name: Optional[str],
        note: Optional[str],
        category: Optional[str],
        content_type: Optional[str],
        size: Optional[int],
        file_access_uri: Optional[str],
        temp_download_uri: Optional[str],
        entity_ref_type: Optional[str],
        entity_ref_value: Optional[str],
    ) -> Optional[QboAttachable]:
        """
        Update a QboAttachable by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "RowVersion": row_version,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "FileName": file_name,
                        "Note": note,
                        "Category": category,
                        "ContentType": content_type,
                        "Size": size,
                        "FileAccessUri": file_access_uri,
                        "TempDownloadUri": temp_download_uri,
                        "EntityRefType": entity_ref_type,
                        "EntityRefValue": entity_ref_value,
                    }
                    logger.debug(f"Calling UpdateQboAttachableByQboId with QboId: {qbo_id}")
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboAttachableByQboId",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo attachable did not return a row.")
                        raise map_database_error(Exception("update qbo attachable by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo attachable by QBO ID: {error}")
            raise map_database_error(error)

    def delete_by_qbo_id(self, qbo_id: str) -> Optional[QboAttachable]:
        """
        Delete a QboAttachable by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboAttachableByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete qbo attachable by QBO ID: {error}")
            raise map_database_error(error)
