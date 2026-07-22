IF OBJECT_ID('dbo.ContractorsLicense', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ContractorsLicense]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_ContractorsLicense_PublicId DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL CONSTRAINT DF_ContractorsLicense_CreatedDatetime DEFAULT (SYSUTCDATETIME()),
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CreatedByUserId] BIGINT NULL CONSTRAINT DF_ContractorsLicense_CreatedByUserId DEFAULT (17),
    [VendorId] BIGINT NOT NULL,
    [LicenseNumber] NVARCHAR(255) NULL,
    [IssuingAuthority] NVARCHAR(255) NULL,
    [Classification] NVARCHAR(255) NULL,
    [IssueDate] DATE NULL,
    [ExpiryDate] DATE NULL,
    [VerificationStatus] NVARCHAR(20) NOT NULL CONSTRAINT DF_ContractorsLicense_VerificationStatus DEFAULT ('Received'),
    [IsDeleted] BIT NOT NULL CONSTRAINT DF_ContractorsLicense_IsDeleted DEFAULT (0)
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_ContractorsLicense_PublicId' AND object_id = OBJECT_ID('dbo.ContractorsLicense'))
BEGIN
    CREATE UNIQUE INDEX UQ_ContractorsLicense_PublicId ON dbo.[ContractorsLicense] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractorsLicense_VendorId' AND object_id = OBJECT_ID('dbo.ContractorsLicense'))
BEGIN
    CREATE INDEX IX_ContractorsLicense_VendorId ON dbo.[ContractorsLicense] ([VendorId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_ContractorsLicense_VerificationStatus' AND parent_object_id = OBJECT_ID('dbo.ContractorsLicense'))
BEGIN
    ALTER TABLE dbo.[ContractorsLicense] ADD CONSTRAINT CK_ContractorsLicense_VerificationStatus
        CHECK ([VerificationStatus] IN ('Received', 'Verified', 'Rejected'));
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ContractorsLicense_Vendor')
BEGIN
    ALTER TABLE dbo.[ContractorsLicense] ADD CONSTRAINT FK_ContractorsLicense_Vendor
        FOREIGN KEY ([VendorId]) REFERENCES dbo.[Vendor]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ContractorsLicense_CreatedByUser')
BEGIN
    ALTER TABLE dbo.[ContractorsLicense] ADD CONSTRAINT FK_ContractorsLicense_CreatedByUser
        FOREIGN KEY ([CreatedByUserId]) REFERENCES dbo.[User]([Id]);
END
GO

CREATE OR ALTER PROCEDURE CreateContractorsLicense
(
    @VendorId BIGINT,
    @LicenseNumber NVARCHAR(255) = NULL,
    @IssuingAuthority NVARCHAR(255) = NULL,
    @Classification NVARCHAR(255) = NULL,
    @IssueDate DATE = NULL,
    @ExpiryDate DATE = NULL,
    @VerificationStatus NVARCHAR(20) = 'Received',
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    INSERT INTO dbo.[ContractorsLicense]
        ([VendorId], [LicenseNumber], [IssuingAuthority], [Classification], [IssueDate], [ExpiryDate], [VerificationStatus], [CreatedByUserId], [IsDeleted])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[VendorId],
        INSERTED.[LicenseNumber],
        INSERTED.[IssuingAuthority],
        INSERTED.[Classification],
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[VerificationStatus],
        INSERTED.[IsDeleted]
    VALUES
        (@VendorId, @LicenseNumber, @IssuingAuthority, @Classification, @IssueDate, @ExpiryDate, @VerificationStatus, COALESCE(@CreatedByUserId, 17), 0);

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractorsLicenses
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
        [VendorId],
        [LicenseNumber],
        [IssuingAuthority],
        [Classification],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[ContractorsLicense]
    WHERE [IsDeleted] = 0
    ORDER BY [VendorId] ASC, [ExpiryDate] DESC, [IssueDate] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractorsLicenseById
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
        [CreatedByUserId],
        [VendorId],
        [LicenseNumber],
        [IssuingAuthority],
        [Classification],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[ContractorsLicense]
    WHERE [Id] = @Id AND [IsDeleted] = 0;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractorsLicenseByPublicId
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
        [CreatedByUserId],
        [VendorId],
        [LicenseNumber],
        [IssuingAuthority],
        [Classification],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[ContractorsLicense]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractorsLicensesByVendorId
(
    @VendorId BIGINT
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
        [VendorId],
        [LicenseNumber],
        [IssuingAuthority],
        [Classification],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[ContractorsLicense]
    WHERE [VendorId] = @VendorId AND [IsDeleted] = 0
    ORDER BY [ExpiryDate] DESC, [IssueDate] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE UpdateContractorsLicenseById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @LicenseNumber NVARCHAR(255) = NULL,
    @IssuingAuthority NVARCHAR(255) = NULL,
    @Classification NVARCHAR(255) = NULL,
    @IssueDate DATE = NULL,
    @ExpiryDate DATE = NULL,
    @VerificationStatus NVARCHAR(20) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    UPDATE dbo.[ContractorsLicense]
    SET
        [ModifiedDatetime] = SYSUTCDATETIME(),
        [LicenseNumber] = CASE WHEN @LicenseNumber IS NULL THEN [LicenseNumber] ELSE @LicenseNumber END,
        [IssuingAuthority] = CASE WHEN @IssuingAuthority IS NULL THEN [IssuingAuthority] ELSE @IssuingAuthority END,
        [Classification] = CASE WHEN @Classification IS NULL THEN [Classification] ELSE @Classification END,
        [IssueDate] = CASE WHEN @IssueDate IS NULL THEN [IssueDate] ELSE @IssueDate END,
        [ExpiryDate] = CASE WHEN @ExpiryDate IS NULL THEN [ExpiryDate] ELSE @ExpiryDate END,
        [VerificationStatus] = CASE WHEN @VerificationStatus IS NULL THEN [VerificationStatus] ELSE @VerificationStatus END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[VendorId],
        INSERTED.[LicenseNumber],
        INSERTED.[IssuingAuthority],
        INSERTED.[Classification],
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[VerificationStatus],
        INSERTED.[IsDeleted]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteContractorsLicenseById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    UPDATE dbo.[ContractorsLicense]
    SET [IsDeleted] = 1, [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[VendorId],
        INSERTED.[LicenseNumber],
        INSERTED.[IssuingAuthority],
        INSERTED.[Classification],
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[VerificationStatus],
        INSERTED.[IsDeleted]
    WHERE [Id] = @Id AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO
