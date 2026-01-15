DROP TABLE IF EXISTS [qbo].[BillLineItemBillLine];
GO

CREATE TABLE [qbo].[BillLineItemBillLine]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BillLineItemId] BIGINT NOT NULL,
    [QboBillLineId] BIGINT NOT NULL,
    CONSTRAINT [UQ_BillLineItemBillLine_BillLineItemId] UNIQUE ([BillLineItemId]),
    CONSTRAINT [UQ_BillLineItemBillLine_QboBillLineId] UNIQUE ([QboBillLineId])
);
GO


DROP PROCEDURE IF EXISTS CreateBillLineItemBillLine;
GO

CREATE PROCEDURE CreateBillLineItemBillLine
(
    @BillLineItemId BIGINT,
    @QboBillLineId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[BillLineItemBillLine] ([CreatedDatetime], [ModifiedDatetime], [BillLineItemId], [QboBillLineId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillLineItemId],
        INSERTED.[QboBillLineId]
    VALUES (@Now, @Now, @BillLineItemId, @QboBillLineId);

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadBillLineItemBillLineById;
GO

CREATE PROCEDURE ReadBillLineItemBillLineById
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
        [BillLineItemId],
        [QboBillLineId]
    FROM [qbo].[BillLineItemBillLine]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadBillLineItemBillLineByBillLineItemId;
GO

CREATE PROCEDURE ReadBillLineItemBillLineByBillLineItemId
(
    @BillLineItemId BIGINT
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
        [BillLineItemId],
        [QboBillLineId]
    FROM [qbo].[BillLineItemBillLine]
    WHERE [BillLineItemId] = @BillLineItemId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadBillLineItemBillLineByQboBillLineId;
GO

CREATE PROCEDURE ReadBillLineItemBillLineByQboBillLineId
(
    @QboBillLineId BIGINT
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
        [BillLineItemId],
        [QboBillLineId]
    FROM [qbo].[BillLineItemBillLine]
    WHERE [QboBillLineId] = @QboBillLineId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteBillLineItemBillLineById;
GO

CREATE PROCEDURE DeleteBillLineItemBillLineById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[BillLineItemBillLine]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillLineItemId],
        DELETED.[QboBillLineId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

SELECT * FROM qbo.BillLineItemBillLine;



