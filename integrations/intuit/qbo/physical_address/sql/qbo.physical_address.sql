GO

IF OBJECT_ID('qbo.PhysicalAddress', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[PhysicalAddress]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(MAX) NULL,
    [Line1] NVARCHAR(MAX) NULL,
    [Line2] NVARCHAR(MAX) NULL,
    [City] NVARCHAR(MAX) NULL,
    [Country] NVARCHAR(MAX) NULL,
    [CountrySubDivisionCode] NVARCHAR(MAX) NULL,
    [PostalCode] NVARCHAR(MAX) NULL
);



END
GO

CREATE OR ALTER PROCEDURE CreateQboPhysicalAddress
(
    @QboId NVARCHAR(MAX),
    @Line1 NVARCHAR(MAX),
    @Line2 NVARCHAR(MAX),
    @City NVARCHAR(MAX),
    @Country NVARCHAR(MAX),
    @CountrySubDivisionCode NVARCHAR(MAX),
    @PostalCode NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO [qbo].[PhysicalAddress] ([CreatedDatetime], [ModifiedDatetime], [QboId], [Line1], [Line2], [City], [Country], [CountrySubDivisionCode], [PostalCode])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[Line1],
        INSERTED.[Line2],
        INSERTED.[City],
        INSERTED.[Country],
        INSERTED.[CountrySubDivisionCode],
        INSERTED.[PostalCode]
    VALUES (@Now, @Now, @QboId, @Line1, @Line2, @City, @Country, @CountrySubDivisionCode, @PostalCode);

    COMMIT TRANSACTION;
END;
GO




GO

CREATE OR ALTER PROCEDURE ReadQboPhysicalAddresses
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [QboId],
        [Line1],
        [Line2],
        [City],
        [Country],
        [CountrySubDivisionCode],
        [PostalCode]
    FROM [qbo].[PhysicalAddress]
    ORDER BY [QboId] ASC;

    COMMIT TRANSACTION;
END;
GO



GO

CREATE OR ALTER PROCEDURE ReadQboPhysicalAddressById
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
        [QboId],
        [Line1],
        [Line2],
        [City],
        [Country],
        [CountrySubDivisionCode],
        [PostalCode]
    FROM [qbo].[PhysicalAddress]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadQboPhysicalAddressByPublicId
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
        [QboId],
        [Line1],
        [Line2],
        [City],
        [Country],
        [CountrySubDivisionCode],
        [PostalCode]
    FROM [qbo].[PhysicalAddress]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO





GO

CREATE OR ALTER PROCEDURE ReadQboPhysicalAddressByQboId
(
    @QboId NVARCHAR(MAX)
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
        [QboId],
        [Line1],
        [Line2],
        [City],
        [Country],
        [CountrySubDivisionCode],
        [PostalCode]
    FROM [qbo].[PhysicalAddress]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO





GO

CREATE OR ALTER PROCEDURE UpdateQboPhysicalAddressById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @QboId NVARCHAR(MAX),
    @Line1 NVARCHAR(MAX),
    @Line2 NVARCHAR(MAX),
    @City NVARCHAR(MAX),
    @Country NVARCHAR(MAX),
    @CountrySubDivisionCode NVARCHAR(MAX),
    @PostalCode NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE [qbo].[PhysicalAddress]
    SET [ModifiedDatetime] = @Now,
        [QboId] = CASE WHEN @QboId IS NULL THEN [QboId] ELSE @QboId END,
        [Line1] = CASE WHEN @Line1 IS NULL THEN [Line1] ELSE @Line1 END,
        [Line2] = CASE WHEN @Line2 IS NULL THEN [Line2] ELSE @Line2 END,
        [City] = CASE WHEN @City IS NULL THEN [City] ELSE @City END,
        [Country] = CASE WHEN @Country IS NULL THEN [Country] ELSE @Country END,
        [CountrySubDivisionCode] = CASE WHEN @CountrySubDivisionCode IS NULL THEN [CountrySubDivisionCode] ELSE @CountrySubDivisionCode END,
        [PostalCode] = CASE WHEN @PostalCode IS NULL THEN [PostalCode] ELSE @PostalCode END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[Line1],
        INSERTED.[Line2],
        INSERTED.[City],
        INSERTED.[Country],
        INSERTED.[CountrySubDivisionCode],
        INSERTED.[PostalCode]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO




GO

CREATE OR ALTER PROCEDURE DeleteQboPhysicalAddressById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[PhysicalAddress]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[Line1],
        DELETED.[Line2],
        DELETED.[City],
        DELETED.[Country],
        DELETED.[CountrySubDivisionCode],
        DELETED.[PostalCode]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


