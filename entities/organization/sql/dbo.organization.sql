IF OBJECT_ID('dbo.Organization', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Organization]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(255) NOT NULL,
    [Website] NVARCHAR(255) NULL
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId/ModifiedByUserId param/INSERT-list references below — live since
-- entities/organization/sql/migrations/001_phase0_access_control.sql. No-op against the live
-- schema (columns/FKs already exist there).
IF OBJECT_ID('dbo.Organization', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.Organization') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[Organization] ADD [CreatedByUserId] BIGINT NULL;
END
GO
IF OBJECT_ID('dbo.Organization', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.Organization') AND name = 'ModifiedByUserId')
BEGIN
    ALTER TABLE [dbo].[Organization] ADD [ModifiedByUserId] BIGINT NULL;
END
GO
IF OBJECT_ID('dbo.Organization', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Organization_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[Organization] ADD CONSTRAINT [FK_Organization_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO
IF OBJECT_ID('dbo.Organization', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Organization_ModifiedByUser')
BEGIN
    ALTER TABLE [dbo].[Organization] ADD CONSTRAINT [FK_Organization_ModifiedByUser]
        FOREIGN KEY ([ModifiedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


CREATE OR ALTER PROCEDURE CreateOrganization
(
    @Name NVARCHAR(255),
    @Website NVARCHAR(255),
    @CreatedByUserId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.Organization
        ([CreatedDatetime], [ModifiedDatetime], [Name], [Website],
         [CreatedByUserId], [ModifiedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Website],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    VALUES
        (@Now, @Now, @Name, @Website,
         @CreatedByUserId, COALESCE(@ModifiedByUserId, @CreatedByUserId));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadOrganizations
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Website],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.Organization
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadOrganizationById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Website],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.Organization
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadOrganizationByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Website],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.Organization
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadOrganizationByName
(
    @Name NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Website],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.Organization
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateOrganizationById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(255),
    @Website NVARCHAR(255),
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.Organization
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Website] = @Website,
        [ModifiedByUserId] = @ModifiedByUserId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Website],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteOrganizationById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.Organization
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Website],
        DELETED.[CreatedByUserId],
        DELETED.[ModifiedByUserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- PublicId index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Organization_PublicId' AND object_id = OBJECT_ID('dbo.Organization'))
BEGIN
    CREATE INDEX [IX_Organization_PublicId] ON [dbo].[Organization] ([PublicId]);
END
GO
