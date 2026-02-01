GO

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
    [TaxpayerIdNumber] NVARCHAR(MAX) NOT NULL
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateTaxpayer
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
        [TaxpayerIdNumber]
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
        [TaxpayerIdNumber]
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
        [TaxpayerIdNumber]
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
        [TaxpayerIdNumber]
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
        [TaxpayerIdNumber]
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
        [TaxpayerIdNumber]
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
        DELETED.[TaxpayerIdNumber]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

