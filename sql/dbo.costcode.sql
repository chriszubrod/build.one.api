IF OBJECT_ID('dbo.CostCode', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.CostCode
    (
        [Id] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [Number] NVARCHAR(50) NOT NULL,
        [Name] NVARCHAR(255) NOT NULL,
        [Description] NVARCHAR(255) NULL,
        CONSTRAINT UQ_CostCode_Number UNIQUE (Number)
    );
END;
GO



DROP PROCEDURE IF EXISTS CreateCostCode;
GO

CREATE PROCEDURE CreateCostCode
(
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.CostCode
    (
        [CreatedDatetime],
        [ModifiedDatetime],
        [Number],
        [Name],
        [Description]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Number],
        INSERTED.[Name],
        INSERTED.[Description]
    VALUES
    (
        @Now,
        @Now,
        @Number,
        @Name,
        @Description
    );

    COMMIT TRANSACTION;
END;
GO



DROP PROCEDURE IF EXISTS ReadCostCodes;
GO

CREATE PROCEDURE ReadCostCodes
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
        [Number],
        [Name],
        [Description]
    FROM dbo.CostCode
    ORDER BY [Number] ASC;

    COMMIT TRANSACTION;
END;
GO



DROP PROCEDURE IF EXISTS ReadCostCodeById;
GO

CREATE PROCEDURE ReadCostCodeById
(
    @Id UNIQUEIDENTIFIER
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
        [Number],
        [Name],
        [Description]
    FROM dbo.CostCode
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO



DROP PROCEDURE IF EXISTS ReadCostCodeByPublicId;
GO

CREATE PROCEDURE ReadCostCodeByPublicId
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
        [Number],
        [Name],
        [Description]
    FROM dbo.CostCode
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO




DROP PROCEDURE IF EXISTS ReadCostCodeByNumber;
GO

CREATE PROCEDURE ReadCostCodeByNumber
(
    @Number NVARCHAR(50)
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
        [Number],
        [Name],
        [Description]
    FROM dbo.CostCode
    WHERE [Number] = @Number;

    COMMIT TRANSACTION;
END;
GO




DROP PROCEDURE IF EXISTS UpdateCostCodeById;
GO

CREATE PROCEDURE UpdateCostCodeById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.CostCode
    SET
        [ModifiedDatetime] = @Now,
        [Number] = @Number,
        [Name] = @Name,
        [Description] = @Description
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Number],
        INSERTED.[Name],
        INSERTED.[Description]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO



DROP PROCEDURE IF EXISTS DeleteCostCodeById;
GO

CREATE PROCEDURE DeleteCostCodeById
(
    @Id UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DELETE FROM dbo.CostCode
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Number],
        DELETED.[Name],
        DELETED.[Description]
    WHERE Id = @Id;

    COMMIT TRANSACTION;
END;
GO
