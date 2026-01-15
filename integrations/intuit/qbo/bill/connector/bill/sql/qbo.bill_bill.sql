DROP TABLE IF EXISTS [qbo].[BillBill];
GO

CREATE TABLE [qbo].[BillBill]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BillId] BIGINT NOT NULL,
    [QboBillId] BIGINT NOT NULL,
    CONSTRAINT [UQ_BillBill_BillId] UNIQUE ([BillId]),
    CONSTRAINT [UQ_BillBill_QboBillId] UNIQUE ([QboBillId])
);
GO


DROP PROCEDURE IF EXISTS CreateBillBill;
GO

CREATE PROCEDURE CreateBillBill
(
    @BillId BIGINT,
    @QboBillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[BillBill] ([CreatedDatetime], [ModifiedDatetime], [BillId], [QboBillId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillId],
        INSERTED.[QboBillId]
    VALUES (@Now, @Now, @BillId, @QboBillId);

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadBillBillById;
GO

CREATE PROCEDURE ReadBillBillById
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
        [BillId],
        [QboBillId]
    FROM [qbo].[BillBill]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadBillBillByBillId;
GO

CREATE PROCEDURE ReadBillBillByBillId
(
    @BillId BIGINT
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
        [BillId],
        [QboBillId]
    FROM [qbo].[BillBill]
    WHERE [BillId] = @BillId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadBillBillByQboBillId;
GO

CREATE PROCEDURE ReadBillBillByQboBillId
(
    @QboBillId BIGINT
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
        [BillId],
        [QboBillId]
    FROM [qbo].[BillBill]
    WHERE [QboBillId] = @QboBillId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteBillBillById;
GO

CREATE PROCEDURE DeleteBillBillById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[BillBill]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillId],
        DELETED.[QboBillId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


SELECT * FROM qbo.BillBill;