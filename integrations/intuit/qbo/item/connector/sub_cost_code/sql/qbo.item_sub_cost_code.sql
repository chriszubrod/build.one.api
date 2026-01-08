DROP TABLE IF EXISTS [qbo].[ItemSubCostCode];
GO

CREATE TABLE [qbo].[ItemSubCostCode]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [SubCostCodeId] BIGINT NOT NULL,
    [QboItemId] BIGINT NOT NULL,
    CONSTRAINT [UQ_ItemSubCostCode_SubCostCodeId] UNIQUE ([SubCostCodeId]),
    CONSTRAINT [UQ_ItemSubCostCode_QboItemId] UNIQUE ([QboItemId])
);
GO


DROP PROCEDURE IF EXISTS CreateItemSubCostCode;
GO

CREATE PROCEDURE CreateItemSubCostCode
(
    @SubCostCodeId BIGINT,
    @QboItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[ItemSubCostCode] ([CreatedDatetime], [ModifiedDatetime], [SubCostCodeId], [QboItemId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[SubCostCodeId],
        INSERTED.[QboItemId]
    VALUES (@Now, @Now, @SubCostCodeId, @QboItemId);

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadItemSubCostCodeById;
GO

CREATE PROCEDURE ReadItemSubCostCodeById
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
        [SubCostCodeId],
        [QboItemId]
    FROM [qbo].[ItemSubCostCode]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadItemSubCostCodeBySubCostCodeId;
GO

CREATE PROCEDURE ReadItemSubCostCodeBySubCostCodeId
(
    @SubCostCodeId BIGINT
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
        [SubCostCodeId],
        [QboItemId]
    FROM [qbo].[ItemSubCostCode]
    WHERE [SubCostCodeId] = @SubCostCodeId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadItemSubCostCodeByQboItemId;
GO

CREATE PROCEDURE ReadItemSubCostCodeByQboItemId
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
        [SubCostCodeId],
        [QboItemId]
    FROM [qbo].[ItemSubCostCode]
    WHERE [QboItemId] = @QboItemId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS UpdateItemSubCostCodeById;
GO

CREATE PROCEDURE UpdateItemSubCostCodeById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @SubCostCodeId BIGINT,
    @QboItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[ItemSubCostCode]
    SET
        [ModifiedDatetime] = @Now,
        [SubCostCodeId] = @SubCostCodeId,
        [QboItemId] = @QboItemId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[SubCostCodeId],
        INSERTED.[QboItemId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteItemSubCostCodeById;
GO

CREATE PROCEDURE DeleteItemSubCostCodeById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[ItemSubCostCode]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[SubCostCodeId],
        DELETED.[QboItemId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


SELECT * FROM qbo.ItemSubCostCode;
