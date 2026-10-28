"""Persistence for dbo.AttachmentTextTokenHash (U-701a)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Sequence

import pyodbc

from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class AttachmentTextTokenHashRepository:
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
        tokenizer_version: str,
        token_hashes: str,
        token_count: int,
    ) -> bool:
        row = self._call(
            "UpsertAttachmentTextTokenHash",
            {
                "AttachmentId": attachment_id,
                "TokenizerVersion": tokenizer_version,
                "TokenHashes": token_hashes,
                "TokenCount": token_count,
            },
            fetch="one",
        )
        return row is not None and getattr(row, "AttachmentId", None) is not None

    def read_since(self, since: datetime) -> Sequence[pyodbc.Row]:
        return self._call("ReadAttachmentTextTokenHashesSince", {"Since": since}, fetch="all")

    def read_corpus_rows(
        self,
        *,
        tokenizer_version: str,
        company_id: Optional[int] = None,
    ) -> Sequence[pyodbc.Row]:
        """Completed-bill corpus; each bill's attachment must hold a hash row of this version."""
        params: dict[str, Any] = {"TokenizerVersion": tokenizer_version}
        if company_id is not None:
            params["CompanyId"] = company_id
        return self._call("ReadSimilarBillsCorpusRows", params, fetch="all")

    def read_for_attachment_ids(
        self,
        attachment_ids: Sequence[int],
        *,
        tokenizer_version: str,
    ) -> Sequence[pyodbc.Row]:
        if not attachment_ids or not tokenizer_version:
            return []
        ids_csv = ",".join(str(int(i)) for i in attachment_ids)
        return self._call(
            "ReadAttachmentTextTokenHashesForAttachmentIds",
            {"AttachmentIds": ids_csv, "TokenizerVersion": tokenizer_version},
            fetch="all",
        )
