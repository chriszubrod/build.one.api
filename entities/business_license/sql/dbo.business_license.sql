IF OBJECT_ID('dbo.BusinessLicense', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BusinessLicense]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_BusinessLicense_PublicId DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL CONSTRAINT DF_BusinessLicense_CreatedDatetime DEFAULT (SYSUTCDATETIME()),
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CreatedByUserId] BIGINT NULL CONSTRAINT DF_BusinessLicense_CreatedByUserId DEFAULT (17),
    [VendorId] BIGINT NOT NULL,
    [LicenseNumber] NVARCHAR(255) NULL,
    [IssuingAuthority] NVARCHAR(255) NULL,
    [IssueDate] DATE NULL,
    [ExpiryDate] DATE NULL,
    [VerificationStatus] NVARCHAR(20) NOT NULL CONSTRAINT DF_BusinessLicense_VerificationStatus DEFAULT ('Received'),
    [IsDeleted] BIT NOT NULL CONSTRAINT DF_BusinessLicense_IsDeleted DEFAULT (0)
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BusinessLicense_PublicId' AND object_id = OBJECT_ID('dbo.BusinessLicense'))
BEGIN
    CREATE UNIQUE INDEX UQ_BusinessLicense_PublicId ON dbo.[BusinessLicense] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BusinessLicense_VendorId' AND object_id = OBJECT_ID('dbo.BusinessLicense'))
BEGIN
    CREATE INDEX IX_BusinessLicense_VendorId ON dbo.[BusinessLicense] ([VendorId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_BusinessLicense_VerificationStatus' AND parent_object_id = OBJECT_ID('dbo.BusinessLicense'))
BEGIN
    ALTER TABLE dbo.[BusinessLicense] ADD CONSTRAINT CK_BusinessLicense_VerificationStatus
        CHECK ([VerificationStatus] IN ('Received', 'Verified', 'Rejected'));
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BusinessLicense_Vendor')
BEGIN
    ALTER TABLE dbo.[BusinessLicense] ADD CONSTRAINT FK_BusinessLicense_Vendor
        FOREIGN KEY ([VendorId]) REFERENCES dbo.[Vendor]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BusinessLicense_CreatedByUser')
BEGIN
    ALTER TABLE dbo.[BusinessLicense] ADD CONSTRAINT FK_BusinessLicense_CreatedByUser
        FOREIGN KEY ([CreatedByUserId]) REFERENCES dbo.[User]([Id]);
END
GO

CREATE OR ALTER PROCEDURE CreateBusinessLicense
(
    @VendorId BIGINT,
    @LicenseNumber NVARCHAR(255) = NULL,
    @IssuingAuthority NVARCHAR(255) = NULL,
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

    INSERT INTO dbo.[BusinessLicense]
        ([VendorId], [LicenseNumber], [IssuingAuthority], [IssueDate], [ExpiryDate], [VerificationStatus], [CreatedByUserId], [IsDeleted])
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
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[VerificationStatus],
        INSERTED.[IsDeleted]
    VALUES
        (@VendorId, @LicenseNumber, @IssuingAuthority, @IssueDate, @ExpiryDate, @VerificationStatus, COALESCE(@CreatedByUserId, 17), 0);

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadBusinessLicenses
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
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[BusinessLicense]
    WHERE [IsDeleted] = 0
    ORDER BY [VendorId] ASC, [ExpiryDate] DESC, [IssueDate] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadBusinessLicenseById
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
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[BusinessLicense]
    WHERE [Id] = @Id AND [IsDeleted] = 0;
END;
GO

CREATE OR ALTER PROCEDURE ReadBusinessLicenseByPublicId
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
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[BusinessLicense]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;
END;
GO

CREATE OR ALTER PROCEDURE ReadBusinessLicensesByVendorId
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
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[BusinessLicense]
    WHERE [VendorId] = @VendorId AND [IsDeleted] = 0
    ORDER BY [ExpiryDate] DESC, [IssueDate] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE UpdateBusinessLicenseById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @LicenseNumber NVARCHAR(255) = NULL,
    @IssuingAuthority NVARCHAR(255) = NULL,
    @IssueDate DATE = NULL,
    @ExpiryDate DATE = NULL,
    @VerificationStatus NVARCHAR(20) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    UPDATE dbo.[BusinessLicense]
    SET
        [ModifiedDatetime] = SYSUTCDATETIME(),
        [LicenseNumber] = CASE WHEN @LicenseNumber IS NULL THEN [LicenseNumber] ELSE @LicenseNumber END,
        [IssuingAuthority] = CASE WHEN @IssuingAuthority IS NULL THEN [IssuingAuthority] ELSE @IssuingAuthority END,
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
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[VerificationStatus],
        INSERTED.[IsDeleted]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteBusinessLicenseById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    UPDATE dbo.[BusinessLicense]
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
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[VerificationStatus],
        INSERTED.[IsDeleted]
    WHERE [Id] = @Id AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO
