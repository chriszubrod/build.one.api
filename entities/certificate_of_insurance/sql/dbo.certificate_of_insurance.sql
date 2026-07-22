IF OBJECT_ID('dbo.CertificateOfInsurance', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[CertificateOfInsurance]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_CertificateOfInsurance_PublicId DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL CONSTRAINT DF_CertificateOfInsurance_CreatedDatetime DEFAULT (SYSUTCDATETIME()),
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CreatedByUserId] BIGINT NULL CONSTRAINT DF_CertificateOfInsurance_CreatedByUserId DEFAULT (17),
    [VendorId] BIGINT NOT NULL,
    [IssuingAuthority] NVARCHAR(255) NULL,
    [IssueDate] DATE NULL,
    [AttachmentId] BIGINT NULL,
    [VerificationStatus] NVARCHAR(20) NOT NULL CONSTRAINT DF_CertificateOfInsurance_VerificationStatus DEFAULT ('Received'),
    [IsDeleted] BIT NOT NULL CONSTRAINT DF_CertificateOfInsurance_IsDeleted DEFAULT (0)
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_CertificateOfInsurance_PublicId' AND object_id = OBJECT_ID('dbo.CertificateOfInsurance'))
BEGIN
    CREATE UNIQUE INDEX UQ_CertificateOfInsurance_PublicId ON dbo.[CertificateOfInsurance] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_CertificateOfInsurance_VendorId' AND object_id = OBJECT_ID('dbo.CertificateOfInsurance'))
BEGIN
    CREATE INDEX IX_CertificateOfInsurance_VendorId ON dbo.[CertificateOfInsurance] ([VendorId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_CertificateOfInsurance_VerificationStatus' AND parent_object_id = OBJECT_ID('dbo.CertificateOfInsurance'))
BEGIN
    ALTER TABLE dbo.[CertificateOfInsurance] ADD CONSTRAINT CK_CertificateOfInsurance_VerificationStatus
        CHECK ([VerificationStatus] IN ('Received', 'Verified', 'Rejected'));
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_CertificateOfInsurance_Vendor')
BEGIN
    ALTER TABLE dbo.[CertificateOfInsurance] ADD CONSTRAINT FK_CertificateOfInsurance_Vendor
        FOREIGN KEY ([VendorId]) REFERENCES dbo.[Vendor]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_CertificateOfInsurance_CreatedByUser')
BEGIN
    ALTER TABLE dbo.[CertificateOfInsurance] ADD CONSTRAINT FK_CertificateOfInsurance_CreatedByUser
        FOREIGN KEY ([CreatedByUserId]) REFERENCES dbo.[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_CertificateOfInsurance_Attachment')
BEGIN
    ALTER TABLE dbo.[CertificateOfInsurance] ADD CONSTRAINT FK_CertificateOfInsurance_Attachment
        FOREIGN KEY ([AttachmentId]) REFERENCES dbo.[Attachment]([Id]);
END
GO

CREATE OR ALTER PROCEDURE CreateCertificateOfInsurance
(
    @VendorId BIGINT,
    @IssuingAuthority NVARCHAR(255) = NULL,
    @IssueDate DATE = NULL,
    @AttachmentId BIGINT = NULL,
    @VerificationStatus NVARCHAR(20) = 'Received',
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    INSERT INTO dbo.[CertificateOfInsurance]
        ([VendorId], [IssuingAuthority], [IssueDate], [AttachmentId], [VerificationStatus], [CreatedByUserId], [IsDeleted])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[VendorId],
        INSERTED.[IssuingAuthority],
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        INSERTED.[AttachmentId],
        INSERTED.[VerificationStatus],
        INSERTED.[IsDeleted]
    VALUES
        (@VendorId, @IssuingAuthority, @IssueDate, @AttachmentId, @VerificationStatus, COALESCE(@CreatedByUserId, 17), 0);

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadCertificatesOfInsurance
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
        [IssuingAuthority],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        [AttachmentId],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[CertificateOfInsurance]
    WHERE [IsDeleted] = 0
    ORDER BY [VendorId] ASC, [IssueDate] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCertificateOfInsuranceById
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
        [IssuingAuthority],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        [AttachmentId],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[CertificateOfInsurance]
    WHERE [Id] = @Id AND [IsDeleted] = 0;
END;
GO

CREATE OR ALTER PROCEDURE ReadCertificateOfInsuranceByPublicId
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
        [IssuingAuthority],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        [AttachmentId],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[CertificateOfInsurance]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;
END;
GO

CREATE OR ALTER PROCEDURE ReadCertificatesOfInsuranceByVendorId
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
        [IssuingAuthority],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        [AttachmentId],
        [VerificationStatus],
        [IsDeleted]
    FROM dbo.[CertificateOfInsurance]
    WHERE [VendorId] = @VendorId AND [IsDeleted] = 0
    ORDER BY [IssueDate] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE UpdateCertificateOfInsuranceById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @IssuingAuthority NVARCHAR(255) = NULL,
    @IssueDate DATE = NULL,
    @AttachmentId BIGINT = NULL,
    @VerificationStatus NVARCHAR(20) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    UPDATE dbo.[CertificateOfInsurance]
    SET
        [ModifiedDatetime] = SYSUTCDATETIME(),
        [IssuingAuthority] = CASE WHEN @IssuingAuthority IS NULL THEN [IssuingAuthority] ELSE @IssuingAuthority END,
        [IssueDate] = CASE WHEN @IssueDate IS NULL THEN [IssueDate] ELSE @IssueDate END,
        [AttachmentId] = CASE WHEN @AttachmentId IS NULL THEN [AttachmentId] ELSE @AttachmentId END,
        [VerificationStatus] = CASE WHEN @VerificationStatus IS NULL THEN [VerificationStatus] ELSE @VerificationStatus END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[VendorId],
        INSERTED.[IssuingAuthority],
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        INSERTED.[AttachmentId],
        INSERTED.[VerificationStatus],
        INSERTED.[IsDeleted]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteCertificateOfInsuranceById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    UPDATE dbo.[CertificateOfInsurance]
    SET [IsDeleted] = 1, [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[VendorId],
        INSERTED.[IssuingAuthority],
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        INSERTED.[AttachmentId],
        INSERTED.[VerificationStatus],
        INSERTED.[IsDeleted]
    WHERE [Id] = @Id AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO
