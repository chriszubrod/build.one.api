"""U-701b2: corpus tenant columns + ReadBillAccessProjectIdsForUser on local SQL Server."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests_sqlserver.fixtures.u701a_real_schema import (
    REPO_ROOT,
    exec_sql_file,
    exec_table_ddl_only,
    u701a_real_schema_conn,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)


def _assert_buildone_test(cur) -> None:
    cur.execute("SELECT DB_NAME()")
    assert cur.fetchone()[0] == "buildone_test"


def _apply_gap2_created_by_user_id(cur) -> None:
    """Gap-2 column; not in entity base CREATE TABLE blocks the U-701a fixture applies."""
    cur.execute(
        """
        IF COL_LENGTH('dbo.Bill', 'CreatedByUserId') IS NULL
            ALTER TABLE dbo.[Bill] ADD [CreatedByUserId] BIGINT NULL;
        IF COL_LENGTH('dbo.Project', 'CreatedByUserId') IS NULL
            ALTER TABLE dbo.[Project] ADD [CreatedByUserId] BIGINT NULL;

        UPDATE dbo.[Bill] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
        UPDATE dbo.[Project] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
        """
    )


def _ensure_role_stub(cur) -> None:
    cur.execute(
        """
        IF OBJECT_ID('dbo.[Role]', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.[Role] (
                [Id] BIGINT NOT NULL PRIMARY KEY,
                [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
                [RowVersion] ROWVERSION NOT NULL,
                [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
                [ModifiedDatetime] DATETIME2(3) NULL,
                [Name] NVARCHAR(255) NOT NULL DEFAULT N'test-role'
            );
        END;
        """
    )


def _install_user_project_and_access(cur) -> None:
    up_sql = REPO_ROOT / "entities/user_project/sql/dbo.userproject.sql"
    role_qualifier_sql = (
        REPO_ROOT / "entities/user_project/sql/migrations/003_role_qualifier.sql"
    )
    exec_table_ddl_only(cur, up_sql)
    _ensure_role_stub(cur)
    exec_sql_file(cur, role_qualifier_sql)
    exec_sql_file(cur, up_sql)
    access_sql = REPO_ROOT / "shared/sql/dbo.access_udfs.sql"
    text = access_sql.read_text(encoding="utf-8")
    for batch in text.split("GO"):
        if "FUNCTION dbo.UserCanAccessBill\n" in batch or "FUNCTION dbo.UserCanAccessBill\r\n" in batch:
            cur.execute(batch)
            break
    else:
        raise AssertionError("UserCanAccessBill batch not found in dbo.access_udfs.sql")
    exec_sql_file(cur, REPO_ROOT / "entities/coding_suggestion/sql/dbo.similar_bills_corpus.sql")


@pytest.fixture
def u701b2_schema_conn(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    _assert_buildone_test(cur)
    _apply_gap2_created_by_user_id(cur)
    _install_user_project_and_access(cur)
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM dbo.[User] WHERE [Id] = 100)
            INSERT dbo.[User]([Id]) VALUES (100), (200);
        """
    )
    yield u701a_real_schema_conn


def _seed_bill_with_lines(
    cur,
    *,
    bill_id: int,
    att_id: int,
    company_id: int = 1,
    created_by: int = 17,
    project_ids: list[int],
    bli_start: int,
    blia_start: int,
) -> None:
    cur.execute(
        f"""
        SET IDENTITY_INSERT dbo.[Attachment] ON;
        INSERT dbo.[Attachment](
            [Id], [CreatedDatetime], [Filename], [OriginalFilename], [ContentType],
            [FileSize], [BlobUrl], [CompanyId]
        ) VALUES ({att_id}, SYSUTCDATETIME(), N'f.pdf', N'f.pdf', N'application/pdf', 1, N'https://b/x', {company_id});
        SET IDENTITY_INSERT dbo.[Attachment] OFF;

        SET IDENTITY_INSERT dbo.[Bill] ON;
        INSERT dbo.[Bill](
            [Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId],
            [CompanyId], [CreatedByUserId]
        ) VALUES (
            {bill_id}, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1,
            {company_id}, {created_by}
        );
        SET IDENTITY_INSERT dbo.[Bill] OFF;
        """
    )
    for i, pid in enumerate(project_ids):
        bli_id = bli_start + i
        cur.execute(
            f"""
            SET IDENTITY_INSERT dbo.[BillLineItem] ON;
            INSERT dbo.[BillLineItem](
                [Id], [CreatedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Amount], [IsDraft]
            ) VALUES ({bli_id}, SYSUTCDATETIME(), {bill_id}, 1, {pid}, 100.00, 0);
            SET IDENTITY_INSERT dbo.[BillLineItem] OFF;
            """
        )
        blia_id = blia_start + i
        cur.execute(
            f"""
            SET IDENTITY_INSERT dbo.[BillLineItemAttachment] ON;
            INSERT dbo.[BillLineItemAttachment](
                [Id], [CreatedDatetime], [BillLineItemId], [AttachmentId]
            ) VALUES ({blia_id}, SYSUTCDATETIME(), {bli_id}, {att_id});
            SET IDENTITY_INSERT dbo.[BillLineItemAttachment] OFF;
            """
        )
    cur.execute(
        f"""
        EXEC dbo.UpsertAttachmentTextTokenHash
            @AttachmentId={att_id}, @TokenizerVersion='u701a-v1:abcd',
            @TokenHashes='abc', @TokenCount=1;
        """
    )


def test_corpus_project_ids_csv_and_company_filter(u701b2_schema_conn):
    cur = u701b2_schema_conn.cursor()
    _assert_buildone_test(cur)
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM dbo.[Project] WHERE [Id] = 2)
        BEGIN
            SET IDENTITY_INSERT dbo.[Project] ON;
            INSERT dbo.[Project]([Id], [CreatedDatetime], [Name])
            VALUES (2, SYSUTCDATETIME(), N'test-project-2');
            SET IDENTITY_INSERT dbo.[Project] OFF;
        END;
        """
    )
    _seed_bill_with_lines(
        cur,
        bill_id=80,
        att_id=80,
        company_id=1,
        project_ids=[1, 2],
        bli_start=800,
        blia_start=8000,
    )
    _seed_bill_with_lines(
        cur,
        bill_id=81,
        att_id=81,
        company_id=2,
        project_ids=[1],
        bli_start=810,
        blia_start=8100,
    )
    cur.execute(
        "EXEC dbo.ReadSimilarBillsCorpusRows @TokenizerVersion='u701a-v1:abcd', @CompanyId=1"
    )
    rows = {int(r.BillId): r for r in cur.fetchall()}
    assert set(rows) == {80}
    assert int(rows[80].CreatedByUserId) == 17
    assert rows[80].ProjectIdsCsv == "1,2"

    cur.execute(
        "EXEC dbo.ReadSimilarBillsCorpusRows @TokenizerVersion='u701a-v1:abcd', @CompanyId=NULL"
    )
    assert {int(r.BillId) for r in cur.fetchall()} == {80, 81}


def test_bill_access_project_ids_match_user_can_access_bill(u701b2_schema_conn):
    """The set the similar-bills filter uses must equal UserCanAccessBill's project
    branch: membership counts, being a project's creator does not."""
    cur = u701b2_schema_conn.cursor()
    _assert_buildone_test(cur)
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Project] ON;
        INSERT dbo.[Project]([Id], [CreatedDatetime], [Name], [CreatedByUserId], [CompanyId])
        VALUES (50, SYSUTCDATETIME(), N'creator-only', 100, 1),
               (51, SYSUTCDATETIME(), N'member', 17, 1);
        SET IDENTITY_INSERT dbo.[Project] OFF;

        INSERT dbo.[UserProject]([CreatedDatetime], [UserId], [ProjectId])
        VALUES (SYSUTCDATETIME(), 100, 51);
        """
    )
    cur.execute("EXEC dbo.ReadBillAccessProjectIdsForUser @ActorUserId=100")
    accessible = {int(r.ProjectId) for r in cur.fetchall()}
    assert accessible == {51}

    # Bills by another user (17) on each project: visibility through the project
    # branch must agree with dbo.UserCanAccessBill for user 100.
    _seed_bill_with_lines(cur, bill_id=90, att_id=90, created_by=17, project_ids=[50],
                          bli_start=900, blia_start=9000)
    _seed_bill_with_lines(cur, bill_id=91, att_id=91, created_by=17, project_ids=[51],
                          bli_start=910, blia_start=9100)
    for bill_id, pid in ((90, 50), (91, 51)):
        cur.execute("SELECT dbo.UserCanAccessBill(?, 0, ?)", (100, bill_id))
        assert bool(cur.fetchone()[0]) == (pid in accessible)

    cur.execute("EXEC dbo.ReadBillAccessProjectIdsForUser @ActorUserId=200")
    assert cur.fetchall() == []
