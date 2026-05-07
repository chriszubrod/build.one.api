-- =====================================================================
-- Gap 2 Phase Reference — thread CreatedByUserId on the 6 reference-
-- entity Create sprocs.
--
-- Pattern: add @CreatedByUserId BIGINT = NULL param; INSERT uses
-- COALESCE(@CreatedByUserId, 17) preserving the DEFAULT-trick fallback
-- for scheduler / system context / seed scripts.
--
-- Reference entities churn rarely — most rows are seeded once and
-- never touched again. CreatedByUserId is mostly attribution audit data
-- ("who added this Vendor on 2026-05-12") rather than a row-scoping
-- key.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =====================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ===== 1. CreateVendor =====
CREATE OR ALTER PROCEDURE CreateVendor
(
    @Name NVARCHAR(450),
    @Abbreviation NVARCHAR(255),
    @VendorTypeId BIGINT NULL,
    @TaxpayerId BIGINT NULL,
    @IsDraft BIT = 1,
    @IsContractLabor BIT = 0,
    @Notes NVARCHAR(MAX) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Vendor] ([CreatedDatetime], [ModifiedDatetime], [Name], [Abbreviation], [VendorTypeId], [TaxpayerId], [IsDraft], [IsDeleted], [IsContractLabor], [Notes], [CreatedByUserId])
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
        INSERTED.[Notes]
    VALUES (@Now, @Now, @Name, @Abbreviation, @VendorTypeId, @TaxpayerId, @IsDraft, 0, @IsContractLabor, @Notes, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 2. CreateCustomer =====
CREATE OR ALTER PROCEDURE CreateCustomer
(
    @Name NVARCHAR(50),
    @Email NVARCHAR(255),
    @Phone NVARCHAR(50),
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Customer] ([CreatedDatetime], [ModifiedDatetime], [Name], [Email], [Phone], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Email],
        INSERTED.[Phone]
    VALUES (@Now, @Now, @Name, @Email, @Phone, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 3. CreateSubCostCode =====
-- Returns via vw_SubCostCode; just add column to INSERT, view handles passthrough.
CREATE OR ALTER PROCEDURE CreateSubCostCode
(
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL,
    @CostCodeId BIGINT,
    @Aliases NVARCHAR(500) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[SubCostCode] ([CreatedDatetime], [ModifiedDatetime], [Number], [Name], [Description], [CostCodeId], [Aliases], [CreatedByUserId])
    VALUES (@Now, @Now, @Number, @Name, @Description, @CostCodeId, @Aliases, COALESCE(@CreatedByUserId, 17));

    SELECT * FROM dbo.[vw_SubCostCode] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO

-- ===== 4. CreateCostCode =====
CREATE OR ALTER PROCEDURE CreateCostCode
(
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[CostCode] ([CreatedDatetime], [ModifiedDatetime], [Number], [Name], [Description], [CreatedByUserId])
    VALUES (@Now, @Now, @Number, @Name, @Description, COALESCE(@CreatedByUserId, 17));

    SELECT * FROM dbo.[vw_CostCode] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO

-- ===== 5. CreatePaymentTerm =====
CREATE OR ALTER PROCEDURE CreatePaymentTerm
(
    @Name NVARCHAR(50),
    @Description NVARCHAR(255),
    @DiscountPercent DECIMAL(5,2) NULL,
    @DiscountDays INT NULL,
    @DueDays INT NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[PaymentTerm] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [DiscountPercent], [DiscountDays], [DueDays], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[DiscountPercent],
        INSERTED.[DiscountDays],
        INSERTED.[DueDays]
    VALUES (@Now, @Now, @Name, @Description, @DiscountPercent, @DiscountDays, @DueDays, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 6. CreateProjectAddress =====
CREATE OR ALTER PROCEDURE CreateProjectAddress
(
    @ProjectId BIGINT,
    @AddressId BIGINT,
    @AddressTypeId BIGINT,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ProjectAddress] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [AddressId], [AddressTypeId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[AddressId],
        INSERTED.[AddressTypeId]
    VALUES (@Now, @Now, @ProjectId, @AddressId, @AddressTypeId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

PRINT 'Gap 2 Phase Reference: 6 Create sprocs threaded with @CreatedByUserId';
GO
