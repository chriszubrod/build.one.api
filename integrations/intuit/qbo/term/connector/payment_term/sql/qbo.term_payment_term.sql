GO

IF OBJECT_ID('qbo.TermPaymentTerm', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[TermPaymentTerm]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [PaymentTermId] BIGINT NOT NULL,
    [QboTermId] BIGINT NOT NULL,
    CONSTRAINT [UQ_TermPaymentTerm_PaymentTermId] UNIQUE ([PaymentTermId]),
    CONSTRAINT [UQ_TermPaymentTerm_QboTermId] UNIQUE ([QboTermId])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateTermPaymentTerm
(
    @PaymentTermId BIGINT,
    @QboTermId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[TermPaymentTerm] ([CreatedDatetime], [ModifiedDatetime], [PaymentTermId], [QboTermId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[PaymentTermId],
        INSERTED.[QboTermId]
    VALUES (@Now, @Now, @PaymentTermId, @QboTermId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTermPaymentTermById
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
        [PaymentTermId],
        [QboTermId]
    FROM [qbo].[TermPaymentTerm]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTermPaymentTermByPaymentTermId
(
    @PaymentTermId BIGINT
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
        [PaymentTermId],
        [QboTermId]
    FROM [qbo].[TermPaymentTerm]
    WHERE [PaymentTermId] = @PaymentTermId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTermPaymentTermByQboTermId
(
    @QboTermId BIGINT
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
        [PaymentTermId],
        [QboTermId]
    FROM [qbo].[TermPaymentTerm]
    WHERE [QboTermId] = @QboTermId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateTermPaymentTermById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @PaymentTermId BIGINT,
    @QboTermId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[TermPaymentTerm]
    SET
        [ModifiedDatetime] = @Now,
        [PaymentTermId] = @PaymentTermId,
        [QboTermId] = @QboTermId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[PaymentTermId],
        INSERTED.[QboTermId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteTermPaymentTermById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[TermPaymentTerm]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[PaymentTermId],
        DELETED.[QboTermId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


