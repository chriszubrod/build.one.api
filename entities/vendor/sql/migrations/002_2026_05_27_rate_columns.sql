-- =============================================================================
-- 2026-05-27 — Phase 2: rate storage in DB.
--
-- Adds Vendor.HourlyRate + Vendor.Markup columns (default rates per vendor).
-- Re-issues CreateVendor + UpdateVendorById with the new params.
-- Per-project overrides live in dbo.VendorProjectRate (separate migration).
--
-- Default-rate lookup precedence at aggregation time (Phase 4):
--   1. dbo.VendorProjectRate (VendorId, ProjectId) override row → rate, markup
--   2. dbo.Vendor.HourlyRate, Vendor.Markup (this column)
--   3. ERROR — aggregation refuses to write $0 silently
--
-- Idempotent. Safe to re-run.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- Column additions ------------------------------------------------------------
IF COL_LENGTH('dbo.[Vendor]', 'HourlyRate') IS NULL
    ALTER TABLE [dbo].[Vendor] ADD [HourlyRate] DECIMAL(18,4) NULL;
GO

IF COL_LENGTH('dbo.[Vendor]', 'Markup') IS NULL
    ALTER TABLE [dbo].[Vendor] ADD [Markup] DECIMAL(18,4) NULL;
GO


-- Re-issue Read sprocs to include the new columns -----------------------------
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
        [Markup]
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
        [Markup]
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
        [Markup]
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
        [Markup]
    FROM dbo.[Vendor]
    WHERE [Name] = @Name AND [IsDeleted] = 0;
END;
GO


-- Re-issue Create/Update with rate params -------------------------------------
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
    @Markup DECIMAL(18,4) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Vendor]
        ([CreatedDatetime], [ModifiedDatetime], [Name], [Abbreviation], [VendorTypeId], [TaxpayerId],
         [IsDraft], [IsDeleted], [IsContractLabor], [Notes], [CreatedByUserId], [HourlyRate], [Markup])
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
        INSERTED.[Markup]
    VALUES (@Now, @Now, @Name, @Abbreviation, @VendorTypeId, @TaxpayerId, @IsDraft, 0, @IsContractLabor, @Notes,
            COALESCE(@CreatedByUserId, 17), @HourlyRate, @Markup);

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
    @Markup DECIMAL(18,4) = NULL
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
        [Markup]     = CASE WHEN @Markup     IS NULL THEN [Markup]     ELSE @Markup     END
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
        INSERTED.[Markup]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

PRINT 'Vendor rate-column migration applied: HourlyRate/Markup added + Read/Create/Update sprocs extended.';
