GO

IF OBJECT_ID('qbo.ItemCostCode', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[ItemCostCode]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CostCodeId] BIGINT NOT NULL,
    [QboItemId] BIGINT NOT NULL,
    CONSTRAINT [UQ_ItemCostCode_CostCodeId] UNIQUE ([CostCodeId]),
    CONSTRAINT [UQ_ItemCostCode_QboItemId] UNIQUE ([QboItemId])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateItemCostCode
(
    @CostCodeId BIGINT,
    @QboItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[ItemCostCode] ([CreatedDatetime], [ModifiedDatetime], [CostCodeId], [QboItemId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CostCodeId],
        INSERTED.[QboItemId]
    VALUES (@Now, @Now, @CostCodeId, @QboItemId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadItemCostCodeById
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
        [CostCodeId],
        [QboItemId]
    FROM [qbo].[ItemCostCode]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadItemCostCodeByCostCodeId
(
    @CostCodeId BIGINT
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
        [CostCodeId],
        [QboItemId]
    FROM [qbo].[ItemCostCode]
    WHERE [CostCodeId] = @CostCodeId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadItemCostCodeByQboItemId
(
    @QboItemId BIGINT
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
        [CostCodeId],
        [QboItemId]
    FROM [qbo].[ItemCostCode]
    WHERE [QboItemId] = @QboItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateItemCostCodeById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @CostCodeId BIGINT,
    @QboItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[ItemCostCode]
    SET
        [ModifiedDatetime] = @Now,
        [CostCodeId] = @CostCodeId,
        [QboItemId] = @QboItemId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CostCodeId],
        INSERTED.[QboItemId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteItemCostCodeById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[ItemCostCode]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[CostCodeId],
        DELETED.[QboItemId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

