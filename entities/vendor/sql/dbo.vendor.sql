GO

IF OBJECT_ID('dbo.Vendor', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Vendor]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(450) NOT NULL,
    [Abbreviation] NVARCHAR(255) NULL,
    [VendorTypeId] BIGINT NULL,
    [TaxpayerId] BIGINT NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    [IsDeleted] BIT NOT NULL DEFAULT 0,
    [IsContractLabor] BIT NOT NULL DEFAULT 0
);
END
GO

-- Migrate Name column from NVARCHAR(MAX) to NVARCHAR(450) for indexability
IF EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Vendor' AND TABLE_SCHEMA = 'dbo' AND COLUMN_NAME = 'Name' AND CHARACTER_MAXIMUM_LENGTH = -1
)
BEGIN
    ALTER TABLE [dbo].[Vendor] ALTER COLUMN [Name] NVARCHAR(450) NOT NULL;
END
GO

-- Add IsDeleted column if it does not exist (migration for existing tables)
IF COL_LENGTH('dbo.Vendor', 'IsDeleted') IS NULL
BEGIN
    ALTER TABLE [dbo].[Vendor] ADD [IsDeleted] BIT NOT NULL DEFAULT 0;
END
GO

-- Add IsContractLabor column if it does not exist (migration for existing tables)
IF COL_LENGTH('dbo.Vendor', 'IsContractLabor') IS NULL
BEGIN
    ALTER TABLE [dbo].[Vendor] ADD [IsContractLabor] BIT NOT NULL DEFAULT 0;
END
GO

-- FK constraint: VendorTypeId -> VendorType.Id
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Vendor_VendorType')
BEGIN
    ALTER TABLE [dbo].[Vendor]
    ADD CONSTRAINT [FK_Vendor_VendorType] FOREIGN KEY ([VendorTypeId]) REFERENCES [dbo].[VendorType]([Id]);
END
GO

-- FK constraint: TaxpayerId -> Taxpayer.Id
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Vendor_Taxpayer')
BEGIN
    ALTER TABLE [dbo].[Vendor]
    ADD CONSTRAINT [FK_Vendor_Taxpayer] FOREIGN KEY ([TaxpayerId]) REFERENCES [dbo].[Taxpayer]([Id]);
END
GO

-- Unique index on Name for active (non-deleted) vendors
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Vendor_Name_Active' AND object_id = OBJECT_ID('dbo.Vendor'))
BEGIN
    CREATE UNIQUE INDEX [UQ_Vendor_Name_Active] ON [dbo].[Vendor] ([Name]) WHERE [IsDeleted] = 0;
END
GO


GO

CREATE OR ALTER PROCEDURE CreateVendor
(
    @Name NVARCHAR(450),
    @Abbreviation NVARCHAR(255),
    @VendorTypeId BIGINT NULL,
    @TaxpayerId BIGINT NULL,
    @IsDraft BIT = 1,
    @IsContractLabor BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Vendor] ([CreatedDatetime], [ModifiedDatetime], [Name], [Abbreviation], [VendorTypeId], [TaxpayerId], [IsDraft], [IsDeleted], [IsContractLabor])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Abbreviation],
        INSERTED.[VendorTypeId],
        INSERTED.[TaxpayerId],
        INSERTED.[IsDraft],
        INSERTED.[IsDeleted],
        INSERTED.[IsContractLabor]
    VALUES (@Now, @Now, @Name, @Abbreviation, @VendorTypeId, @TaxpayerId, @IsDraft, 0, @IsContractLabor);

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadVendors
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [VendorTypeId],
        [TaxpayerId],
        [IsDraft],
        [IsDeleted],
        [IsContractLabor]
    FROM dbo.[Vendor]
    WHERE [IsDeleted] = 0
    ORDER BY [Name] ASC;
END;



GO

CREATE OR ALTER PROCEDURE ReadVendorById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [VendorTypeId],
        [TaxpayerId],
        [IsDraft],
        [IsDeleted],
        [IsContractLabor]
    FROM dbo.[Vendor]
    WHERE [Id] = @Id AND [IsDeleted] = 0;
END;



GO

CREATE OR ALTER PROCEDURE ReadVendorByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [VendorTypeId],
        [TaxpayerId],
        [IsDraft],
        [IsDeleted],
        [IsContractLabor]
    FROM dbo.[Vendor]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;
END;



GO

CREATE OR ALTER PROCEDURE ReadVendorByName
(
    @Name NVARCHAR(450)
)
AS
BEGIN
    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [VendorTypeId],
        [TaxpayerId],
        [IsDraft],
        [IsDeleted],
        [IsContractLabor]
    FROM dbo.[Vendor]
    WHERE [Name] = @Name AND [IsDeleted] = 0;
END;



GO

CREATE OR ALTER PROCEDURE UpdateVendorById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(450),
    @Abbreviation NVARCHAR(255),
    @VendorTypeId BIGINT NULL,
    @TaxpayerId BIGINT NULL,
    @IsDraft BIT = NULL,
    @IsContractLabor BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Verify the record exists and is not deleted
    IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE [Id] = @Id AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Vendor not found.', 16, 1);
        RETURN;
    END

    -- Verify RowVersion matches (optimistic concurrency check)
    IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Concurrency conflict: the vendor record has been modified by another user. Please refresh and try again.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Vendor]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Abbreviation] = @Abbreviation,
        [VendorTypeId] = @VendorTypeId,
        [TaxpayerId] = @TaxpayerId,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END,
        [IsContractLabor] = CASE WHEN @IsContractLabor IS NULL THEN [IsContractLabor] ELSE @IsContractLabor END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Abbreviation],
        INSERTED.[VendorTypeId],
        INSERTED.[TaxpayerId],
        INSERTED.[IsDraft],
        INSERTED.[IsDeleted],
        INSERTED.[IsContractLabor]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE SoftDeleteVendorByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE [PublicId] = @PublicId AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Vendor not found.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Vendor]
    SET
        [ModifiedDatetime] = @Now,
        [IsDeleted] = 1
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Abbreviation],
        INSERTED.[VendorTypeId],
        INSERTED.[TaxpayerId],
        INSERTED.[IsDraft],
        INSERTED.[IsDeleted],
        INSERTED.[IsContractLabor]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
