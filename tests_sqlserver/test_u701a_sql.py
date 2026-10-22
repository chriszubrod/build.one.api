"""U-701a: AttachmentTextTokenHash SQL on the REAL table definitions (no stub tables)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests_sqlserver.fixtures.u701a_real_schema import (
    _drain_cursor,
    exec_sql_file,
    u701a_real_schema_conn,  # noqa: F401  (pytest fixture)
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN_SQL = REPO_ROOT / "entities/attachment/sql/dbo.attachment_text_token_hash.sql"
CORPUS_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.similar_bills_corpus.sql"
INTAKE_SQL = REPO_ROOT / "entities/bill_intake_prediction/sql/dbo.bill_intake_prediction.sql"

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)


def _insert_attachments(cur, *ids: int) -> None:
    values = ", ".join(
        f"({i}, SYSUTCDATETIME(), N'f.pdf', N'f.pdf', N'application/pdf', 1, N'https://b/x')"
        for i in ids
    )
    cur.execute(
        "SET IDENTITY_INSERT dbo.[Attachment] ON; "
        "INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename], "
        f"[ContentType], [FileSize], [BlobUrl]) VALUES {values}; "
        "SET IDENTITY_INSERT dbo.[Attachment] OFF;"
    )


def _upsert(cur, att_id: int, version: str, hashes: str, count: int) -> None:
    cur.execute(
        "EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=?, @TokenizerVersion=?, "
        "@TokenHashes=?, @TokenCount=?",
        (att_id, version, hashes, count),
    )
    _drain_cursor(cur)


def test_sql_idempotent_twice(u701a_real_schema_conn):
    """All three U-701a SQL files re-apply cleanly over the already-installed real schema."""
    cur = u701a_real_schema_conn.cursor()
    for path in (TOKEN_SQL, CORPUS_SQL, INTAKE_SQL):
        exec_sql_file(cur, path)
        _drain_cursor(cur)
    _insert_attachments(cur, 1)
    _upsert(cur, 1, "u701a-v1:abcd", "abc def", 2)
    _upsert(cur, 1, "u701a-v1:abcd", "abc def ghi", 3)
    cur.execute("EXEC dbo.ReadAttachmentTextTokenHashesSince @Since='2000-01-01'")
    rows = [r for r in cur.fetchall() if int(r.AttachmentId) == 1]
    assert len(rows) == 1 and int(rows[0].TokenCount) == 3
    cur.execute(
        "SELECT COL_LENGTH('dbo.BillIntakePrediction','SimilarPredSubCostCodeId') AS a, "
        "COL_LENGTH('dbo.BillIntakePrediction','SimilarConfidence') AS b"
    )
    cols = cur.fetchone()
    assert cols.a is not None and cols.b is not None
    cur.execute(
        "SELECT COUNT(*) FROM sys.foreign_keys WHERE parent_object_id = OBJECT_ID('dbo.BillIntakePrediction') "
        "AND name = 'FK_BillIntakePrediction_SimilarPredSubCostCode'"
    )
    assert cur.fetchone()[0] == 0  # telemetry table carries no FK (U-701a B2)


def test_orphan_hash_rows_removed_before_fk(u701a_real_schema_conn):
    """Re-applying the token SQL over a table that holds an orphan row removes it, then adds the FK."""
    cur = u701a_real_schema_conn.cursor()
    cur.execute(
        """
        IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AttachmentTextTokenHash_Attachment')
            ALTER TABLE dbo.[AttachmentTextTokenHash] DROP CONSTRAINT [FK_AttachmentTextTokenHash_Attachment];
        INSERT dbo.[AttachmentTextTokenHash]
            ([AttachmentId], [TokenizerVersion], [TokenHashes], [TokenCount], [CreatedDatetime], [ModifiedDatetime])
        VALUES (999999, 'u701a-v1:abcd', 'orphan', 1, SYSUTCDATETIME(), SYSUTCDATETIME());
        """
    )
    exec_sql_file(cur, TOKEN_SQL)
    _drain_cursor(cur)
    cur.execute("SELECT COUNT(*) FROM dbo.[AttachmentTextTokenHash] WHERE [AttachmentId] = 999999")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM sys.foreign_keys WHERE name = 'FK_AttachmentTextTokenHash_Attachment'")
    assert cur.fetchone()[0] == 1


def test_read_hashes_for_attachment_ids_set_based(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    _insert_attachments(cur, 1, 2, 3)
    _upsert(cur, 1, "u701a-v1:aaaa", "a", 1)
    _upsert(cur, 2, "u701a-v1:bbbb", "b", 1)
    _upsert(cur, 3, "u701a-v1:aaaa", "c", 1)
    cur.execute(
        "EXEC dbo.ReadAttachmentTextTokenHashesForAttachmentIds "
        "@AttachmentIds='1,3,2,junk', @TokenizerVersion='u701a-v1:aaaa'"
    )
    assert {int(r.AttachmentId) for r in cur.fetchall()} == {1, 3}


def test_attachment_delete_cascades_hash(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    _insert_attachments(cur, 5)
    _upsert(cur, 5, "u701a-v1:abcd", "abc", 1)
    cur.execute("DELETE FROM dbo.[Attachment] WHERE [Id] = 5")
    cur.execute("SELECT COUNT(*) FROM dbo.[AttachmentTextTokenHash] WHERE [AttachmentId] = 5")
    assert cur.fetchone()[0] == 0
