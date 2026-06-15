-- ============================================================================
-- box.ProjectWorkbook — Project ↔ Box-hosted .xlsx workbook 1:1 mapping
-- (Phase 3, Excel-in-Box).
--
-- Maps exactly one dbo.Project to exactly one Box workbook file (the Box-side
-- string file id) plus the name of the single data worksheet we ever edit
-- (the "DETAILS" tab — summary tabs are formula-driven and never touched).
-- The completion flow looks this up to decide whether to enqueue a
-- KIND_UPDATE_EXCEL Box outbox row; the drain handler downloads the .xlsx,
-- edits DETAILS with openpyxl, and uploads a NEW VERSION.
--
-- This is the mapping table only — the registry of pushed file versions lives
-- in [box].[File] (Kind='workbook') / [box].[PushLog] (Phase 1+2 infra).
--
-- RUN ORDER: box.outbox.sql runs FIRST (it owns the CREATE SCHEMA [box]
-- guard). This file assumes the [box] schema exists, but per the Phase-2
-- convention it carries its own idempotent guard too so it can be run
-- standalone via scripts/run_sql.py.
--
-- KEYSPACE DISCIPLINE (see feedback_qbo_dbo_id_keyspaces.md): [BoxFileId] is
-- Box's NVARCHAR(32) STRING file id — never a BIGINT, never aliased over a
-- local PK. There is no internal [box].[File]-FK on this table (unlike
-- [box].[ProjectFolder], whose BoxFolderId is a BIGINT FK to [box].[Folder]);
-- the mapping points straight at the Box string id because the workbook may be
-- collaborated-in rather than created by us.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'box')
    EXEC('CREATE SCHEMA box AUTHORIZATION dbo;');
GO


