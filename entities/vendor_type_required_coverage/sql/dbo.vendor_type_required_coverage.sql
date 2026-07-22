IF OBJECT_ID('dbo.VendorTypeRequiredCoverage', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[VendorTypeRequiredCoverage]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_VendorTypeRequiredCoverage_PublicId DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL CONSTRAINT DF_VendorTypeRequiredCoverage_CreatedDatetime DEFAULT (SYSUTCDATETIME()),
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CreatedByUserId] BIGINT NULL CONSTRAINT DF_VendorTypeRequiredCoverage_CreatedByUserId DEFAULT (17),
    [VendorTypeId] BIGINT NOT NULL,
    [CoverageType] NVARCHAR(20) NOT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_VendorTypeRequiredCoverage_PublicId' AND object_id = OBJECT_ID('dbo.VendorTypeRequiredCoverage'))
BEGIN
    CREATE UNIQUE INDEX UQ_VendorTypeRequiredCoverage_PublicId ON dbo.[VendorTypeRequiredCoverage] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_VendorTypeRequiredCoverage_TypeCoverage' AND object_id = OBJECT_ID('dbo.VendorTypeRequiredCoverage'))
BEGIN
    CREATE UNIQUE INDEX UQ_VendorTypeRequiredCoverage_TypeCoverage ON dbo.[VendorTypeRequiredCoverage] ([VendorTypeId], [CoverageType]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_VendorTypeRequiredCoverage_CoverageType' AND parent_object_id = OBJECT_ID('dbo.VendorTypeRequiredCoverage'))
BEGIN
    ALTER TABLE dbo.[VendorTypeRequiredCoverage] ADD CONSTRAINT CK_VendorTypeRequiredCoverage_CoverageType
        CHECK ([CoverageType] IN ('GL', 'WC'));
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorTypeRequiredCoverage_VendorType')
BEGIN
    ALTER TABLE dbo.[VendorTypeRequiredCoverage] ADD CONSTRAINT FK_VendorTypeRequiredCoverage_VendorType
        FOREIGN KEY ([VendorTypeId]) REFERENCES dbo.[VendorType]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorTypeRequiredCoverage_CreatedByUser')
BEGIN
    ALTER TABLE dbo.[VendorTypeRequiredCoverage] ADD CONSTRAINT FK_VendorTypeRequiredCoverage_CreatedByUser
        FOREIGN KEY ([CreatedByUserId]) REFERENCES dbo.[User]([Id]);
END
GO

CREATE OR ALTER PROCEDURE CreateVendorTypeRequiredCoverage
(
    @VendorTypeId BIGINT,
    @CoverageType NVARCHAR(20),
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    INSERT INTO dbo.[VendorTypeRequiredCoverage]
        ([VendorTypeId], [CoverageType], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[VendorTypeId],
        INSERTED.[CoverageType]
    VALUES
        (@VendorTypeId, @CoverageType, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadVendorTypeRequiredCoverages
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [CreatedByUserId],
        [VendorTypeId],
        [CoverageType]
    FROM dbo.[VendorTypeRequiredCoverage]
    ORDER BY [VendorTypeId] ASC, [CoverageType] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadVendorTypeRequiredCoveragesByVendorTypeId
(
    @VendorTypeId BIGINT
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
        [CreatedByUserId],
        [VendorTypeId],
        [CoverageType]
    FROM dbo.[VendorTypeRequiredCoverage]
    WHERE [VendorTypeId] = @VendorTypeId
    ORDER BY [CoverageType] ASC;
END;
GO

CREATE OR ALTER PROCEDURE DeleteVendorTypeRequiredCoverageById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[VendorTypeRequiredCoverage]
    OUTPUT DELETED.[Id]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteVendorTypeRequiredCoverageByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[VendorTypeRequiredCoverage]
    OUTPUT DELETED.[Id]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO
