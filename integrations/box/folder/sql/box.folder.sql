-- ============================================================================
-- box.Folder + box.ProjectFolder — Box folder registry + Project↔Box-folder
-- 1:1 mapping (Phase 2).
--
-- [box].[Folder] is a local registry of Box folders we know about (the
-- Box-side string id, name, parent). [box].[ProjectFolder] maps a dbo.Project
-- to a [box].[Folder] PER DOCUMENT CLASS (DocClass): a project has at most one
-- folder for 'invoices' (vendor AP docs → "14 - Invoices") and at most one for
-- 'draw_requests' (our customer invoice packets → "15 - Draw Requests"),
-- mirroring the SharePoint per-module split. A [box].[Folder] may be SHARED by
-- several projects (the client files sub-units — guest house, barn, pool, etc.
-- — into the parent property's "14 - Invoices" / "15 - Draw Requests"), so
-- BoxFolderId is deliberately NOT unique on [box].[ProjectFolder]; only
-- (ProjectId, DocClass) is.
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
    -- Which document class files into this folder for the project. Mirrors the
    -- SharePoint per-module split: 'invoices' = vendor AP docs (bills / expenses
    -- / bill credits → "14 - Invoices"); 'draw_requests' = our customer invoice
    -- packets (→ "15 - Draw Requests"). At most one folder per (project, class).
    [DocClass]           NVARCHAR(32) NOT NULL CONSTRAINT DF_BoxProjectFolder_DocClass DEFAULT ('invoices'),
    [CreatedByUserId]    BIGINT NOT NULL CONSTRAINT DF_BoxProjectFolder_CreatedByUserId DEFAULT (17),

    [CreatedDatetime]    DATETIME2(3) NOT NULL,
    [ModifiedDatetime]   DATETIME2(3) NULL,

    -- Only (ProjectId, DocClass) is unique. BoxFolderId is intentionally NOT
    -- unique: sub-unit projects share the parent property's AP / draw folders.
    CONSTRAINT [UQ_BoxProjectFolder_ProjectId_DocClass] UNIQUE ([ProjectId], [DocClass])
);
END
GO


-- ----------------------------------------------------------------------------
-- Migration (existing DBs): add [DocClass]; swap the single-project UNIQUE for
-- the (ProjectId, DocClass) composite; and DROP the BoxFolderId UNIQUE (folders
-- are now shared across sub-unit projects). Idempotent; new rows default to
-- 'invoices'.
-- ----------------------------------------------------------------------------
IF OBJECT_ID('box.ProjectFolder', 'U') IS NOT NULL
   AND COL_LENGTH('box.ProjectFolder', 'DocClass') IS NULL
BEGIN
    ALTER TABLE [box].[ProjectFolder]
    ADD [DocClass] NVARCHAR(32) NOT NULL
        CONSTRAINT DF_BoxProjectFolder_DocClass DEFAULT ('invoices');
END
GO

IF EXISTS (SELECT 1 FROM sys.key_constraints
           WHERE name = 'UQ_BoxProjectFolder_ProjectId'
             AND parent_object_id = OBJECT_ID('box.ProjectFolder'))
BEGIN
    ALTER TABLE [box].[ProjectFolder] DROP CONSTRAINT [UQ_BoxProjectFolder_ProjectId];
END
GO

-- Folders are shared by sub-unit projects (MR2 cluster, OHR2+GUEST, OL+OL-PH,
-- …) → BoxFolderId must NOT be unique. Drop the legacy constraint if present.
IF EXISTS (SELECT 1 FROM sys.key_constraints
           WHERE name = 'UQ_BoxProjectFolder_BoxFolderId'
             AND parent_object_id = OBJECT_ID('box.ProjectFolder'))
BEGIN
    ALTER TABLE [box].[ProjectFolder] DROP CONSTRAINT [UQ_BoxProjectFolder_BoxFolderId];
END
GO

IF OBJECT_ID('box.ProjectFolder', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.key_constraints
                   WHERE name = 'UQ_BoxProjectFolder_ProjectId_DocClass'
                     AND parent_object_id = OBJECT_ID('box.ProjectFolder'))
BEGIN
    ALTER TABLE [box].[ProjectFolder]
    ADD CONSTRAINT [UQ_BoxProjectFolder_ProjectId_DocClass] UNIQUE ([ProjectId], [DocClass]);
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
    @DocClass        NVARCHAR(32) = 'invoices',
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
        ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [BoxFolderId], [DocClass], [CreatedByUserId])
    VALUES
        (@Now, @Now, @ProjectId, @BoxFolderId, COALESCE(@DocClass, 'invoices'), COALESCE(@CreatedByUserId, 17));

    SET @NewId = SCOPE_IDENTITY();

    COMMIT TRANSACTION;

    SELECT
        pf.[Id], pf.[PublicId], pf.[RowVersion],
        CONVERT(VARCHAR(19), pf.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pf.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pf.[ProjectId],
        pf.[BoxFolderId]  AS [FolderId],      -- internal [box].[Folder].Id
        pf.[DocClass],
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

    -- A project can now have multiple rows (one per DocClass). Ordered so the
    -- legacy single-row callers (repo.read_by_project_id → fetchone) get the
    -- 'invoices' AP folder deterministically rather than an arbitrary class.
    SELECT
        pf.[Id], pf.[PublicId], pf.[RowVersion],
        CONVERT(VARCHAR(19), pf.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pf.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pf.[ProjectId],
        pf.[BoxFolderId]  AS [FolderId],      -- internal [box].[Folder].Id
        pf.[DocClass],
        pf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName]
    FROM [box].[ProjectFolder] pf
    INNER JOIN [box].[Folder] f ON f.[Id] = pf.[BoxFolderId]
    WHERE pf.[ProjectId] = @ProjectId
    ORDER BY CASE WHEN pf.[DocClass] = 'invoices' THEN 0 ELSE 1 END, pf.[Id];
END;
GO


-- ============================================================================
-- ReadBoxProjectFolderByProjectIdAndDocClass
-- The mapping for one (Project, DocClass) — the routing-aware lookup the
-- completion enqueues use ('invoices' for bill/expense/credit attachments,
-- 'draw_requests' for invoice packets). [BoxFolderId] is the Box STRING id.
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxProjectFolderByProjectIdAndDocClass
(
    @ProjectId BIGINT,
    @DocClass  NVARCHAR(32)
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
        pf.[DocClass],
        pf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName]
    FROM [box].[ProjectFolder] pf
    INNER JOIN [box].[Folder] f ON f.[Id] = pf.[BoxFolderId]
    WHERE pf.[ProjectId] = @ProjectId AND pf.[DocClass] = @DocClass;
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
        pf.[DocClass],
        pf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName],
        p.[Name]          AS [ProjectName],
        p.[PublicId]      AS [ProjectPublicId]
    FROM [box].[ProjectFolder] pf
    INNER JOIN [box].[Folder] f ON f.[Id] = pf.[BoxFolderId]
    INNER JOIN [dbo].[Project] p ON p.[Id] = pf.[ProjectId]
    ORDER BY p.[Name], pf.[DocClass];
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
        DELETED.[DocClass],
        DELETED.[CreatedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
