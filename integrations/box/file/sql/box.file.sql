-- ============================================================================
-- box.File + box.PushLog — Box file registry + append-only push audit log
-- (Phase 2).
--
-- [box].[File] is the local registry of files we have pushed to Box, keyed
-- by Box's string file id. It is the conflict-resolution guard for
-- BoxFileService.push_blob_to_box: on a 409 name collision, the worker looks
-- the conflicting Box file id up here — if the registry row exists AND
-- belongs to the same EntityPublicId, we own the identity and may
-- upload_file_version; otherwise the push dead-letters for human review.
--
-- [box].[PushLog] is an append-only audit log — one row per successful push
-- (new file or new version). No RowVersion / no ModifiedDatetime / no FKs:
-- log rows are never mutated, and a push-log write failure must never fail
-- the push itself.
--
-- RUN ORDER: box.outbox.sql runs FIRST (it owns the CREATE SCHEMA [box]
-- guard). This file assumes the [box] schema exists.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'box')
    EXEC('CREATE SCHEMA box AUTHORIZATION dbo;');
GO

IF OBJECT_ID('box.File', 'U') IS NULL
BEGIN
CREATE TABLE [box].[File]
(
    [Id]                BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_BoxFile_PublicId DEFAULT NEWID(),
    [RowVersion]        ROWVERSION NOT NULL,

    [BoxFileId]         NVARCHAR(32)  NOT NULL,     -- Box's string file id
    [BoxFolderId]       NVARCHAR(32)  NOT NULL,     -- Box's string folder id (parent at last push)
    [Name]              NVARCHAR(255) NOT NULL,     -- sanitized filename as pushed
    [Kind]              NVARCHAR(32)  NOT NULL CONSTRAINT DF_BoxFile_Kind DEFAULT 'document',
                                                    -- doc_kind: 'attachment' | 'receipt' | 'packet' | 'document'

    -- Local identity the file belongs to (nullable — registry can hold
    -- files we discover that aren't bound to a local entity)
    [EntityType]        NVARCHAR(64)  NULL,         -- 'bill' | 'bill_credit' | 'expense' | 'invoice'
    [EntityPublicId]    UNIQUEIDENTIFIER NULL,
    [AttachmentId]      BIGINT        NULL,         -- dbo.Attachment.Id (loose pointer, no FK)
    [ProjectId]         BIGINT        NULL,         -- dbo.Project.Id (loose pointer, no FK)

    -- Box-side version/content fingerprints from the last push
    [Sha1]              NVARCHAR(64)  NULL,
    [Etag]              NVARCHAR(32)  NULL,
    [FileVersionId]     NVARCHAR(32)  NULL,
    [LastPushedAt]      DATETIME2(3)  NULL,

    [CreatedDatetime]   DATETIME2(3)  NOT NULL,
    [ModifiedDatetime]  DATETIME2(3)  NULL,

    CONSTRAINT [UQ_BoxFile_BoxFileId] UNIQUE ([BoxFileId])
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BoxFile_PublicId' AND object_id = OBJECT_ID('box.File'))
BEGIN
    CREATE UNIQUE INDEX UQ_BoxFile_PublicId
        ON [box].[File] ([PublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BoxFile_Entity' AND object_id = OBJECT_ID('box.File'))
BEGIN
    CREATE INDEX IX_BoxFile_Entity
        ON [box].[File] ([EntityType], [EntityPublicId]);
END
GO


IF OBJECT_ID('box.PushLog', 'U') IS NULL
BEGIN
CREATE TABLE [box].[PushLog]
(
    [Id]              BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]        UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_BoxPushLog_PublicId DEFAULT NEWID(),

    [BoxFileId]       NVARCHAR(32) NOT NULL,        -- Box's string file id
    [FileVersionId]   NVARCHAR(32) NULL,
    [Sha1]            NVARCHAR(64) NULL,
    [RequestId]       UNIQUEIDENTIFIER NULL,        -- outbox row's idempotency key
    [OutboxId]        BIGINT       NULL,            -- [box].[Outbox].Id (loose pointer, no FK)
    [ActorUserId]     BIGINT       NOT NULL CONSTRAINT DF_BoxPushLog_ActorUserId DEFAULT (17),

    [CreatedDatetime] DATETIME2(3) NOT NULL
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BoxPushLog_PublicId' AND object_id = OBJECT_ID('box.PushLog'))
BEGIN
    CREATE UNIQUE INDEX UQ_BoxPushLog_PublicId
        ON [box].[PushLog] ([PublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BoxPushLog_BoxFileId' AND object_id = OBJECT_ID('box.PushLog'))
BEGIN
    CREATE INDEX IX_BoxPushLog_BoxFileId
        ON [box].[PushLog] ([BoxFileId]);
END
GO


-- ============================================================================
-- UpsertBoxFile
-- MERGE on the natural key [BoxFileId]. HOLDLOCK closes the classic MERGE
-- upsert race. Preserve-on-NULL (COALESCE) for every nullable column so a
-- partial re-push can never wipe identity/fingerprint values already
-- recorded (SP NULL-overwrite bug pattern). [BoxFolderId] is deliberately
-- NOT updated on match per the Phase-2 contract (files don't move folders
-- via push). Always-COMMIT; final SELECT returns the upserted row.
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
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
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
            t.[LastPushedAt]     = COALESCE(@LastPushedAt,   t.[LastPushedAt])
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
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt]
    FROM [box].[File]
    WHERE [BoxFileId] = @BoxFileId;
END;
GO


-- ============================================================================
-- ReadBoxFileByBoxFileId (the 409 conflict-recovery registry lookup)
-- ============================================================================
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
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt]
    FROM [box].[File]
    WHERE [BoxFileId] = @BoxFileId;
END;
GO


-- ============================================================================
-- ReadBoxFilesByEntity
-- All registry rows for a local entity, newest first.
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxFilesByEntity
(
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
        [BoxFileId], [BoxFolderId], [Name], [Kind],
        [EntityType], [EntityPublicId], [AttachmentId], [ProjectId],
        [Sha1], [Etag], [FileVersionId],
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt]
    FROM [box].[File]
    WHERE [EntityType]     = @EntityType
      AND [EntityPublicId] = @EntityPublicId
    ORDER BY [Id] DESC;
END;
GO


-- ============================================================================
-- ReadRecentBoxFiles
-- Most-recently-pushed registry rows, for the daily reconcile canary's
-- "does this file still exist in Box?" spot-check. Bounded by @Limit so the
-- canary stays well under Box rate limits regardless of registry size.
-- ============================================================================
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
        CONVERT(VARCHAR(19), [LastPushedAt], 120) AS [LastPushedAt]
    FROM [box].[File]
    ORDER BY COALESCE([LastPushedAt], [CreatedDatetime]) DESC, [Id] DESC;
END;
GO


-- ============================================================================
-- CreateBoxPushLog
-- Append-only; one row per successful push (new file OR new version).
-- ============================================================================
CREATE OR ALTER PROCEDURE CreateBoxPushLog
(
    @BoxFileId     NVARCHAR(32),
    @FileVersionId NVARCHAR(32)     = NULL,
    @Sha1          NVARCHAR(64)     = NULL,
    @RequestId     UNIQUEIDENTIFIER = NULL,
    @OutboxId      BIGINT           = NULL,
    @ActorUserId   BIGINT           = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the rows.
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [box].[PushLog]
        ([CreatedDatetime], [BoxFileId], [FileVersionId], [Sha1], [RequestId], [OutboxId], [ActorUserId])
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        INSERTED.[BoxFileId], INSERTED.[FileVersionId], INSERTED.[Sha1],
        INSERTED.[RequestId], INSERTED.[OutboxId], INSERTED.[ActorUserId]
    VALUES
        (@Now, @BoxFileId, @FileVersionId, @Sha1, @RequestId, @OutboxId, COALESCE(@ActorUserId, 17));

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- ReadBoxPushLogsByBoxFileId — push history for one Box file, newest first.
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxPushLogsByBoxFileId
(
    @BoxFileId NVARCHAR(32)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        [BoxFileId], [FileVersionId], [Sha1],
        [RequestId], [OutboxId], [ActorUserId]
    FROM [box].[PushLog]
    WHERE [BoxFileId] = @BoxFileId
    ORDER BY [Id] DESC;
END;
GO
