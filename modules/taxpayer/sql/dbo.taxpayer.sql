DROP TABLE IF EXISTS [dbo].[Taxpayer];
GO

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
    [TaxpayerIdNumber] NVARCHAR(MAX) NOT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateTaxpayer;
GO

CREATE PROCEDURE CreateTaxpayer
(
    @EntityName NVARCHAR(MAX),
    @BusinessName NVARCHAR(MAX),
    @Classification NVARCHAR(MAX),
    @TaxpayerIdNumber NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Taxpayer] ([CreatedDatetime], [ModifiedDatetime], [EntityName], [BusinessName], [Classification], [TaxpayerIdNumber])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EntityName],
        INSERTED.[BusinessName],
        INSERTED.[Classification],
        INSERTED.[TaxpayerIdNumber]
    VALUES (@Now, @Now, @EntityName, @BusinessName, @Classification, @TaxpayerIdNumber);

    COMMIT TRANSACTION;
END;

EXEC CreateTaxpayer
    @EntityName = 'Acme Supply Co.',
    @BusinessName = 'Acme Supply Co.',
    @Classification = 'Corporation',
    @TaxpayerIdNumber = '1234567890';
GO


DROP PROCEDURE IF EXISTS ReadTaxpayers;
GO

CREATE PROCEDURE ReadTaxpayers
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
        [TaxpayerIdNumber]
    FROM dbo.[Taxpayer]
    ORDER BY [EntityName] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayers;
GO


DROP PROCEDURE IF EXISTS ReadTaxpayerById;
GO

CREATE PROCEDURE ReadTaxpayerById
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
        [TaxpayerIdNumber]
    FROM dbo.[Taxpayer]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayerById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadTaxpayerByPublicId;
GO

CREATE PROCEDURE ReadTaxpayerByPublicId
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
        [TaxpayerIdNumber]
    FROM dbo.[Taxpayer]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayerByPublicId
    @PublicId = 1;
GO


DROP PROCEDURE IF EXISTS ReadTaxpayerByName;
GO

CREATE PROCEDURE ReadTaxpayerByName
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
        [TaxpayerIdNumber]
    FROM dbo.[Taxpayer]
    WHERE [EntityName] = @EntityName;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayerByName
    @EntityName = 'Acme Supply Co.';
GO


DROP PROCEDURE IF EXISTS ReadTaxpayerByBusinessName;
GO

CREATE PROCEDURE ReadTaxpayerByBusinessName
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
        [TaxpayerIdNumber]
    FROM dbo.[Taxpayer]
    WHERE [BusinessName] = @BusinessName;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayerByBusinessName
    @BusinessName = 'Acme Supply Co.';
GO


DROP PROCEDURE IF EXISTS ReadTaxpayerByTaxpayerIdNumber;
GO

CREATE PROCEDURE ReadTaxpayerByTaxpayerIdNumber
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
        [TaxpayerIdNumber]
    FROM dbo.[Taxpayer]
    WHERE [TaxpayerIdNumber] = @TaxpayerIdNumber;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayerByTaxpayerIdNumber
    @TaxpayerIdNumber = '1234567890';
GO


DROP PROCEDURE IF EXISTS UpdateTaxpayerById;
GO

CREATE PROCEDURE UpdateTaxpayerById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @EntityName NVARCHAR(MAX),
    @BusinessName NVARCHAR(MAX),
    @Classification NVARCHAR(MAX),
    @TaxpayerIdNumber NVARCHAR(MAX)
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
        [TaxpayerIdNumber] = @TaxpayerIdNumber
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EntityName],
        INSERTED.[BusinessName],
        INSERTED.[Classification],
        INSERTED.[TaxpayerIdNumber]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateTaxpayerById
    @Id = 1,
    @RowVersion = 0x0000000000000000,
    @EntityName = 'Acme Supply Co. Updated',
    @BusinessName = 'Acme Supply Co. Updated',
    @Classification = 'Corporation',
    @TaxpayerIdNumber = '1234567890';
GO


DROP PROCEDURE IF EXISTS DeleteTaxpayerById;
GO

CREATE PROCEDURE DeleteTaxpayerById
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
        DELETED.[TaxpayerIdNumber]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteTaxpayerById
    @Id = 1;
GO
