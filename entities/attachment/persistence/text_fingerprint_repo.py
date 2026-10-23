"""Persistence for dbo.AttachmentTextFingerprint (U-723)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import pyodbc

from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class AttachmentTextFingerprintRepository:
    def _call(self, name: str, params: dict[str, Any], *, fetch: str) -> Any:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name=name, params=params)
                return cursor.fetchone() if fetch == "one" else cursor.fetchall()
        except Exception as error:
            logger.error("%s failed: %s", name, type(error).__name__)
            raise map_database_error(error) from error

    def upsert(
        self,
        *,
        attachment_id: int,
        version: str,
        fingerprint: str,
    ) -> bool:
        row = self._call(
            "UpsertAttachmentTextFingerprint",
            {
                "AttachmentId": attachment_id,
                "Version": version,
                "Fingerprint": fingerprint,
            },
            fetch="one",
        )
        return row is not None and getattr(row, "AttachmentId", None) is not None

    def read_by_attachment_id(self, attachment_id: int) -> Optional[pyodbc.Row]:
        return self._call(
            "ReadAttachmentTextFingerprintByAttachmentId",
            {"AttachmentId": attachment_id},
            fetch="one",
        )
