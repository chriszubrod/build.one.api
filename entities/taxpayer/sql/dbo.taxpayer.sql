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
    [TaxpayerIdNumberHash] CHAR(64) NULL,
    [IsSigned] INT NOT NULL,
    [SignatureDate] DATETIME2(3) NULL,
    [IsDeleted] BIT NOT NULL DEFAULT 0
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

IF COL_LENGTH('dbo.Taxpayer', 'TaxpayerIdNumberHash') IS NULL
BEGIN
    ALTER TABLE dbo.[Taxpayer] ADD [TaxpayerIdNumberHash] CHAR(64) NULL;
END
GO

IF COL_LENGTH('dbo.Taxpayer', 'IsDeleted') IS NULL
BEGIN
    ALTER TABLE dbo.[Taxpayer] ADD [IsDeleted] BIT NOT NULL CONSTRAINT DF_Taxpayer_IsDeleted DEFAULT (0);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Taxpayer_TaxpayerIdNumberHash' AND object_id = OBJECT_ID('dbo.Taxpayer'))
BEGIN
    CREATE UNIQUE INDEX UQ_Taxpayer_TaxpayerIdNumberHash ON dbo.[Taxpayer]([TaxpayerIdNumberHash]) WHERE [TaxpayerIdNumberHash] IS NOT NULL AND [IsDeleted] = 0;
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Taxpayer') AND name = 'SignatureDate' AND is_nullable = 0)
BEGIN
    ALTER TABLE dbo.[Taxpayer] ALTER COLUMN [SignatureDate] DATETIME2(3) NULL;
END
GO



CREATE OR ALTER PROCEDURE CreateTaxpayer
(
    @EntityName NVARCHAR(MAX),
    @BusinessName NVARCHAR(MAX),
    @Classification NVARCHAR(MAX),
    @TaxpayerIdNumber NVARCHAR(MAX),
    @TaxpayerIdNumberHash CHAR(64) = NULL,
    @IsSigned INT = 0,
    @SignatureDate DATETIME2(3) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @SigDate DATETIME2(3) = @SignatureDate;  -- allow NULL for an unsigned/undated W-9

    INSERT INTO dbo.[Taxpayer] ([CreatedDatetime], [ModifiedDatetime], [EntityName], [BusinessName], [Classification], [TaxpayerIdNumber], [TaxpayerIdNumberHash], [IsSigned], [SignatureDate], [IsDeleted])
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
        INSERTED.[TaxpayerIdNumberHash],
        INSERTED.[IsSigned],
        CONVERT(VARCHAR(19), INSERTED.[SignatureDate], 120) AS [SignatureDate],
        INSERTED.[IsDeleted]
    VALUES (@Now, @Now, @EntityName, @BusinessName, @Classification, @TaxpayerIdNumber, @TaxpayerIdNumberHash, @IsSigned, @SigDate, 0);

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayers
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

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
        [TaxpayerIdNumberHash],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate],
        [IsDeleted]
    FROM dbo.[Taxpayer]
    WHERE [IsDeleted] = 0
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
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

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
        [TaxpayerIdNumberHash],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate],
        [IsDeleted]
    FROM dbo.[Taxpayer]
    WHERE [Id] = @Id AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayerByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

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
        [TaxpayerIdNumberHash],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate],
        [IsDeleted]
    FROM dbo.[Taxpayer]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayerByName
(
    @EntityName NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

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
        [TaxpayerIdNumberHash],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate],
        [IsDeleted]
    FROM dbo.[Taxpayer]
    WHERE [EntityName] = @EntityName AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadTaxpayerByBusinessName
(
    @BusinessName NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

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
        [TaxpayerIdNumberHash],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate],
        [IsDeleted]
    FROM dbo.[Taxpayer]
    WHERE [BusinessName] = @BusinessName AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO




DROP PROCEDURE IF EXISTS dbo.ReadTaxpayerByTaxpayerIdNumber;
GO

CREATE OR ALTER PROCEDURE ReadTaxpayerByTaxpayerIdNumberHash
(
    @TaxpayerIdNumberHash CHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

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
        [TaxpayerIdNumberHash],
        [IsSigned],
        CONVERT(VARCHAR(19), [SignatureDate], 120) AS [SignatureDate],
        [IsDeleted]
    FROM dbo.[Taxpayer]
    WHERE [TaxpayerIdNumberHash] = @TaxpayerIdNumberHash AND [IsDeleted] = 0;

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
    @TaxpayerIdNumberHash CHAR(64) = NULL,
    @IsSigned INT,
    @SignatureDate DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Taxpayer]
    SET
        [ModifiedDatetime] = @Now,
        [EntityName] = @EntityName,
        [BusinessName] = @BusinessName,
        [Classification] = @Classification,
        [TaxpayerIdNumber] = @TaxpayerIdNumber,
        [TaxpayerIdNumberHash] = CASE WHEN @TaxpayerIdNumberHash IS NULL THEN [TaxpayerIdNumberHash] ELSE @TaxpayerIdNumberHash END,
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
        INSERTED.[TaxpayerIdNumberHash],
        INSERTED.[IsSigned],
        CONVERT(VARCHAR(19), INSERTED.[SignatureDate], 120) AS [SignatureDate],
        INSERTED.[IsDeleted]
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
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Taxpayer]
    SET [IsDeleted] = 1, [ModifiedDatetime] = @Now
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
        INSERTED.[TaxpayerIdNumberHash],
        INSERTED.[IsSigned],
        CONVERT(VARCHAR(19), INSERTED.[SignatureDate], 120) AS [SignatureDate],
        INSERTED.[IsDeleted]
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
