IF OBJECT_ID('dbo.SubCostCode', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.SubCostCode
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWSEQUENTIALID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [Number] NVARCHAR(50) NOT NULL,
        [Name] NVARCHAR(255) NOT NULL,
        [Description] NVARCHAR(255) NULL,
        [CostCodeId] UNIQUEIDENTIFIER NOT NULL
    );

END;
GO

DROP TABLE IF EXISTS dbo.SubCostCode;
GO



DROP PROCEDURE IF EXISTS CreateSubCostCode;
GO

CREATE PROCEDURE CreateSubCostCode
(
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL,
    @CostCodeId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.SubCostCode
    (
        [CreatedDatetime],
        [ModifiedDatetime],
        [Number],
        [Name],
        [Description],
        [CostCodeId]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Number],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[CostCodeId]
    VALUES
    (
        @Now,
        @Now,
        @Number,
        @Name,
        @Description,
        @CostCodeId
    );

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadSubCostCodes;
GO

CREATE PROCEDURE ReadSubCostCodes
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
        [Description],
        [CostCodeId]
    FROM dbo.SubCostCode
    ORDER BY [Number] ASC;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadSubCostCodeById;
GO

CREATE PROCEDURE ReadSubCostCodeById
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
        [Description],
        [CostCodeId]
    FROM dbo.SubCostCode
    WHERE [Id] = @Id
    ORDER BY [Number] ASC;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadSubCostCodeByPublicId;
GO

CREATE PROCEDURE ReadSubCostCodeByPublicId
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
        [Description],
        [CostCodeId]
    FROM dbo.SubCostCode
    WHERE [PublicId] = @PublicId
    ORDER BY [Number] ASC;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadSubCostCodeByNumber;
GO

CREATE PROCEDURE ReadSubCostCodeByNumber
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
        [Description],
        [CostCodeId]
    FROM dbo.SubCostCode
    WHERE [Number] = @Number
    ORDER BY [Number] ASC;

    COMMIT TRANSACTION;
END;
GO



DROP PROCEDURE IF EXISTS ReadSubCostCodeByCostCodeId;
GO

CREATE PROCEDURE ReadSubCostCodeByCostCodeId
(
    @CostCodeId UNIQUEIDENTIFIER
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
        [Description],
        [CostCodeId]
    FROM dbo.SubCostCode
    WHERE [CostCodeId] = @CostCodeId
    ORDER BY [Number] ASC;

    COMMIT TRANSACTION;
END;
GO
    


DROP PROCEDURE IF EXISTS UpdateSubCostCodeById;
GO

CREATE PROCEDURE UpdateSubCostCodeById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL,
    @CostCodeId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.SubCostCode
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
        INSERTED.[Description],
        INSERTED.[CostCodeId]
    WHERE [Id] = @Id
      AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteSubCostCodeById;
GO

CREATE PROCEDURE DeleteSubCostCodeById
(
    @Id UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DELETE FROM dbo.SubCostCode
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Number],
        DELETED.[Name],
        DELETED.[Description],
        DELETED.[CostCodeId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
