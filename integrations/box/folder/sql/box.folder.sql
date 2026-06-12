-- ============================================================================
-- box.Folder + box.ProjectFolder — Box folder registry + Project↔Box-folder
-- 1:1 mapping (Phase 2).
--
-- [box].[Folder] is a local registry of Box folders we know about (the
-- Box-side string id, name, parent). [box].[ProjectFolder] maps exactly one
-- dbo.Project to exactly one [box].[Folder] row (both sides UNIQUE — the
-- qbo.BillBill 1:1 mapping-table pattern).
--
-- RUN ORDER: box.outbox.sql runs FIRST (it owns the CREATE SCHEMA [box]
-- guard). This file assumes the [box] schema exists.
--
-- KEYSPACE DISCIPLINE (see feedback_qbo_dbo_id_keyspaces.md): in this module
-- "BoxFolderId" means TWO different things:
--   - [box].[Folder].[BoxFolderId]        NVARCHAR(32) — Box's string folder id
--   - [box].[ProjectFolder].[BoxFolderId] BIGINT       — FK to [box].[Folder].[Id]
-- Every result set in this file reserves the column name [BoxFolderId]
-- EXCLUSIVELY for the Box string id; the BIGINT FK is always aliased
-- AS [FolderId]. Never return the BIGINT under the name [BoxFolderId].
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'box')
    EXEC('CREATE SCHEMA box AUTHORIZATION dbo;');
GO

IF OBJECT_ID('box.Folder', 'U') IS NULL
BEGIN
CREATE TABLE [box].[Folder]
(
    [Id]                 BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]           UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_BoxFolder_PublicId DEFAULT NEWID(),
    [RowVersion]         ROWVERSION NOT NULL,

    [BoxFolderId]        NVARCHAR(32)  NOT NULL,    -- Box's string folder id
    [Name]               NVARCHAR(255) NOT NULL,
    [ParentBoxFolderId]  NVARCHAR(32)  NULL,        -- Box's string id of the parent folder

    [CreatedDatetime]    DATETIME2(3)  NOT NULL,
    [ModifiedDatetime]   DATETIME2(3)  NULL,

    CONSTRAINT [UQ_BoxFolder_BoxFolderId] UNIQUE ([BoxFolderId])
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BoxFolder_PublicId' AND object_id = OBJECT_ID('box.Folder'))
BEGIN
    CREATE UNIQUE INDEX UQ_BoxFolder_PublicId
        ON [box].[Folder] ([PublicId]);
END
GO


