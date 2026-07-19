IF OBJECT_ID('dbo.VendorInsurancePolicy', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[VendorInsurancePolicy]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorComplianceDocumentId] BIGINT NOT NULL,
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

CREATE OR ALTER PROCEDURE RecomputeComplianceDocumentExpiryFromPolicies
(
    @VendorComplianceDocumentId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE dbo.[VendorComplianceDocument]
    SET [ExpiryDate] = (SELECT MIN([ExpiryDate]) FROM dbo.[VendorInsurancePolicy]
                        WHERE [VendorComplianceDocumentId] = @VendorComplianceDocumentId),
        [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [Id] = @VendorComplianceDocumentId
      AND [DocumentType] = 'CERTIFICATE_OF_INSURANCE';
END;
GO

CREATE OR ALTER PROCEDURE CreateVendorInsurancePolicy
(
    @VendorComplianceDocumentId BIGINT,
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
            [VendorComplianceDocumentId],
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
        INSERTED.[VendorComplianceDocumentId],
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
            @VendorComplianceDocumentId,
            @CoverageType,
            @Carrier,
            @PolicyNumber,
            @EachOccurrence,
            @Aggregate,
            @EffectiveDate,
            @ExpiryDate,
            COALESCE(@CreatedByUserId, 17)
        );

    EXEC RecomputeComplianceDocumentExpiryFromPolicies @VendorComplianceDocumentId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorInsurancePolicyById
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
        [VendorComplianceDocumentId],
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
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorComplianceDocumentId],
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


CREATE OR ALTER PROCEDURE ReadVendorInsurancePoliciesByComplianceDocumentId
(
    @VendorComplianceDocumentId BIGINT
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorComplianceDocumentId],
        [CoverageType],
        [Carrier],
        [PolicyNumber],
        [EachOccurrence],
        [Aggregate],
        CONVERT(VARCHAR(10), [EffectiveDate], 23) AS [EffectiveDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [CreatedByUserId]
    FROM dbo.[VendorInsurancePolicy]
    WHERE [VendorComplianceDocumentId] = @VendorComplianceDocumentId
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
    DECLARE @DocId BIGINT;
    DECLARE @ExistingRowVersion BINARY(8);
    DECLARE @RowExists BIT = 0;

    SELECT
        @DocId = [VendorComplianceDocumentId],
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
        INSERTED.[VendorComplianceDocumentId],
        INSERTED.[CoverageType],
        INSERTED.[Carrier],
        INSERTED.[PolicyNumber],
        INSERTED.[EachOccurrence],
        INSERTED.[Aggregate],
        CONVERT(VARCHAR(10), INSERTED.[EffectiveDate], 23) AS [EffectiveDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[CreatedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    EXEC RecomputeComplianceDocumentExpiryFromPolicies @DocId;

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

    DECLARE @DocId BIGINT;

    SELECT @DocId = [VendorComplianceDocumentId]
    FROM dbo.[VendorInsurancePolicy]
    WHERE [Id] = @Id;

    DELETE FROM dbo.[VendorInsurancePolicy]
    OUTPUT DELETED.[Id]
    WHERE [Id] = @Id;

    EXEC RecomputeComplianceDocumentExpiryFromPolicies @DocId;

    COMMIT TRANSACTION;
END;
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorInsurancePolicy_ComplianceDocument')
BEGIN
    ALTER TABLE [dbo].[VendorInsurancePolicy]
    ADD CONSTRAINT [FK_VendorInsurancePolicy_ComplianceDocument]
        FOREIGN KEY ([VendorComplianceDocumentId]) REFERENCES [dbo].[VendorComplianceDocument]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_VendorInsurancePolicy_CoverageType')
BEGIN
    ALTER TABLE [dbo].[VendorInsurancePolicy]
    ADD CONSTRAINT [CK_VendorInsurancePolicy_CoverageType]
        CHECK ([CoverageType] IN ('GL', 'AUTO', 'UMBRELLA', 'WC'));
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VendorInsurancePolicy_ComplianceDocumentId' AND object_id = OBJECT_ID('dbo.VendorInsurancePolicy'))
BEGIN
    CREATE INDEX [IX_VendorInsurancePolicy_ComplianceDocumentId]
        ON [dbo].[VendorInsurancePolicy] ([VendorComplianceDocumentId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VendorInsurancePolicy_PublicId' AND object_id = OBJECT_ID('dbo.VendorInsurancePolicy'))
BEGIN
    CREATE INDEX [IX_VendorInsurancePolicy_PublicId]
        ON [dbo].[VendorInsurancePolicy] ([PublicId]);
END
GO
