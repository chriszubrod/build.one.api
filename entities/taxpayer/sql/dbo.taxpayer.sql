IF OBJECT_ID('dbo.Taxpayer', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Taxpayer]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [EntityName] NVARCHAR(MAX) NOT NULL,
    [BusinessName] NVARCHAR(MAX) NOT NULL,
    [Classification] NVARCHAR(MAX) NOT NULL,
    [TaxpayerIdNumber] NVARCHAR(MAX) NOT NULL,
    [IsSigned] INT NOT NULL,
    [SignatureDate] DATETIME2(3) NOT NULL
);
END
GO

-- Add columns to existing table if missing (migration)
IF COL_LENGTH('dbo.Taxpayer', 'IsSigned') IS NULL
BEGIN
    ALTER TABLE [dbo].[Taxpayer] ADD [IsSigned] INT NOT NULL DEFAULT 0;
END
IF COL_LENGTH('dbo.Taxpayer', 'SignatureDate') IS NULL
BEGIN
    ALTER TABLE [dbo].[Taxpayer] ADD [SignatureDate] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME();
END
GO



CREATE OR ALTER PROCEDURE CreateTaxpayer
(
    @EntityName NVARCHAR(MAX),
    @BusinessName NVARCHAR(MAX),
    @Classification NVARCHAR(MAX),
    @TaxpayerIdNumber NVARCHAR(MAX),
    @IsSigned INT = 0,
    @SignatureDate DATETIME2(3) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @SigDate DATETIME2(3) = COALESCE(@SignatureDate, @Now);

    INSERT INTO dbo.[Taxpayer] ([CreatedDatetime], [ModifiedDatetime], [EntityName], [BusinessName], [Classification], [TaxpayerIdNumber], [IsSigned], [SignatureDate])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EntityName],
        INSERTED.[BusinessName],
        INSERTED.[Classification],
        INSERTED.[TaxpayerIdNumber],
        INSERTED.[IsSigned],
        CONVERT(VARCHAR(19), INSERTED.[SignatureDate], 120) AS [SignatureDate]
    VALUES (@Now, @Now, @EntityName, @BusinessName, @Classification, @TaxpayerIdNumber, @IsSigned, @SigDate);

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayers
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [EntityName],
        [BusinessName],
        [Classification],
        [TaxpayerIdNumber],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate]
    FROM dbo.[Taxpayer]
    ORDER BY [EntityName] ASC;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayerById
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
        [EntityName],
        [BusinessName],
        [Classification],
        [TaxpayerIdNumber],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate]
    FROM dbo.[Taxpayer]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayerByPublicId
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
        [EntityName],
        [BusinessName],
        [Classification],
        [TaxpayerIdNumber],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate]
    FROM dbo.[Taxpayer]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayerByName
(
    @EntityName NVARCHAR(MAX)
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
        [EntityName],
        [BusinessName],
        [Classification],
        [TaxpayerIdNumber],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate]
    FROM dbo.[Taxpayer]
    WHERE [EntityName] = @EntityName;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayerByBusinessName
(
    @BusinessName NVARCHAR(MAX)
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
        [EntityName],
        [BusinessName],
        [Classification],
        [TaxpayerIdNumber],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate]
    FROM dbo.[Taxpayer]
    WHERE [BusinessName] = @BusinessName;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayerByTaxpayerIdNumber
(
    @TaxpayerIdNumber NVARCHAR(MAX)
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
        [EntityName],
        [BusinessName],
        [Classification],
        [TaxpayerIdNumber],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate]
    FROM dbo.[Taxpayer]
    WHERE [TaxpayerIdNumber] = @TaxpayerIdNumber;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE UpdateTaxpayerById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @EntityName NVARCHAR(MAX),
    @BusinessName NVARCHAR(MAX),
    @Classification NVARCHAR(MAX),
    @TaxpayerIdNumber NVARCHAR(MAX),
    @IsSigned INT,
    @SignatureDate DATETIME2(3)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Taxpayer]
    SET
        [ModifiedDatetime] = @Now,
        [EntityName] = @EntityName,
        [BusinessName] = @BusinessName,
        [Classification] = @Classification,
        [TaxpayerIdNumber] = @TaxpayerIdNumber,
        [IsSigned] = @IsSigned,
        [SignatureDate] = @SignatureDate
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EntityName],
        INSERTED.[BusinessName],
        INSERTED.[Classification],
        INSERTED.[TaxpayerIdNumber],
        INSERTED.[IsSigned],
        CONVERT(VARCHAR(19), INSERTED.[SignatureDate], 120) AS [SignatureDate]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE DeleteTaxpayerById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Taxpayer]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[EntityName],
        DELETED.[BusinessName],
        DELETED.[Classification],
        DELETED.[TaxpayerIdNumber],
        DELETED.[IsSigned],
        CONVERT(VARCHAR(19), DELETED.[SignatureDate], 120) AS [SignatureDate]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- PublicId index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Taxpayer_PublicId' AND object_id = OBJECT_ID('dbo.Taxpayer'))
BEGIN
    CREATE INDEX [IX_Taxpayer_PublicId] ON [dbo].[Taxpayer] ([PublicId]);
END
GO
