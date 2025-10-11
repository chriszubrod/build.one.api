GO

IF OBJECT_ID('dbo.CostCode', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.CostCode
    (
        Id UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_CostCode_Id DEFAULT NEWSEQUENTIALID(),
        PublicId UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_CostCode_PublicId DEFAULT NEWID(),
        RowVersion ROWVERSION NOT NULL,
        CreatedDatetime DATETIME2(3) NOT NULL CONSTRAINT DF_CostCode_Created DEFAULT SYSUTCDATETIME(),
        ModifiedDatetime DATETIME2(3) NOT NULL CONSTRAINT DF_CostCode_Modified DEFAULT SYSUTCDATETIME(),
        Code NVARCHAR(50) NOT NULL,
        Description NVARCHAR(255) NULL,
        Category NVARCHAR(100) NULL,
        CONSTRAINT PK_CostCode PRIMARY KEY (Id),
        CONSTRAINT UQ_CostCode_Code UNIQUE (Code)
    );
END;
GO

DROP PROCEDURE IF EXISTS CreateCostCode;
GO

CREATE PROCEDURE CreateCostCode
(
    @Code NVARCHAR(50),
    @Description NVARCHAR(255) = NULL,
    @Category NVARCHAR(100) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.CostCode
    (
        CreatedDatetime,
        ModifiedDatetime,
        Code,
        Description,
        Category
    )
    OUTPUT
        INSERTED.Id,
        INSERTED.PublicId,
        INSERTED.RowVersion,
        CONVERT(VARCHAR(19), INSERTED.CreatedDatetime, 120) AS CreatedDatetime,
        CONVERT(VARCHAR(19), INSERTED.ModifiedDatetime, 120) AS ModifiedDatetime,
        INSERTED.Code,
        INSERTED.Description,
        INSERTED.Category
    VALUES
    (
        @Now,
        @Now,
        @Code,
        @Description,
        @Category
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
        Id,
        PublicId,
        RowVersion,
        CONVERT(VARCHAR(19), CreatedDatetime, 120) AS CreatedDatetime,
        CONVERT(VARCHAR(19), ModifiedDatetime, 120) AS ModifiedDatetime,
        Code,
        Description,
        Category
    FROM dbo.CostCode
    ORDER BY Code ASC;

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
        Id,
        PublicId,
        RowVersion,
        CONVERT(VARCHAR(19), CreatedDatetime, 120) AS CreatedDatetime,
        CONVERT(VARCHAR(19), ModifiedDatetime, 120) AS ModifiedDatetime,
        Code,
        Description,
        Category
    FROM dbo.CostCode
    WHERE Id = @Id;

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
        Id,
        PublicId,
        RowVersion,
        CONVERT(VARCHAR(19), CreatedDatetime, 120) AS CreatedDatetime,
        CONVERT(VARCHAR(19), ModifiedDatetime, 120) AS ModifiedDatetime,
        Code,
        Description,
        Category
    FROM dbo.CostCode
    WHERE PublicId = @PublicId;

    COMMIT TRANSACTION;
END;
GO

DROP PROCEDURE IF EXISTS ReadCostCodeByCode;
GO

CREATE PROCEDURE ReadCostCodeByCode
(
    @Code NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT
        Id,
        PublicId,
        RowVersion,
        CONVERT(VARCHAR(19), CreatedDatetime, 120) AS CreatedDatetime,
        CONVERT(VARCHAR(19), ModifiedDatetime, 120) AS ModifiedDatetime,
        Code,
        Description,
        Category
    FROM dbo.CostCode
    WHERE Code = @Code;

    COMMIT TRANSACTION;
END;
GO

DROP PROCEDURE IF EXISTS UpdateCostCodeById;
GO

CREATE PROCEDURE UpdateCostCodeById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @Code NVARCHAR(50),
    @Description NVARCHAR(255) = NULL,
    @Category NVARCHAR(100) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.CostCode
    SET
        ModifiedDatetime = @Now,
        Code = @Code,
        Description = @Description,
        Category = @Category
    OUTPUT
        INSERTED.Id,
        INSERTED.PublicId,
        INSERTED.RowVersion,
        CONVERT(VARCHAR(19), INSERTED.CreatedDatetime, 120) AS CreatedDatetime,
        CONVERT(VARCHAR(19), INSERTED.ModifiedDatetime, 120) AS ModifiedDatetime,
        INSERTED.Code,
        INSERTED.Description,
        INSERTED.Category
    WHERE Id = @Id AND RowVersion = @RowVersion;

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
        DELETED.Id,
        DELETED.PublicId,
        DELETED.RowVersion,
        CONVERT(VARCHAR(19), DELETED.CreatedDatetime, 120) AS CreatedDatetime,
        CONVERT(VARCHAR(19), DELETED.ModifiedDatetime, 120) AS ModifiedDatetime,
        DELETED.Code,
        DELETED.Description,
        DELETED.Category
    WHERE Id = @Id;

    COMMIT TRANSACTION;
END;
GO
