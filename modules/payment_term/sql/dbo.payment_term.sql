DROP TABLE IF EXISTS dbo.[PaymentTerm];
GO

CREATE TABLE [dbo].[PaymentTerm]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Description] NVARCHAR(255) NULL,
    [DiscountPercent] DECIMAL(5,2) NULL,
    [DiscountDays] INT NULL,
    [DueDays] INT NULL
);
GO


DROP PROCEDURE IF EXISTS CreatePaymentTerm;
GO

CREATE PROCEDURE CreatePaymentTerm
(
    @Name NVARCHAR(50),
    @Description NVARCHAR(255),
    @DiscountPercent DECIMAL(5,2) NULL,
    @DiscountDays INT NULL,
    @DueDays INT NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[PaymentTerm] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [DiscountPercent], [DiscountDays], [DueDays])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[DiscountPercent],
        INSERTED.[DiscountDays],
        INSERTED.[DueDays]
    VALUES (@Now, @Now, @Name, @Description, @DiscountPercent, @DiscountDays, @DueDays);

    COMMIT TRANSACTION;
END;

EXEC CreatePaymentTerm
    @Name = 'Net 30',
    @Description = 'Payment due within 30 days.',
    @DiscountPercent = NULL,
    @DiscountDays = NULL,
    @DueDays = 30;
GO

EXEC CreatePaymentTerm
    @Name = '2/10 Net 30',
    @Description = '2% discount if paid within 10 days, otherwise due in 30 days.',
    @DiscountPercent = 2.00,
    @DiscountDays = 10,
    @DueDays = 30;
GO


DROP PROCEDURE IF EXISTS ReadPaymentTerms;
GO

CREATE PROCEDURE ReadPaymentTerms
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Description],
        [DiscountPercent],
        [DiscountDays],
        [DueDays]
    FROM dbo.[PaymentTerm]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadPaymentTerms;
GO


DROP PROCEDURE IF EXISTS ReadPaymentTermById;
GO

CREATE PROCEDURE ReadPaymentTermById
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
        [Name],
        [Description],
        [DiscountPercent],
        [DiscountDays],
        [DueDays]
    FROM dbo.[PaymentTerm]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadPaymentTermById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadPaymentTermByPublicId;
GO

CREATE PROCEDURE ReadPaymentTermByPublicId
(
    @PublicId UNIQUEIDENTIFIER
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
        [Name],
        [Description],
        [DiscountPercent],
        [DiscountDays],
        [DueDays]
    FROM dbo.[PaymentTerm]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadPaymentTermByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadPaymentTermByName;
GO

CREATE PROCEDURE ReadPaymentTermByName
(
    @Name NVARCHAR(50)
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
        [Name],
        [Description],
        [DiscountPercent],
        [DiscountDays],
        [DueDays]
    FROM dbo.[PaymentTerm]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;

EXEC ReadPaymentTermByName
    @Name = 'Net 30';
GO


DROP PROCEDURE IF EXISTS UpdatePaymentTermById;
GO

CREATE PROCEDURE UpdatePaymentTermById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Description NVARCHAR(255),
    @DiscountPercent DECIMAL(5,2) NULL,
    @DiscountDays INT NULL,
    @DueDays INT NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[PaymentTerm]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Description] = @Description,
        [DiscountPercent] = @DiscountPercent,
        [DiscountDays] = @DiscountDays,
        [DueDays] = @DueDays
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[DiscountPercent],
        INSERTED.[DiscountDays],
        INSERTED.[DueDays]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdatePaymentTermById
    @Id = 1,
    @RowVersion = 0x0000000000000000,
    @Name = 'Net 45',
    @Description = 'Payment due within 45 days.',
    @DiscountPercent = NULL,
    @DiscountDays = NULL,
    @DueDays = 45;
GO


DROP PROCEDURE IF EXISTS DeletePaymentTermById;
GO

CREATE PROCEDURE DeletePaymentTermById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[PaymentTerm]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Description],
        DELETED.[DiscountPercent],
        DELETED.[DiscountDays],
        DELETED.[DueDays]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeletePaymentTermById
    @Id = 1;
GO
