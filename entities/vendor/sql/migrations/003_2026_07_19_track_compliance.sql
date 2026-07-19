-- Migration 003 (U-089): Vendor.TrackCompliance roster flag.
-- =============================================================================
-- Adds Vendor.TrackCompliance (compliance-roster flag, default 0).
-- Re-issues Read/Create/Update sprocs with the new column.
-- Idempotent. Safe to re-run. Do NOT edit dbo.vendor.sql — apply via this file.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

IF COL_LENGTH('dbo.Vendor', 'TrackCompliance') IS NULL
    ALTER TABLE dbo.[Vendor] ADD [TrackCompliance] BIT NOT NULL CONSTRAINT DF_Vendor_TrackCompliance DEFAULT (0);
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
        [IsContractLabor],
        [Notes],
        [HourlyRate],
        [Markup],
        [TrackCompliance]
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
        [IsContractLabor],
        [Notes],
        [HourlyRate],
        [Markup],
        [TrackCompliance]
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
        [IsContractLabor],
        [Notes],
        [HourlyRate],
        [Markup],
        [TrackCompliance]
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
        [IsContractLabor],
        [Notes],
        [HourlyRate],
        [Markup],
        [TrackCompliance]
    FROM dbo.[Vendor]
    WHERE [Name] = @Name AND [IsDeleted] = 0;
END;
GO

CREATE OR ALTER PROCEDURE CreateVendor
(
    @Name NVARCHAR(450),
    @Abbreviation NVARCHAR(255),
    @VendorTypeId BIGINT NULL,
    @TaxpayerId BIGINT NULL,
    @IsDraft BIT = 1,
    @IsContractLabor BIT = 0,
    @Notes NVARCHAR(MAX) = NULL,
    @CreatedByUserId BIGINT = NULL,
    @HourlyRate DECIMAL(18,4) = NULL,
    @Markup DECIMAL(18,4) = NULL,
    @TrackCompliance BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Vendor]
        ([CreatedDatetime], [ModifiedDatetime], [Name], [Abbreviation], [VendorTypeId], [TaxpayerId],
         [IsDraft], [IsDeleted], [IsContractLabor], [Notes], [CreatedByUserId], [HourlyRate], [Markup],
         [TrackCompliance])
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
        INSERTED.[IsContractLabor],
        INSERTED.[Notes],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[TrackCompliance]
    VALUES (@Now, @Now, @Name, @Abbreviation, @VendorTypeId, @TaxpayerId, @IsDraft, 0, @IsContractLabor, @Notes,
            COALESCE(@CreatedByUserId, 17), @HourlyRate, @Markup, @TrackCompliance);

    COMMIT TRANSACTION;
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
    @IsContractLabor BIT = NULL,
    @Notes NVARCHAR(MAX) = NULL,
    @HourlyRate DECIMAL(18,4) = NULL,
    @Markup DECIMAL(18,4) = NULL,
    @TrackCompliance BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE [Id] = @Id AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Vendor not found.', 16, 1);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Concurrency conflict: the vendor record has been modified by another user. Please refresh and try again.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- HourlyRate / Markup use CASE WHEN preserve-on-NULL — same pattern as
    -- IsDraft / IsContractLabor so callers can update just the rate without
    -- having to re-send every field. Pass an explicit Decimal to overwrite;
    -- pass NULL to preserve the existing value.
    --
    -- Note: this means there's no way to set HourlyRate/Markup back to NULL
    -- via this sproc once they're populated. If that becomes needed, add a
    -- separate ClearVendorRate sproc.
    UPDATE dbo.[Vendor]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Abbreviation] = @Abbreviation,
        [VendorTypeId] = CASE WHEN @VendorTypeId IS NULL THEN [VendorTypeId] ELSE @VendorTypeId END,
        [TaxpayerId] = CASE WHEN @TaxpayerId IS NULL THEN [TaxpayerId] ELSE @TaxpayerId END,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END,
        [IsContractLabor] = CASE WHEN @IsContractLabor IS NULL THEN [IsContractLabor] ELSE @IsContractLabor END,
        [Notes] = @Notes,
        [HourlyRate] = CASE WHEN @HourlyRate IS NULL THEN [HourlyRate] ELSE @HourlyRate END,
        [Markup]     = CASE WHEN @Markup     IS NULL THEN [Markup]     ELSE @Markup     END,
        [TrackCompliance] = CASE WHEN @TrackCompliance IS NULL THEN [TrackCompliance] ELSE @TrackCompliance END
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
        INSERTED.[IsContractLabor],
        INSERTED.[Notes],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[TrackCompliance]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

PRINT 'Vendor TrackCompliance migration applied: column added + Read/Create/Update sprocs extended.';
