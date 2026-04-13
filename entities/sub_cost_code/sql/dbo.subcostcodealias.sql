IF OBJECT_ID('dbo.SubCostCodeAlias', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.SubCostCodeAlias
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWSEQUENTIALID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [SubCostCodeId] BIGINT NOT NULL,
        [Alias] NVARCHAR(255) NOT NULL,
        [Source] NVARCHAR(50) NULL
    );
END;
GO


GO

CREATE OR ALTER PROCEDURE CreateSubCostCodeAlias
(
    @SubCostCodeId BIGINT,
    @Alias NVARCHAR(255),
    @Source NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.SubCostCodeAlias
    (
        [CreatedDatetime],
        [ModifiedDatetime],
        [SubCostCodeId],
        [Alias],
        [Source]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[SubCostCodeId],
        INSERTED.[Alias],
        INSERTED.[Source]
    VALUES
    (
        @Now,
        @Now,
        @SubCostCodeId,
        @Alias,
        @Source
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadSubCostCodeAliases
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [SubCostCodeId],
        [Alias],
        [Source]
    FROM dbo.SubCostCodeAlias
    ORDER BY [Alias] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadSubCostCodeAliasById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [SubCostCodeId],
        [Alias],
        [Source]
    FROM dbo.SubCostCodeAlias
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadSubCostCodeAliasByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [SubCostCodeId],
        [Alias],
        [Source]
    FROM dbo.SubCostCodeAlias
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadSubCostCodeAliasBySubCostCodeId
(
    @SubCostCodeId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [SubCostCodeId],
        [Alias],
        [Source]
    FROM dbo.SubCostCodeAlias
    WHERE [SubCostCodeId] = @SubCostCodeId
    ORDER BY [Alias] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadSubCostCodeAliasByAlias
(
    @Alias NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [SubCostCodeId],
        [Alias],
        [Source]
    FROM dbo.SubCostCodeAlias
    WHERE [Alias] = @Alias;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateSubCostCodeAliasById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @SubCostCodeId BIGINT,
    @Alias NVARCHAR(255),
    @Source NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.SubCostCodeAlias
    SET
        [ModifiedDatetime] = @Now,
        [SubCostCodeId] = @SubCostCodeId,
        [Alias] = @Alias,
        [Source] = @Source
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[SubCostCodeId],
        INSERTED.[Alias],
        INSERTED.[Source]
    WHERE [Id] = @Id
      AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteSubCostCodeAliasById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DELETE FROM dbo.SubCostCodeAlias
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[SubCostCodeId],
        DELETED.[Alias],
        DELETED.[Source]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- FK constraint
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_SubCostCodeAlias_SubCostCode')
BEGIN
    ALTER TABLE [dbo].[SubCostCodeAlias] ADD CONSTRAINT [FK_SubCostCodeAlias_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]);
END
GO
