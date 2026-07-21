-- ============================================================================
-- box.VendorFolder — Vendor ↔ Box-folder 1:1 mapping (U-106).
--
-- Mirrors [box].[ProjectFolder] shape but keyed on dbo.Vendor. Uniqueness is
-- STRICTER than ProjectFolder (one folder per vendor AND one vendor per folder),
-- matching [ms].[DriveItemVendor].
--
-- RUN ORDER: box.outbox.sql / box.folder.sql run FIRST ([box] schema +
-- [box].[Folder] must exist). This file assumes both are present.
--
-- KEYSPACE DISCIPLINE: same as box.folder.sql — result sets reserve
-- [BoxFolderId] for Box's STRING id; the BIGINT FK is aliased AS [FolderId].
-- ============================================================================

IF OBJECT_ID('box.VendorFolder', 'U') IS NULL
BEGIN
CREATE TABLE [box].[VendorFolder]
(
    [Id]                 BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]           UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_BoxVendorFolder_PublicId DEFAULT NEWID(),
    [RowVersion]         ROWVERSION NOT NULL,

    [VendorId]           BIGINT NOT NULL,           -- FK dbo.Vendor(Id)
    [BoxFolderId]        BIGINT NOT NULL,           -- FK [box].[Folder](Id) — INTERNAL id, NOT the Box string id
    [CreatedByUserId]    BIGINT NULL CONSTRAINT DF_BoxVendorFolder_CreatedByUserId DEFAULT (17),

    [CreatedDatetime]    DATETIME2(3) NOT NULL CONSTRAINT DF_BoxVendorFolder_CreatedDatetime DEFAULT (SYSUTCDATETIME()),
    [ModifiedDatetime]   DATETIME2(3) NULL,

    CONSTRAINT [UQ_BoxVendorFolder_VendorId] UNIQUE ([VendorId]),
    CONSTRAINT [UQ_BoxVendorFolder_BoxFolderId] UNIQUE ([BoxFolderId])
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BoxVendorFolder_PublicId' AND object_id = OBJECT_ID('box.VendorFolder'))
BEGIN
    CREATE UNIQUE INDEX UQ_BoxVendorFolder_PublicId
        ON [box].[VendorFolder] ([PublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BoxVendorFolder_Vendor')
BEGIN
    ALTER TABLE [box].[VendorFolder]
    ADD CONSTRAINT [FK_BoxVendorFolder_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BoxVendorFolder_Folder')
BEGIN
    ALTER TABLE [box].[VendorFolder]
    ADD CONSTRAINT [FK_BoxVendorFolder_Folder] FOREIGN KEY ([BoxFolderId]) REFERENCES [box].[Folder]([Id]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BoxVendorFolder_CreatedByUser')
BEGIN
    ALTER TABLE [box].[VendorFolder]
    ADD CONSTRAINT [FK_BoxVendorFolder_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


-- ============================================================================
-- CreateBoxVendorFolder
-- @BoxFolderId is the INTERNAL [box].[Folder].[Id] (BIGINT), not the Box
-- string id. Returns the new mapping joined to its Folder row.
-- ============================================================================
CREATE OR ALTER PROCEDURE CreateBoxVendorFolder
(
    @VendorId         BIGINT,
    @BoxFolderId      BIGINT,        -- internal [box].[Folder].Id
    @CreatedByUserId  BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @Now   DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @NewId BIGINT;

    BEGIN TRANSACTION;

    INSERT INTO [box].[VendorFolder]
        ([CreatedDatetime], [ModifiedDatetime], [VendorId], [BoxFolderId], [CreatedByUserId])
    VALUES
        (@Now, @Now, @VendorId, @BoxFolderId, COALESCE(@CreatedByUserId, 17));

    SET @NewId = SCOPE_IDENTITY();

    COMMIT TRANSACTION;

    SELECT
        vf.[Id], vf.[PublicId], vf.[RowVersion],
        CONVERT(VARCHAR(19), vf.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), vf.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        vf.[VendorId],
        vf.[BoxFolderId]  AS [FolderId],      -- internal [box].[Folder].Id
        vf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName]
    FROM [box].[VendorFolder] vf
    INNER JOIN [box].[Folder] f ON f.[Id] = vf.[BoxFolderId]
    WHERE vf.[Id] = @NewId;
END;
GO


-- ============================================================================
-- ReadBoxVendorFolderByVendorId
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxVendorFolderByVendorId
(
    @VendorId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        vf.[Id], vf.[PublicId], vf.[RowVersion],
        CONVERT(VARCHAR(19), vf.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), vf.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        vf.[VendorId],
        vf.[BoxFolderId]  AS [FolderId],      -- internal [box].[Folder].Id
        vf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName]
    FROM [box].[VendorFolder] vf
    INNER JOIN [box].[Folder] f ON f.[Id] = vf.[BoxFolderId]
    WHERE vf.[VendorId] = @VendorId;
END;
GO


-- ============================================================================
-- ReadBoxVendorFolderByBoxFolderId
-- @BoxFolderId is the INTERNAL [box].[Folder].[Id] (BIGINT FK on the mapping).
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxVendorFolderByBoxFolderId
(
    @BoxFolderId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        vf.[Id], vf.[PublicId], vf.[RowVersion],
        CONVERT(VARCHAR(19), vf.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), vf.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        vf.[VendorId],
        vf.[BoxFolderId]  AS [FolderId],      -- internal [box].[Folder].Id
        vf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName]
    FROM [box].[VendorFolder] vf
    INNER JOIN [box].[Folder] f ON f.[Id] = vf.[BoxFolderId]
    WHERE vf.[BoxFolderId] = @BoxFolderId;
END;
GO


-- ============================================================================
-- ReadBoxVendorFolders
-- All mappings joined to Folder + dbo.Vendor for the admin list surface.
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxVendorFolders
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        vf.[Id], vf.[PublicId], vf.[RowVersion],
        CONVERT(VARCHAR(19), vf.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), vf.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        vf.[VendorId],
        vf.[BoxFolderId]  AS [FolderId],      -- internal [box].[Folder].Id
        vf.[CreatedByUserId],
        f.[BoxFolderId],                       -- Box's string folder id
        f.[Name]          AS [FolderName],
        v.[Name]          AS [VendorName],
        v.[PublicId]      AS [VendorPublicId]
    FROM [box].[VendorFolder] vf
    INNER JOIN [box].[Folder] f ON f.[Id] = vf.[BoxFolderId]
    INNER JOIN [dbo].[Vendor] v ON v.[Id] = vf.[VendorId]
    ORDER BY v.[Name];
END;
GO


-- ============================================================================
-- DeleteBoxVendorFolderById
-- RowVersion-guarded; mismatch (or already gone) → empty result set.
-- ============================================================================
CREATE OR ALTER PROCEDURE DeleteBoxVendorFolderById
(
    @Id         BIGINT,
    @RowVersion BINARY(8)
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM [box].[VendorFolder]
    OUTPUT
        DELETED.[Id], DELETED.[PublicId], DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        DELETED.[BoxFolderId] AS [FolderId],  -- internal [box].[Folder].Id
        DELETED.[CreatedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- DeleteBoxVendorFolderByVendorId
-- Unlink-by-vendor (no RowVersion guard).
-- ============================================================================
CREATE OR ALTER PROCEDURE DeleteBoxVendorFolderByVendorId
(
    @VendorId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM [box].[VendorFolder]
    OUTPUT
        DELETED.[Id], DELETED.[PublicId], DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        DELETED.[BoxFolderId] AS [FolderId],  -- internal [box].[Folder].Id
        DELETED.[CreatedByUserId]
    WHERE [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;
GO
