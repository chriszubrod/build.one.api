DROP SCHEMA IF EXISTS [map];
GO

CREATE SCHEMA [map];
GO

CREATE TABLE [map].[VendorQboVendor]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NOT NULL,
    [QboVendorId] NVARCHAR(MAX) NOT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateVendorQboVendor;
GO

CREATE PROCEDURE CreateVendorQboVendor
(
    @VendorId BIGINT,
    @QboVendorId NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorQboVendor] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [QboVendorId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[QboVendorId]
    VALUES (@Now, @Now, @VendorId, @QboVendorId);

    COMMIT TRANSACTION;
END;

EXEC CreateVendorQboVendor
    @VendorId = 1,
    @QboVendorId = '1';
GO


DROP PROCEDURE IF EXISTS ReadVendorQboVendors;
GO

CREATE PROCEDURE ReadVendorQboVendors
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [QboVendorId]
    FROM dbo.[VendorQboVendor]
    ORDER BY [VendorId] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorQboVendors;
GO


DROP PROCEDURE IF EXISTS ReadVendorQboVendorById;
GO

CREATE PROCEDURE ReadVendorQboVendorById
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
        [VendorId],
        [QboVendorId]
    FROM dbo.[VendorQboVendor]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorQboVendorById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadVendorQboVendorByPublicId;
GO

CREATE PROCEDURE ReadVendorQboVendorByPublicId
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
        [VendorId],
        [QboVendorId]
    FROM dbo.[VendorQboVendor]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorQboVendorByPublicId
    @PublicId = 'c86edd93-a99c-424b-afa3-8df26f7de144';
GO


DROP PROCEDURE IF EXISTS ReadVendorQboVendorByVendorId;
GO

CREATE PROCEDURE ReadVendorQboVendorByVendorId
(
    @VendorId BIGINT
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
        [VendorId],
        [QboVendorId]
    FROM dbo.[VendorQboVendor]
    WHERE [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorQboVendorByVendorId
    @VendorId = 1;
GO



DROP PROCEDURE IF EXISTS ReadVendorQboVendorByQboVendorId;
GO

CREATE PROCEDURE ReadVendorQboVendorByQboVendorId
(
    @QboVendorId NVARCHAR(MAX)
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
        [VendorId],
        [QboVendorId]
    FROM dbo.[VendorQboVendor]
    WHERE [QboVendorId] = @QboVendorId;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorQboVendorByQboVendorId
    @QboVendorId = 1;
GO




DROP PROCEDURE IF EXISTS UpdateVendorQboVendorById;
GO

CREATE PROCEDURE UpdateVendorQboVendorById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT,
    @QboVendorId NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[VendorQboVendor]
    SET
        [ModifiedDatetime] = @Now,
        [VendorId] = @VendorId,
        [QboVendorId] = @QboVendorId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[QboVendorId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateVendorQboVendorById
    @Id = 1,
    @RowVersion = 0x0000000000020B85,
    @VendorId = 1,
    @QboVendorId = '1';
GO


DROP PROCEDURE IF EXISTS DeleteVendorQboVendorById;
GO

CREATE PROCEDURE DeleteVendorQboVendorById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Sync]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        DELETED.[QboVendorId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteVendorQboVendorById
    @Id = 1;
GO
