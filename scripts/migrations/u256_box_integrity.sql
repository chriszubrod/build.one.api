-- Migration: U-256 — Box integration integrity (registry invalidation, guard
-- scoped read, Excel freshness cache)
-- Purpose: Part C soft-delete registry rows on reconcile 404; Part E scoped
--          ReadBoxFilesByEntity (optional folder+name filters) for idempotency
--          guard; Part F
--          WorkbookEntityPush freshness short-circuit before Box lock.
-- SQL-FIRST, NOT YET APPLIED — do NOT run this file until explicitly approved
-- for prod apply (a deploy is in flight). Self-contained: schema + sproc bodies.
-- Run with: python scripts/run_sql.py scripts/migrations/u256_box_integrity.sql

-- ============================================================================
-- Part C — [box].[File] drift invalidation columns
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('box.File') AND name = 'IsDeleted')
BEGIN
    ALTER TABLE [box].[File]
    ADD [IsDeleted] BIT NOT NULL CONSTRAINT DF_BoxFile_IsDeleted DEFAULT 0;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('box.File') AND name = 'InvalidatedAt')
BEGIN
    ALTER TABLE [box].[File]
    ADD [InvalidatedAt] DATETIME2(3) NULL;
END
GO

-- ============================================================================
-- Part F — [box].[WorkbookEntityPush] freshness cache table
-- ============================================================================
IF OBJECT_ID('box.WorkbookEntityPush', 'U') IS NULL
BEGIN
CREATE TABLE [box].[WorkbookEntityPush]
(
    [Id]               BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]         UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_BoxWorkbookEntityPush_PublicId DEFAULT NEWID(),
    [RowVersion]       ROWVERSION NOT NULL,

    [BoxFileId]        NVARCHAR(32) NOT NULL,
    [EntityType]       NVARCHAR(64) NOT NULL,
    [EntityPublicId]   UNIQUEIDENTIFIER NOT NULL,
    [ContentHash]      NVARCHAR(64) NOT NULL,
    [LastPushedAt]     DATETIME2(3) NOT NULL,

    [CreatedDatetime]  DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    CONSTRAINT [UQ_BoxWorkbookEntityPush_File_Entity] UNIQUE ([BoxFileId], [EntityType], [EntityPublicId])
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BoxWorkbookEntityPush_PublicId' AND object_id = OBJECT_ID('box.WorkbookEntityPush'))
BEGIN
    CREATE UNIQUE INDEX UQ_BoxWorkbookEntityPush_PublicId
        ON [box].[WorkbookEntityPush] ([PublicId]);
END
GO

-- ============================================================================
-- Part C/E — box.File sprocs (UpsertBoxFile, reads, InvalidateBoxFile)
-- Canonical home: integrations/box/file/sql/box.file.sql
-- ============================================================================
CREATE OR ALTER PROCEDURE UpsertBoxFile
(
    @BoxFileId      NVARCHAR(32),
    @BoxFolderId    NVARCHAR(32),
    @Name           NVARCHAR(255),
    @Kind           NVARCHAR(32)     = NULL,
    @EntityType     NVARCHAR(64)     = NULL,
    @EntityPublicId UNIQUEIDENTIFIER = NULL,
    @AttachmentId   BIGINT           = NULL,
    @ProjectId      BIGINT           = NULL,
    @Sha1           NVARCHAR(64)     = NULL,
    @Etag           NVARCHAR(32)     = NULL,
    @FileVersionId  NVARCHAR(32)     = NULL,
    @LastPushedAt   DATETIME2(3)     = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    MERGE [box].[File] WITH (HOLDLOCK) AS t
    USING (SELECT @BoxFileId AS [BoxFileId]) AS s
        ON t.[BoxFileId] = s.[BoxFileId]
    WHEN MATCHED THEN
        UPDATE SET
            t.[ModifiedDatetime] = @Now,
            t.[Name]             = @Name,
            t.[Kind]             = COALESCE(@Kind,           t.[Kind]),
            t.[EntityType]       = COALESCE(@EntityType,     t.[EntityType]),
            t.[EntityPublicId]   = COALESCE(@EntityPublicId, t.[EntityPublicId]),
            t.[AttachmentId]     = COALESCE(@AttachmentId,   t.[AttachmentId]),
            t.[ProjectId]        = COALESCE(@ProjectId,      t.[ProjectId]),
            t.[Sha1]             = COALESCE(@Sha1,           t.[Sha1]),
            t.[Etag]             = COALESCE(@Etag,           t.[Etag]),
            t.[FileVersionId]    = COALESCE(@FileVersionId,  t.[FileVersionId]),
            t.[LastPushedAt]     = COALESCE(@LastPushedAt,   t.[LastPushedAt]),
            t.[IsDeleted]        = 0,
            t.[InvalidatedAt]    = NULL
    WHEN NOT MATCHED THEN
        INSERT (
            [CreatedDatetime], [ModifiedDatetime],
            [BoxFileId], [BoxFolderId], [Name], [Kind],
            [EntityType], [EntityPublicId], [AttachmentId], [ProjectId],
            [Sha1], [Etag], [FileVersionId], [LastPushedAt]
        )
        VALUES (
            @Now, @Now,
            @BoxFileId, @BoxFolderId, @Name, COALESCE(@Kind, 'document'),
            @EntityType, @EntityPublicId, @AttachmentId, @ProjectId,
            @Sha1, @Etag, @FileVersionId, @LastPushedAt
        );

    COMMIT TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFileId], [BoxFolderId], [Name], [Kind],
        [EntityType], [EntityPublicId], [AttachmentId], [ProjectId],
        [Sha1], [Etag], [FileVersionId],
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt],
        [IsDeleted],
        CONVERT(VARCHAR(19), [InvalidatedAt], 120) AS [InvalidatedAt]
    FROM [box].[File]
    WHERE [BoxFileId] = @BoxFileId;
