DROP TABLE IF EXISTS [qbo].[VendorVendor];
GO

CREATE TABLE [qbo].[VendorVendor]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NOT NULL,
    [QboVendorId] BIGINT NOT NULL,
    CONSTRAINT [UQ_VendorVendor_VendorId] UNIQUE ([VendorId]),
    CONSTRAINT [UQ_VendorVendor_QboVendorId] UNIQUE ([QboVendorId])
);
GO


DROP PROCEDURE IF EXISTS CreateVendorVendor;
GO

CREATE PROCEDURE CreateVendorVendor
(
    @VendorId BIGINT,
    @QboVendorId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[VendorVendor] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [QboVendorId])
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
GO


DROP PROCEDURE IF EXISTS ReadVendorVendorById;
GO

CREATE PROCEDURE ReadVendorVendorById
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
    FROM [qbo].[VendorVendor]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadVendorVendorByVendorId;
GO

CREATE PROCEDURE ReadVendorVendorByVendorId
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
    FROM [qbo].[VendorVendor]
    WHERE [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadVendorVendorByQboVendorId;
GO

CREATE PROCEDURE ReadVendorVendorByQboVendorId
(
    @QboVendorId BIGINT
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
    FROM [qbo].[VendorVendor]
    WHERE [QboVendorId] = @QboVendorId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS UpdateVendorVendorById;
GO

CREATE PROCEDURE UpdateVendorVendorById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT,
    @QboVendorId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[VendorVendor]
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
GO


DROP PROCEDURE IF EXISTS DeleteVendorVendorById;
GO

CREATE PROCEDURE DeleteVendorVendorById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[VendorVendor]
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
GO