IF OBJECT_ID('box.ProjectWorkbook', 'U') IS NULL
BEGIN
CREATE TABLE [box].[ProjectWorkbook]
(
    [Id]               BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]         UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_BoxProjectWorkbook_PublicId DEFAULT NEWID(),
    [RowVersion]       ROWVERSION NOT NULL,

    [ProjectId]        BIGINT NOT NULL,             -- FK dbo.Project(Id)
    [BoxFileId]        NVARCHAR(32) NOT NULL,        -- Box's string file id (the .xlsx)
    [WorksheetName]    NVARCHAR(128) NOT NULL CONSTRAINT DF_BoxProjectWorkbook_WorksheetName DEFAULT (N'DETAILS'),
    [CreatedByUserId]  BIGINT NOT NULL CONSTRAINT DF_BoxProjectWorkbook_CreatedByUserId DEFAULT (17),

    [CreatedDatetime]  DATETIME2(3) NOT NULL CONSTRAINT DF_BoxProjectWorkbook_CreatedDatetime DEFAULT SYSUTCDATETIME(),
    [ModifiedDatetime] DATETIME2(3) NOT NULL CONSTRAINT DF_BoxProjectWorkbook_ModifiedDatetime DEFAULT SYSUTCDATETIME(),

    -- One workbook mapping per Project (the 1:1 mapping-table discipline).
    CONSTRAINT [UQ_BoxProjectWorkbook_ProjectId] UNIQUE ([ProjectId])
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BoxProjectWorkbook_PublicId' AND object_id = OBJECT_ID('box.ProjectWorkbook'))
BEGIN
    CREATE UNIQUE INDEX UQ_BoxProjectWorkbook_PublicId
        ON [box].[ProjectWorkbook] ([PublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BoxProjectWorkbook_Project')
BEGIN
    ALTER TABLE [box].[ProjectWorkbook]
    ADD CONSTRAINT [FK_BoxProjectWorkbook_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BoxProjectWorkbook_CreatedByUser')
BEGIN
    ALTER TABLE [box].[ProjectWorkbook]
    ADD CONSTRAINT [FK_BoxProjectWorkbook_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


-- ============================================================================
-- CreateBoxProjectWorkbook
-- Upsert-shaped on the natural key [ProjectId] so map_workbook re-runs are
-- idempotent (CreateBoxFolder pattern) — a re-map of the SAME project refreshes
-- BoxFileId / WorksheetName and returns the existing row rather than blowing up
-- on UQ_BoxProjectWorkbook_ProjectId. (The service still proves Box visibility
-- + rejects re-pointing a project to a DIFFERENT file before calling this; the
-- upsert here keeps the sproc safe under a re-map of an unchanged mapping.)
-- Validate-first / always-COMMIT — no in-proc ROLLBACK. Always returns the
-- (created or refreshed) row joined to dbo.Project.Name.
-- ============================================================================
CREATE OR ALTER PROCEDURE CreateBoxProjectWorkbook
(
    @ProjectId       BIGINT,
    @BoxFileId       NVARCHAR(32),
    @WorksheetName   NVARCHAR(128) = N'DETAILS',
    @CreatedByUserId BIGINT        = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    -- UPDLOCK+HOLDLOCK serializes concurrent maps of the same Project so two
    -- map_workbook calls can't race past the EXISTS check into dup INSERTs.
    IF EXISTS (SELECT 1 FROM [box].[ProjectWorkbook] WITH (UPDLOCK, HOLDLOCK) WHERE [ProjectId] = @ProjectId)
    BEGIN
        UPDATE [box].[ProjectWorkbook]
        SET [ModifiedDatetime] = @Now,
            [BoxFileId]        = @BoxFileId,
            -- Preserve-on-NULL: a caller that omits the worksheet must not wipe
            -- a previously recorded one (SP NULL-overwrite pattern).
            [WorksheetName]    = COALESCE(@WorksheetName, [WorksheetName])
        WHERE [ProjectId] = @ProjectId;
    END
    ELSE
    BEGIN
        INSERT INTO [box].[ProjectWorkbook]
            ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [BoxFileId], [WorksheetName], [CreatedByUserId])
        VALUES
            (@Now, @Now, @ProjectId, @BoxFileId, COALESCE(@WorksheetName, N'DETAILS'), COALESCE(@CreatedByUserId, 17));
    END

    COMMIT TRANSACTION;

    SELECT
        pw.[Id], pw.[PublicId], pw.[RowVersion],
        CONVERT(VARCHAR(19), pw.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pw.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pw.[ProjectId],
        pw.[BoxFileId],                        -- Box's string file id
        pw.[WorksheetName],
        pw.[CreatedByUserId],
        p.[Name]     AS [ProjectName],
        p.[PublicId] AS [ProjectPublicId]
    FROM [box].[ProjectWorkbook] pw
    INNER JOIN [dbo].[Project] p ON p.[Id] = pw.[ProjectId]
    WHERE pw.[ProjectId] = @ProjectId;
END;
GO


-- ============================================================================
-- ReadBoxProjectWorkbookByProjectId
-- The workbook mapping for one Project, joined to dbo.Project. [BoxFileId] is
-- the Box STRING file id. Empty result set = no mapping (the completion flow
-- reads this to decide whether to enqueue an Excel update).
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxProjectWorkbookByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        pw.[Id], pw.[PublicId], pw.[RowVersion],
        CONVERT(VARCHAR(19), pw.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pw.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pw.[ProjectId],
        pw.[BoxFileId],                        -- Box's string file id
        pw.[WorksheetName],
        pw.[CreatedByUserId],
        p.[Name]     AS [ProjectName],
        p.[PublicId] AS [ProjectPublicId]
    FROM [box].[ProjectWorkbook] pw
    INNER JOIN [dbo].[Project] p ON p.[Id] = pw.[ProjectId]
    WHERE pw.[ProjectId] = @ProjectId;
END;
GO


-- ============================================================================
-- ReadBoxProjectWorkbooks
-- All mappings joined to dbo.Project for the admin list surface.
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxProjectWorkbooks
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        pw.[Id], pw.[PublicId], pw.[RowVersion],
        CONVERT(VARCHAR(19), pw.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pw.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pw.[ProjectId],
        pw.[BoxFileId],                        -- Box's string file id
        pw.[WorksheetName],
        pw.[CreatedByUserId],
        p.[Name]     AS [ProjectName],
        p.[PublicId] AS [ProjectPublicId]
    FROM [box].[ProjectWorkbook] pw
    INNER JOIN [dbo].[Project] p ON p.[Id] = pw.[ProjectId]
    ORDER BY p.[Name];
END;
GO


-- ============================================================================
-- DeleteBoxProjectWorkbookById
-- RowVersion-guarded; mismatch (or already gone) → EMPTY result set (the
-- DeleteBoxProjectFolderById / box.folder.sql shape — OUTPUT DELETED.* emits a
-- row only when the WHERE matched, never ROLLBACK in-proc). The service layer
-- reads zero rows as a concurrency conflict.
-- ============================================================================
CREATE OR ALTER PROCEDURE DeleteBoxProjectWorkbookById
(
    @Id         BIGINT,
    @RowVersion BINARY(8)
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the rows.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM [box].[ProjectWorkbook]
    OUTPUT
        DELETED.[Id], DELETED.[PublicId], DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[BoxFileId],                   -- Box's string file id
        DELETED.[WorksheetName],
        DELETED.[CreatedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
