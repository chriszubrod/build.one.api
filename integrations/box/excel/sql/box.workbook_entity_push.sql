-- ============================================================================
-- box.WorkbookEntityPush — freshness cache for Box Excel DETAILS sync
-- (U-256, Phase 3 Excel-in-Box).
--
-- One row per (BoxFileId, EntityType, EntityPublicId) tuple records the
-- content hash of the last successful push so BoxExcelUpdateService can
-- short-circuit BEFORE acquiring the cross-process lock or calling Box when
-- nothing changed. The col-Z dedup inside workbook_editor remains the
-- correctness backstop; this table is a performance/cost gate only.
--
-- RUN ORDER: box.outbox.sql runs FIRST (it owns the CREATE SCHEMA [box]
-- guard). This file assumes the [box] schema exists, but per the Phase-2
-- convention it carries its own idempotent guard too so it can be run
-- standalone via scripts/run_sql.py.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'box')
    EXEC('CREATE SCHEMA box AUTHORIZATION dbo;');
GO


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
-- UpsertBoxWorkbookEntityPush
-- MERGE on (BoxFileId, EntityType, EntityPublicId). HOLDLOCK closes the
-- classic MERGE upsert race. Always-COMMIT; final SELECT returns the row.
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
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
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


-- ============================================================================
-- ReadBoxWorkbookEntityPush
-- Single-row lookup by the natural (BoxFileId, EntityType, EntityPublicId)
-- key. Empty result set = no prior push recorded for this entity/workbook.
-- ============================================================================
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


-- ============================================================================
-- ReadBoxWorkbookEntityPushByFile
-- All freshness-cache rows for one workbook. Used by the insert-path
-- pre-check to batch-lookup cached hashes in one round trip.
-- ============================================================================
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
