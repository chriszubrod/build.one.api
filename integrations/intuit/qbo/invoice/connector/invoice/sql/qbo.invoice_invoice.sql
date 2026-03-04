GO

IF OBJECT_ID('qbo.InvoiceInvoice', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[InvoiceInvoice]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [InvoiceId] BIGINT NOT NULL,
    [QboInvoiceId] BIGINT NOT NULL,
    CONSTRAINT [UQ_InvoiceInvoice_InvoiceId] UNIQUE ([InvoiceId]),
    CONSTRAINT [UQ_InvoiceInvoice_QboInvoiceId] UNIQUE ([QboInvoiceId])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateInvoiceInvoice
(
    @InvoiceId BIGINT,
    @QboInvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[InvoiceInvoice] ([CreatedDatetime], [ModifiedDatetime], [InvoiceId], [QboInvoiceId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceId],
        INSERTED.[QboInvoiceId]
    VALUES (@Now, @Now, @InvoiceId, @QboInvoiceId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadInvoiceInvoiceById
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
        [InvoiceId],
        [QboInvoiceId]
    FROM [qbo].[InvoiceInvoice]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadInvoiceInvoiceByInvoiceId
(
    @InvoiceId BIGINT
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
        [InvoiceId],
        [QboInvoiceId]
    FROM [qbo].[InvoiceInvoice]
    WHERE [InvoiceId] = @InvoiceId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadInvoiceInvoiceByQboInvoiceId
(
    @QboInvoiceId BIGINT
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
        [InvoiceId],
        [QboInvoiceId]
    FROM [qbo].[InvoiceInvoice]
    WHERE [QboInvoiceId] = @QboInvoiceId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteInvoiceInvoiceById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[InvoiceInvoice]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[InvoiceId],
        DELETED.[QboInvoiceId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
