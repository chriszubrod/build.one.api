IF OBJECT_ID('dbo.SubCostCode', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.SubCostCode
    (
        [Id] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWSEQUENTIALID(),
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWSEQUENTIALID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [CostCodeId] UNIQUEIDENTIFIER NOT NULL,
        [Number] NVARCHAR(50) NOT NULL,
        [Name] NVARCHAR(255) NOT NULL,
        [Description] NVARCHAR(255) NULL,
        CONSTRAINT PK_SubCostCode PRIMARY KEY CLUSTERED ([Id] ASC),
        CONSTRAINT UQ_SubCostCode_CostCode_Number UNIQUE ([CostCodeId], [Number])
    );

    ALTER TABLE dbo.SubCostCode
    ADD CONSTRAINT FK_SubCostCode_CostCode
        FOREIGN KEY ([CostCodeId]) REFERENCES dbo.CostCode([Id]);
END;
GO


DROP PROCEDURE IF EXISTS CreateSubCostCode;
GO

CREATE PROCEDURE CreateSubCostCode
(
    @CostCodePublicId UNIQUEIDENTIFIER,
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @CostCodeId UNIQUEIDENTIFIER;

    SELECT @CostCodeId = [Id]
    FROM dbo.CostCode
    WHERE [PublicId] = @CostCodePublicId;

    IF @CostCodeId IS NULL
    BEGIN
        RAISERROR('Parent cost code not found.', 16, 1);
        ROLLBACK TRANSACTION;
        RETURN;
    END;

    DECLARE @Inserted TABLE
    (
        [Id] UNIQUEIDENTIFIER,
        [PublicId] UNIQUEIDENTIFIER,
        [RowVersion] VARBINARY(8),
        [CreatedDatetime] VARCHAR(19),
        [ModifiedDatetime] VARCHAR(19),
        [CostCodeId] UNIQUEIDENTIFIER,
        [Number] NVARCHAR(50),
        [Name] NVARCHAR(255),
        [Description] NVARCHAR(255)
    );

    INSERT INTO dbo.SubCostCode
    (
        [CreatedDatetime],
        [ModifiedDatetime],
        [CostCodeId],
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
        INSERTED.[CostCodeId],
        INSERTED.[Number],
        INSERTED.[Name],
        INSERTED.[Description]
    INTO @Inserted
    VALUES
    (
        @Now,
        @Now,
        @CostCodeId,
        @Number,
        @Name,
        @Description
    );

    SELECT
        i.[Id],
        i.[PublicId],
        i.[RowVersion],
        i.[CreatedDatetime],
        i.[ModifiedDatetime],
        i.[CostCodeId],
        @CostCodePublicId AS [CostCodePublicId],
        c.[Number] AS [CostCodeNumber],
        c.[Name] AS [CostCodeName],
        i.[Number],
        i.[Name],
        i.[Description]
    FROM @Inserted i
    JOIN dbo.CostCode c ON c.[Id] = i.[CostCodeId];

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
        sc.[Id],
        sc.[PublicId],
        sc.[RowVersion],
        CONVERT(VARCHAR(19), sc.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), sc.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        sc.[CostCodeId],
        cc.[PublicId] AS [CostCodePublicId],
        cc.[Number] AS [CostCodeNumber],
        cc.[Name] AS [CostCodeName],
        sc.[Number],
        sc.[Name],
        sc.[Description]
    FROM dbo.SubCostCode sc
    JOIN dbo.CostCode cc ON cc.[Id] = sc.[CostCodeId]
    ORDER BY cc.[Number] ASC, sc.[Number] ASC;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadSubCostCodesByCostCodePublicId;
GO

CREATE PROCEDURE ReadSubCostCodesByCostCodePublicId
(
    @CostCodePublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT
        sc.[Id],
        sc.[PublicId],
        sc.[RowVersion],
        CONVERT(VARCHAR(19), sc.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), sc.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        sc.[CostCodeId],
        cc.[PublicId] AS [CostCodePublicId],
        cc.[Number] AS [CostCodeNumber],
        cc.[Name] AS [CostCodeName],
        sc.[Number],
        sc.[Name],
        sc.[Description]
    FROM dbo.SubCostCode sc
    JOIN dbo.CostCode cc ON cc.[Id] = sc.[CostCodeId]
    WHERE cc.[PublicId] = @CostCodePublicId
    ORDER BY sc.[Number] ASC;

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
        sc.[Id],
        sc.[PublicId],
        sc.[RowVersion],
        CONVERT(VARCHAR(19), sc.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), sc.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        sc.[CostCodeId],
        cc.[PublicId] AS [CostCodePublicId],
        cc.[Number] AS [CostCodeNumber],
        cc.[Name] AS [CostCodeName],
        sc.[Number],
        sc.[Name],
        sc.[Description]
    FROM dbo.SubCostCode sc
    JOIN dbo.CostCode cc ON cc.[Id] = sc.[CostCodeId]
    WHERE sc.[Id] = @Id;

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
        sc.[Id],
        sc.[PublicId],
        sc.[RowVersion],
        CONVERT(VARCHAR(19), sc.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), sc.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        sc.[CostCodeId],
        cc.[PublicId] AS [CostCodePublicId],
        cc.[Number] AS [CostCodeNumber],
        cc.[Name] AS [CostCodeName],
        sc.[Number],
        sc.[Name],
        sc.[Description]
    FROM dbo.SubCostCode sc
    JOIN dbo.CostCode cc ON cc.[Id] = sc.[CostCodeId]
    WHERE sc.[PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadSubCostCodeByNumber;
GO

CREATE PROCEDURE ReadSubCostCodeByNumber
(
    @Number NVARCHAR(50),
    @CostCodePublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT
        sc.[Id],
        sc.[PublicId],
        sc.[RowVersion],
        CONVERT(VARCHAR(19), sc.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), sc.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        sc.[CostCodeId],
        cc.[PublicId] AS [CostCodePublicId],
        cc.[Number] AS [CostCodeNumber],
        cc.[Name] AS [CostCodeName],
        sc.[Number],
        sc.[Name],
        sc.[Description]
    FROM dbo.SubCostCode sc
    JOIN dbo.CostCode cc ON cc.[Id] = sc.[CostCodeId]
    WHERE sc.[Number] = @Number
      AND cc.[PublicId] = @CostCodePublicId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS UpdateSubCostCodeById;
GO

CREATE PROCEDURE UpdateSubCostCodeById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @CostCodePublicId UNIQUEIDENTIFIER,
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @CostCodeId UNIQUEIDENTIFIER;

    SELECT @CostCodeId = [Id]
    FROM dbo.CostCode
    WHERE [PublicId] = @CostCodePublicId;

    IF @CostCodeId IS NULL
    BEGIN
        RAISERROR('Parent cost code not found.', 16, 1);
        ROLLBACK TRANSACTION;
        RETURN;
    END;

    DECLARE @Updated TABLE
    (
        [Id] UNIQUEIDENTIFIER,
        [PublicId] UNIQUEIDENTIFIER,
        [RowVersion] VARBINARY(8),
        [CreatedDatetime] VARCHAR(19),
        [ModifiedDatetime] VARCHAR(19),
        [CostCodeId] UNIQUEIDENTIFIER,
        [Number] NVARCHAR(50),
        [Name] NVARCHAR(255),
        [Description] NVARCHAR(255)
    );

    UPDATE dbo.SubCostCode
    SET
        [ModifiedDatetime] = SYSUTCDATETIME(),
        [CostCodeId] = @CostCodeId,
        [Number] = @Number,
        [Name] = @Name,
        [Description] = @Description
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CostCodeId],
        INSERTED.[Number],
        INSERTED.[Name],
        INSERTED.[Description]
    INTO @Updated
    WHERE [Id] = @Id
      AND [RowVersion] = @RowVersion;

    IF @@ROWCOUNT = 0
    BEGIN
        RAISERROR('RowVersion conflict', 16, 1);
        ROLLBACK TRANSACTION;
        RETURN;
    END;

    SELECT
        u.[Id],
        u.[PublicId],
        u.[RowVersion],
        u.[CreatedDatetime],
        u.[ModifiedDatetime],
        u.[CostCodeId],
        @CostCodePublicId AS [CostCodePublicId],
        cc.[Number] AS [CostCodeNumber],
        cc.[Name] AS [CostCodeName],
        u.[Number],
        u.[Name],
        u.[Description]
    FROM @Updated u
    JOIN dbo.CostCode cc ON cc.[Id] = u.[CostCodeId];

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

    DECLARE @Deleted TABLE
    (
        [Id] UNIQUEIDENTIFIER,
        [PublicId] UNIQUEIDENTIFIER,
        [RowVersion] VARBINARY(8),
        [CreatedDatetime] VARCHAR(19),
        [ModifiedDatetime] VARCHAR(19),
        [CostCodeId] UNIQUEIDENTIFIER,
        [Number] NVARCHAR(50),
        [Name] NVARCHAR(255),
        [Description] NVARCHAR(255)
    );

    DELETE FROM dbo.SubCostCode
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[CostCodeId],
        DELETED.[Number],
        DELETED.[Name],
        DELETED.[Description]
    INTO @Deleted
    WHERE [Id] = @Id;

    SELECT
        d.[Id],
        d.[PublicId],
        d.[RowVersion],
        d.[CreatedDatetime],
        d.[ModifiedDatetime],
        d.[CostCodeId],
        cc.[PublicId] AS [CostCodePublicId],
        cc.[Number] AS [CostCodeNumber],
        cc.[Name] AS [CostCodeName],
        d.[Number],
        d.[Name],
        d.[Description]
    FROM @Deleted d
    JOIN dbo.CostCode cc ON cc.[Id] = d.[CostCodeId];

    COMMIT TRANSACTION;
END;
GO