IF OBJECT_ID('box.ProjectFolder', 'U') IS NULL
BEGIN
CREATE TABLE [box].[ProjectFolder]
(
    [Id]                 BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]           UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_BoxProjectFolder_PublicId DEFAULT NEWID(),
    [RowVersion]         ROWVERSION NOT NULL,

    [ProjectId]          BIGINT NOT NULL,           -- FK dbo.Project(Id)
    [BoxFolderId]        BIGINT NOT NULL,           -- FK [box].[Folder](Id) — INTERNAL id, NOT the Box string id
    [CreatedByUserId]    BIGINT NOT NULL CONSTRAINT DF_BoxProjectFolder_CreatedByUserId DEFAULT (17),

    [CreatedDatetime]    DATETIME2(3) NOT NULL,
    [ModifiedDatetime]   DATETIME2(3) NULL,

    CONSTRAINT [UQ_BoxProjectFolder_ProjectId]   UNIQUE ([ProjectId]),
    CONSTRAINT [UQ_BoxProjectFolder_BoxFolderId] UNIQUE ([BoxFolderId])
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BoxProjectFolder_PublicId' AND object_id = OBJECT_ID('box.ProjectFolder'))
BEGIN
    CREATE UNIQUE INDEX UQ_BoxProjectFolder_PublicId
        ON [box].[ProjectFolder] ([PublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BoxProjectFolder_Project')
BEGIN
    ALTER TABLE [box].[ProjectFolder]
    ADD CONSTRAINT [FK_BoxProjectFolder_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BoxProjectFolder_Folder')
BEGIN
    ALTER TABLE [box].[ProjectFolder]
    ADD CONSTRAINT [FK_BoxProjectFolder_Folder] FOREIGN KEY ([BoxFolderId]) REFERENCES [box].[Folder]([Id]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BoxProjectFolder_CreatedByUser')
BEGIN
    ALTER TABLE [box].[ProjectFolder]
    ADD CONSTRAINT [FK_BoxProjectFolder_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


-- ============================================================================
-- CreateBoxFolder
-- Upsert-shaped on the natural key [BoxFolderId] so map_project re-runs are
-- idempotent (a plain INSERT would blow up on UQ_BoxFolder_BoxFolderId).
-- Validate-first / always-COMMIT — no in-proc ROLLBACK. Always returns the
-- (created or refreshed) row.
-- ============================================================================
CREATE OR ALTER PROCEDURE CreateBoxFolder
(
    @BoxFolderId       NVARCHAR(32),
    @Name              NVARCHAR(255),
    @ParentBoxFolderId NVARCHAR(32) = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    -- UPDLOCK+HOLDLOCK serializes concurrent creates of the same folder so
    -- two map_project calls can't race past the EXISTS check into dup INSERTs.
    IF EXISTS (SELECT 1 FROM [box].[Folder] WITH (UPDLOCK, HOLDLOCK) WHERE [BoxFolderId] = @BoxFolderId)
    BEGIN
        UPDATE [box].[Folder]
        SET [ModifiedDatetime]  = @Now,
            [Name]              = @Name,
            -- Preserve-on-NULL: a caller that doesn't know the parent must
            -- not wipe a previously recorded one (SP NULL-overwrite pattern).
            [ParentBoxFolderId] = COALESCE(@ParentBoxFolderId, [ParentBoxFolderId])
        WHERE [BoxFolderId] = @BoxFolderId;
    END
    ELSE
    BEGIN
        INSERT INTO [box].[Folder]
            ([CreatedDatetime], [ModifiedDatetime], [BoxFolderId], [Name], [ParentBoxFolderId])
        VALUES
            (@Now, @Now, @BoxFolderId, @Name, @ParentBoxFolderId);
    END

    COMMIT TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFolderId], [Name], [ParentBoxFolderId]
    FROM [box].[Folder]
    WHERE [BoxFolderId] = @BoxFolderId;
END;
GO


-- ============================================================================
-- ReadBoxFolderById
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxFolderById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFolderId], [Name], [ParentBoxFolderId]
    FROM [box].[Folder]
    WHERE [Id] = @Id;
END;
GO


-- ============================================================================
-- ReadBoxFolderByBoxFolderId (lookup by Box's string folder id)
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxFolderByBoxFolderId
(
    @BoxFolderId NVARCHAR(32)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BoxFolderId], [Name], [ParentBoxFolderId]
    FROM [box].[Folder]
    WHERE [BoxFolderId] = @BoxFolderId;
END;
GO


-- ============================================================================
-- CreateBoxProjectFolder
-- @BoxFolderId here is the INTERNAL [box].[Folder].[Id] (BIGINT), not the
-- Box string id. Returns the new mapping joined to its Folder row so the
-- caller gets the Box string id ([BoxFolderId]) + [FolderName] in one trip.
-- Duplicate Project / duplicate Folder mappings surface as UNIQUE-constraint
-- errors to the service layer (check read_mapping_by_project_id first).
-- ============================================================================
CREATE OR ALTER PROCEDURE CreateBoxProjectFolder
(
    @ProjectId       BIGINT,
    @BoxFolderId     BIGINT,        -- internal [box].[Folder].Id
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    DECLARE @Now   DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @NewId BIGINT;

    BEGIN TRANSACTION;

    INSERT INTO [box].[ProjectFolder]
        ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [BoxFolderId], [CreatedByUserId])
    VALUES
        (@Now, @Now, @ProjectId, @BoxFolderId, COALESCE(@CreatedByUserId, 17));

    SET @NewId = SCOPE_IDENTITY();

    COMMIT TRANSACTION;

    SELECT
        pf.[Id], pf.[PublicId], pf.[RowVersion],
        CONVERT(VARCHAR(19), pf.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pf.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pf.[ProjectId],
        pf.[BoxFolderId]  AS [FolderId],      -- internal [box].[Folder].Id
        pf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName]
    FROM [box].[ProjectFolder] pf
    INNER JOIN [box].[Folder] f ON f.[Id] = pf.[BoxFolderId]
    WHERE pf.[Id] = @NewId;
END;
GO


-- ============================================================================
-- ReadBoxProjectFolderByProjectId
-- The mapping for one Project, joined to its Folder row — [BoxFolderId] is
-- the Box STRING id; the internal FK is aliased [FolderId].
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxProjectFolderByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        pf.[Id], pf.[PublicId], pf.[RowVersion],
        CONVERT(VARCHAR(19), pf.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pf.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pf.[ProjectId],
        pf.[BoxFolderId]  AS [FolderId],      -- internal [box].[Folder].Id
        pf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName]
    FROM [box].[ProjectFolder] pf
    INNER JOIN [box].[Folder] f ON f.[Id] = pf.[BoxFolderId]
    WHERE pf.[ProjectId] = @ProjectId;
END;
GO


-- ============================================================================
-- ReadBoxProjectFolders
-- All mappings joined to Folder + dbo.Project for the admin list surface.
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxProjectFolders
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        pf.[Id], pf.[PublicId], pf.[RowVersion],
        CONVERT(VARCHAR(19), pf.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pf.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pf.[ProjectId],
        pf.[BoxFolderId]  AS [FolderId],      -- internal [box].[Folder].Id
        pf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName],
        p.[Name]          AS [ProjectName],
        p.[PublicId]      AS [ProjectPublicId]
    FROM [box].[ProjectFolder] pf
    INNER JOIN [box].[Folder] f ON f.[Id] = pf.[BoxFolderId]
    INNER JOIN [dbo].[Project] p ON p.[Id] = pf.[ProjectId]
    ORDER BY p.[Name];
END;
GO


-- ============================================================================
-- DeleteBoxProjectFolderById
-- RowVersion-guarded; mismatch (or already gone) → empty result set. The
-- [box].[Folder] registry row is deliberately left in place (cheap, reusable,
-- and other rows may reference the same folder in the future).
-- ============================================================================
CREATE OR ALTER PROCEDURE DeleteBoxProjectFolderById
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

    DELETE FROM [box].[ProjectFolder]
    OUTPUT
        DELETED.[Id], DELETED.[PublicId], DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[BoxFolderId] AS [FolderId],  -- internal [box].[Folder].Id
        DELETED.[CreatedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