END;
GO

CREATE OR ALTER PROCEDURE ReadBoxFileByBoxFileId
(
    @BoxFileId NVARCHAR(32)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFileId], [BoxFolderId], [Name], [Kind],
        [EntityType], [EntityPublicId], [AttachmentId], [ProjectId],
        [Sha1], [Etag], [FileVersionId],
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt],
        [IsDeleted],
        CONVERT(VARCHAR(19), [InvalidatedAt], 120) AS [InvalidatedAt]
    FROM [box].[File]
    WHERE [BoxFileId] = @BoxFileId;
END;
GO

CREATE OR ALTER PROCEDURE ReadBoxFilesByEntity
(
    @EntityType     NVARCHAR(64),
    @EntityPublicId UNIQUEIDENTIFIER,
    @BoxFolderId    NVARCHAR(32)  = NULL,
    @Name           NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFileId], [BoxFolderId], [Name], [Kind],
        [EntityType], [EntityPublicId], [AttachmentId], [ProjectId],
        [Sha1], [Etag], [FileVersionId],
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt],
        [IsDeleted],
        CONVERT(VARCHAR(19), [InvalidatedAt], 120) AS [InvalidatedAt]
    FROM [box].[File]
    WHERE [EntityType]     = @EntityType
      AND [EntityPublicId] = @EntityPublicId
      AND (@BoxFolderId IS NULL OR [BoxFolderId] = @BoxFolderId)
      AND (@Name IS NULL OR [Name] = @Name)
    ORDER BY [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadRecentBoxFiles
(
    @Limit INT = 25
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@Limit)
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFileId], [BoxFolderId], [Name], [Kind],
        [EntityType], [EntityPublicId], [AttachmentId], [ProjectId],
        [Sha1], [Etag], [FileVersionId],
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt],
        [IsDeleted],
        CONVERT(VARCHAR(19), [InvalidatedAt], 120) AS [InvalidatedAt]
    FROM [box].[File]
    WHERE [IsDeleted] = 0
    ORDER BY COALESCE([LastPushedAt], [CreatedDatetime]) DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE InvalidateBoxFile
(
    @BoxFileId NVARCHAR(32)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [box].[File]
    SET [IsDeleted] = 1, [InvalidatedAt] = @Now, [ModifiedDatetime] = @Now
    WHERE [BoxFileId] = @BoxFileId AND [IsDeleted] = 0;

    COMMIT TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFileId], [BoxFolderId], [Name], [Kind],
        [EntityType], [EntityPublicId], [AttachmentId], [ProjectId],
        [Sha1], [Etag], [FileVersionId],
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt],
        [IsDeleted],
        CONVERT(VARCHAR(19), [InvalidatedAt], 120) AS [InvalidatedAt]
    FROM [box].[File]
    WHERE [BoxFileId] = @BoxFileId;
END;
GO

-- ============================================================================
-- Part F — box.WorkbookEntityPush sprocs
-- Canonical home: integrations/box/excel/sql/box.workbook_entity_push.sql
-- ============================================================================
CREATE OR ALTER PROCEDURE UpsertBoxWorkbookEntityPush
(
    @BoxFileId      NVARCHAR(32),
    @EntityType     NVARCHAR(64),
    @EntityPublicId UNIQUEIDENTIFIER,
    @ContentHash    NVARCHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    MERGE [box].[WorkbookEntityPush] WITH (HOLDLOCK) AS t
    USING (
        SELECT @BoxFileId AS [BoxFileId],
               @EntityType AS [EntityType],
               @EntityPublicId AS [EntityPublicId]
    ) AS s
        ON t.[BoxFileId] = s.[BoxFileId]
       AND t.[EntityType] = s.[EntityType]
       AND t.[EntityPublicId] = s.[EntityPublicId]
    WHEN MATCHED THEN
        UPDATE SET
            t.[ContentHash]      = @ContentHash,
            t.[LastPushedAt]     = @Now,
            t.[ModifiedDatetime] = @Now
    WHEN NOT MATCHED THEN
        INSERT (
            [CreatedDatetime], [ModifiedDatetime],
            [BoxFileId], [EntityType], [EntityPublicId],
            [ContentHash], [LastPushedAt]
        )
        VALUES (
            @Now, @Now,
            @BoxFileId, @EntityType, @EntityPublicId,
            @ContentHash, @Now
        );

    COMMIT TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFileId], [EntityType], [EntityPublicId],
        [ContentHash],
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt]
    FROM [box].[WorkbookEntityPush]
    WHERE [BoxFileId]      = @BoxFileId
      AND [EntityType]     = @EntityType
      AND [EntityPublicId] = @EntityPublicId;
END;
GO

CREATE OR ALTER PROCEDURE ReadBoxWorkbookEntityPush
(
    @BoxFileId      NVARCHAR(32),
    @EntityType     NVARCHAR(64),
    @EntityPublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFileId], [EntityType], [EntityPublicId],
        [ContentHash],
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt]
    FROM [box].[WorkbookEntityPush]
    WHERE [BoxFileId]      = @BoxFileId
      AND [EntityType]     = @EntityType
      AND [EntityPublicId] = @EntityPublicId;
END;
GO

CREATE OR ALTER PROCEDURE ReadBoxWorkbookEntityPushByFile
(
    @BoxFileId NVARCHAR(32)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFileId], [EntityType], [EntityPublicId],
        [ContentHash],
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt]
    FROM [box].[WorkbookEntityPush]
    WHERE [BoxFileId] = @BoxFileId;
END;
GO
