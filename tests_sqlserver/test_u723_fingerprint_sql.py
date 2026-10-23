"""U-723: AttachmentTextFingerprint SQL on local test SQL Server."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests_sqlserver.fixtures.u701a_real_schema import (
    _drain_cursor,
    exec_sql_file,
    u701a_real_schema_conn,  # noqa: F401
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT_SQL = REPO_ROOT / "entities/attachment/sql/dbo.attachment_text_fingerprint.sql"

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


def _upsert(cur, att_id: int, version: str, fingerprint: str) -> None:
    cur.execute(
        "EXEC dbo.UpsertAttachmentTextFingerprint @AttachmentId=?, @Version=?, @Fingerprint=?",
        (att_id, version, fingerprint),
    )
    _drain_cursor(cur)


def test_sql_idempotent_reapply(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    exec_sql_file(cur, FINGERPRINT_SQL)
    _drain_cursor(cur)
    exec_sql_file(cur, FINGERPRINT_SQL)
    _drain_cursor(cur)
    _insert_attachments(cur, 10)
    fp = "a" * 64
    _upsert(cur, 10, "u723-v1", fp)
    _upsert(cur, 10, "u723-v1", "b" * 64)
    cur.execute("EXEC dbo.ReadAttachmentTextFingerprintByAttachmentId @AttachmentId=10")
    row = cur.fetchone()
    assert row is not None
    assert str(row.Fingerprint).strip() == "b" * 64


def test_orphan_rows_removed_before_fk(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    cur.execute(
        """
        IF OBJECT_ID('dbo.AttachmentTextFingerprint', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.[AttachmentTextFingerprint]
            (
                [AttachmentId] BIGINT NOT NULL PRIMARY KEY,
                [Version] NVARCHAR(16) NOT NULL,
                [Fingerprint] CHAR(64) NOT NULL,
                [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME()
            );
        END
        IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AttachmentTextFingerprint_Attachment')
            ALTER TABLE dbo.[AttachmentTextFingerprint] DROP CONSTRAINT [FK_AttachmentTextFingerprint_Attachment];
        INSERT dbo.[AttachmentTextFingerprint] ([AttachmentId], [Version], [Fingerprint])
        VALUES (888888, 'u723-v1', REPLICATE('c', 64));
        """
    )
    exec_sql_file(cur, FINGERPRINT_SQL)
    _drain_cursor(cur)
    cur.execute("SELECT COUNT(*) FROM dbo.[AttachmentTextFingerprint] WHERE [AttachmentId] = 888888")
    assert cur.fetchone()[0] == 0
    cur.execute(
        "SELECT COUNT(*) FROM sys.foreign_keys WHERE name = 'FK_AttachmentTextFingerprint_Attachment'"
    )
    assert cur.fetchone()[0] == 1


def test_attachment_delete_cascades_fingerprint(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    exec_sql_file(cur, FINGERPRINT_SQL)
    _drain_cursor(cur)
    _insert_attachments(cur, 11)
    _upsert(cur, 11, "u723-v1", "d" * 64)
    cur.execute("DELETE FROM dbo.[Attachment] WHERE [Id] = 11")
    cur.execute("SELECT COUNT(*) FROM dbo.[AttachmentTextFingerprint] WHERE [AttachmentId] = 11")
    assert cur.fetchone()[0] == 0


def test_fingerprint_version_index_exists(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    exec_sql_file(cur, FINGERPRINT_SQL)
    _drain_cursor(cur)
    cur.execute(
        """
        SELECT COUNT(*) FROM sys.indexes
        WHERE name = 'IX_AttachmentTextFingerprint_Fingerprint_Version'
          AND object_id = OBJECT_ID('dbo.AttachmentTextFingerprint')
        """
    )
    assert cur.fetchone()[0] == 1
