GO

IF OBJECT_ID('qbo.InvoiceLineItemInvoiceLine', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[InvoiceLineItemInvoiceLine]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [InvoiceLineItemId] BIGINT NOT NULL,
    [QboInvoiceLineId] BIGINT NOT NULL,
    CONSTRAINT [UQ_InvoiceLineItemInvoiceLine_InvoiceLineItemId] UNIQUE ([InvoiceLineItemId]),
    CONSTRAINT [UQ_InvoiceLineItemInvoiceLine_QboInvoiceLineId] UNIQUE ([QboInvoiceLineId])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateInvoiceLineItemInvoiceLine
(
    @InvoiceLineItemId BIGINT,
    @QboInvoiceLineId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[InvoiceLineItemInvoiceLine] ([CreatedDatetime], [ModifiedDatetime], [InvoiceLineItemId], [QboInvoiceLineId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceLineItemId],
        INSERTED.[QboInvoiceLineId]
    VALUES (@Now, @Now, @InvoiceLineItemId, @QboInvoiceLineId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadInvoiceLineItemInvoiceLineById
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
        [InvoiceLineItemId],
        [QboInvoiceLineId]
    FROM [qbo].[InvoiceLineItemInvoiceLine]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadInvoiceLineItemInvoiceLineByInvoiceLineItemId
(
    @InvoiceLineItemId BIGINT
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
        [InvoiceLineItemId],
        [QboInvoiceLineId]
    FROM [qbo].[InvoiceLineItemInvoiceLine]
    WHERE [InvoiceLineItemId] = @InvoiceLineItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadInvoiceLineItemInvoiceLineByQboInvoiceLineId
(
    @QboInvoiceLineId BIGINT
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
        [InvoiceLineItemId],
        [QboInvoiceLineId]
    FROM [qbo].[InvoiceLineItemInvoiceLine]
    WHERE [QboInvoiceLineId] = @QboInvoiceLineId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteInvoiceLineItemInvoiceLineById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[InvoiceLineItemInvoiceLine]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[InvoiceLineItemId],
        DELETED.[QboInvoiceLineId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
