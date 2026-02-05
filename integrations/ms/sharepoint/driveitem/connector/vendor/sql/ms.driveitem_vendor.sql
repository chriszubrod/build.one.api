IF OBJECT_ID('ms.DriveItemVendor', 'U') IS NULL
BEGIN
CREATE TABLE [ms].[DriveItemVendor]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NOT NULL,
    [MsDriveItemId] BIGINT NOT NULL,
    CONSTRAINT [UQ_DriveItemVendor_VendorId] UNIQUE ([VendorId]),
    CONSTRAINT [UQ_DriveItemVendor_MsDriveItemId] UNIQUE ([MsDriveItemId]),
    CONSTRAINT [FK_DriveItemVendor_DriveItem] FOREIGN KEY ([MsDriveItemId]) REFERENCES [ms].[DriveItem]([Id]) ON DELETE CASCADE
);
END
GO



CREATE OR ALTER PROCEDURE CreateDriveItemVendor
(
    @VendorId BIGINT,
    @MsDriveItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[DriveItemVendor] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [MsDriveItemId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[MsDriveItemId]
    VALUES (@Now, @Now, @VendorId, @MsDriveItemId);

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE ReadDriveItemVendorById
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
        [MsDriveItemId]
    FROM [ms].[DriveItemVendor]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE ReadDriveItemVendorByVendorId
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
        [MsDriveItemId]
    FROM [ms].[DriveItemVendor]
    WHERE [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE ReadDriveItemVendorByMsDriveItemId
(
    @MsDriveItemId BIGINT
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
        [MsDriveItemId]
    FROM [ms].[DriveItemVendor]
    WHERE [MsDriveItemId] = @MsDriveItemId;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE DeleteDriveItemVendorById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemVendor]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        DELETED.[MsDriveItemId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE DeleteDriveItemVendorByVendorId
(
    @VendorId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemVendor]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        DELETED.[MsDriveItemId]
    WHERE [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;
GO
