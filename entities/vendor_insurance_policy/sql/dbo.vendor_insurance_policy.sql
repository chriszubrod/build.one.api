IF OBJECT_ID('dbo.VendorInsurancePolicy', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[VendorInsurancePolicy]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CertificateOfInsuranceId] BIGINT NOT NULL,
    [CoverageType] NVARCHAR(20) NOT NULL,
    [Carrier] NVARCHAR(255) NULL,
    [PolicyNumber] NVARCHAR(255) NULL,
    [EachOccurrence] DECIMAL(18,2) NULL,
    [Aggregate] DECIMAL(18,2) NULL,
    [EffectiveDate] DATE NULL,
    [ExpiryDate] DATE NULL,
    [CreatedByUserId] BIGINT NULL
);
END
GO

CREATE OR ALTER PROCEDURE CreateVendorInsurancePolicy
(
    @CertificateOfInsuranceId BIGINT,
    @CoverageType NVARCHAR(20),
    @Carrier NVARCHAR(255) = NULL,
    @PolicyNumber NVARCHAR(255) = NULL,
    @EachOccurrence DECIMAL(18,2) = NULL,
    @Aggregate DECIMAL(18,2) = NULL,
    @EffectiveDate DATE = NULL,
    @ExpiryDate DATE = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorInsurancePolicy]
        (
            [CreatedDatetime],
            [ModifiedDatetime],
            [CertificateOfInsuranceId],
            [CoverageType],
            [Carrier],
            [PolicyNumber],
            [EachOccurrence],
            [Aggregate],
            [EffectiveDate],
            [ExpiryDate],
            [CreatedByUserId]
        )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CertificateOfInsuranceId],
        INSERTED.[CoverageType],
        INSERTED.[Carrier],
        INSERTED.[PolicyNumber],
        INSERTED.[EachOccurrence],
        INSERTED.[Aggregate],
        CONVERT(VARCHAR(10), INSERTED.[EffectiveDate], 23) AS [EffectiveDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[CreatedByUserId]
    VALUES
        (
            @Now,
            @Now,
            @CertificateOfInsuranceId,
            @CoverageType,
            @Carrier,
            @PolicyNumber,
            @EachOccurrence,
            @Aggregate,
            @EffectiveDate,
            @ExpiryDate,
            COALESCE(@CreatedByUserId, 17)
        );

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorInsurancePolicyById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [CertificateOfInsuranceId],
        [CoverageType],
        [Carrier],
        [PolicyNumber],
        [EachOccurrence],
        [Aggregate],
        CONVERT(VARCHAR(10), [EffectiveDate], 23) AS [EffectiveDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [CreatedByUserId]
    FROM dbo.[VendorInsurancePolicy]
    WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorInsurancePolicyByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [CertificateOfInsuranceId],
        [CoverageType],
        [Carrier],
        [PolicyNumber],
        [EachOccurrence],
        [Aggregate],
        CONVERT(VARCHAR(10), [EffectiveDate], 23) AS [EffectiveDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [CreatedByUserId]
    FROM dbo.[VendorInsurancePolicy]
    WHERE [PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorInsurancePoliciesByCertificateOfInsuranceId
(
    @CertificateOfInsuranceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [CertificateOfInsuranceId],
        [CoverageType],
        [Carrier],
        [PolicyNumber],
        [EachOccurrence],
        [Aggregate],
        CONVERT(VARCHAR(10), [EffectiveDate], 23) AS [EffectiveDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [CreatedByUserId]
    FROM dbo.[VendorInsurancePolicy]
    WHERE [CertificateOfInsuranceId] = @CertificateOfInsuranceId
    ORDER BY [ExpiryDate] ASC;
END;
GO


CREATE OR ALTER PROCEDURE UpdateVendorInsurancePolicyById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @CoverageType NVARCHAR(20) = NULL,
    @Carrier NVARCHAR(255) = NULL,
    @PolicyNumber NVARCHAR(255) = NULL,
    @EachOccurrence DECIMAL(18,2) = NULL,
    @Aggregate DECIMAL(18,2) = NULL,
    @EffectiveDate DATE = NULL,
    @ExpiryDate DATE = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @ExistingRowVersion BINARY(8);
    DECLARE @RowExists BIT = 0;

    SELECT
        @ExistingRowVersion = [RowVersion],
        @RowExists = 1
    FROM dbo.[VendorInsurancePolicy] WITH (UPDLOCK)
    WHERE [Id] = @Id;

    IF @RowExists = 0 OR @ExistingRowVersion <> @RowVersion
    BEGIN
        COMMIT TRANSACTION;
        RETURN;
    END;

    UPDATE dbo.[VendorInsurancePolicy]
    SET
        [ModifiedDatetime] = @Now,
        [CoverageType] = CASE WHEN @CoverageType IS NULL THEN [CoverageType] ELSE @CoverageType END,
        [Carrier] = CASE WHEN @Carrier IS NULL THEN [Carrier] ELSE @Carrier END,
        [PolicyNumber] = CASE WHEN @PolicyNumber IS NULL THEN [PolicyNumber] ELSE @PolicyNumber END,
        [EachOccurrence] = CASE WHEN @EachOccurrence IS NULL THEN [EachOccurrence] ELSE @EachOccurrence END,
        [Aggregate] = CASE WHEN @Aggregate IS NULL THEN [Aggregate] ELSE @Aggregate END,
        [EffectiveDate] = CASE WHEN @EffectiveDate IS NULL THEN [EffectiveDate] ELSE @EffectiveDate END,
        [ExpiryDate] = CASE WHEN @ExpiryDate IS NULL THEN [ExpiryDate] ELSE @ExpiryDate END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CertificateOfInsuranceId],
        INSERTED.[CoverageType],
        INSERTED.[Carrier],
        INSERTED.[PolicyNumber],
        INSERTED.[EachOccurrence],
        INSERTED.[Aggregate],
        CONVERT(VARCHAR(10), INSERTED.[EffectiveDate], 23) AS [EffectiveDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[CreatedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteVendorInsurancePolicyById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[VendorInsurancePolicy]
    OUTPUT DELETED.[Id]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorInsurancePolicy_CertificateOfInsurance')
BEGIN
    ALTER TABLE [dbo].[VendorInsurancePolicy]
    ADD CONSTRAINT [FK_VendorInsurancePolicy_CertificateOfInsurance]
        FOREIGN KEY ([CertificateOfInsuranceId]) REFERENCES [dbo].[CertificateOfInsurance]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_VendorInsurancePolicy_CoverageType')
BEGIN
    ALTER TABLE [dbo].[VendorInsurancePolicy]
    ADD CONSTRAINT [CK_VendorInsurancePolicy_CoverageType]
        CHECK ([CoverageType] IN ('GL', 'WC', 'OTHER'));
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VendorInsurancePolicy_CertificateOfInsuranceId' AND object_id = OBJECT_ID('dbo.VendorInsurancePolicy'))
BEGIN
    CREATE INDEX [IX_VendorInsurancePolicy_CertificateOfInsuranceId]
        ON [dbo].[VendorInsurancePolicy] ([CertificateOfInsuranceId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VendorInsurancePolicy_PublicId' AND object_id = OBJECT_ID('dbo.VendorInsurancePolicy'))
BEGIN
    CREATE INDEX [IX_VendorInsurancePolicy_PublicId]
        ON [dbo].[VendorInsurancePolicy] ([PublicId]);
END
GO
