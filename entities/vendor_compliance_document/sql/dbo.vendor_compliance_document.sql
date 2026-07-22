IF OBJECT_ID('dbo.VendorComplianceDocument', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[VendorComplianceDocument]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NOT NULL,
    [DocumentType] NVARCHAR(40) NOT NULL,
    [IssuingAuthority] NVARCHAR(255) NULL,
    [DocumentNumber] NVARCHAR(255) NULL,
    [Classification] NVARCHAR(255) NULL,
    [IssueDate] DATE NULL,
    [ExpiryDate] DATE NULL,
    [AttachmentId] BIGINT NULL,
    [VerificationStatus] NVARCHAR(20) NOT NULL CONSTRAINT DF_VendorComplianceDocument_VerificationStatus DEFAULT ('Received'),
    [CreatedByUserId] BIGINT NULL
);
END
GO

CREATE OR ALTER PROCEDURE CreateVendorComplianceDocument
(
    @VendorId BIGINT,
    @DocumentType NVARCHAR(40),
    @IssuingAuthority NVARCHAR(255) = NULL,
    @DocumentNumber NVARCHAR(255) = NULL,
    @Classification NVARCHAR(255) = NULL,
    @IssueDate DATE = NULL,
    @ExpiryDate DATE = NULL,
    @AttachmentId BIGINT = NULL,
    @VerificationStatus NVARCHAR(20) = 'Received',
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorComplianceDocument]
        (
            [CreatedDatetime],
            [ModifiedDatetime],
            [VendorId],
            [DocumentType],
            [IssuingAuthority],
            [DocumentNumber],
            [Classification],
            [IssueDate],
            [ExpiryDate],
            [AttachmentId],
            [VerificationStatus],
            [CreatedByUserId]
        )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[DocumentType],
        INSERTED.[IssuingAuthority],
        INSERTED.[DocumentNumber],
        INSERTED.[Classification],
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[AttachmentId],
        INSERTED.[VerificationStatus],
        INSERTED.[CreatedByUserId]
    VALUES
        (
            @Now,
            @Now,
            @VendorId,
            @DocumentType,
            @IssuingAuthority,
            @DocumentNumber,
            @Classification,
            @IssueDate,
            @ExpiryDate,
            @AttachmentId,
            @VerificationStatus,
            COALESCE(@CreatedByUserId, 17)
        );
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorComplianceDocumentById
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
        [VendorId],
        [DocumentType],
        [IssuingAuthority],
        [DocumentNumber],
        [Classification],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [AttachmentId],
        [VerificationStatus],
        [CreatedByUserId]
    FROM dbo.[VendorComplianceDocument]
    WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorComplianceDocumentByPublicId
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
        [VendorId],
        [DocumentType],
        [IssuingAuthority],
        [DocumentNumber],
        [Classification],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [AttachmentId],
        [VerificationStatus],
        [CreatedByUserId]
    FROM dbo.[VendorComplianceDocument]
    WHERE [PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorComplianceDocumentsByVendorId
(
    @VendorId BIGINT
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [DocumentType],
        [IssuingAuthority],
        [DocumentNumber],
        [Classification],
        CONVERT(VARCHAR(10), [IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), [ExpiryDate], 23) AS [ExpiryDate],
        [AttachmentId],
        [VerificationStatus],
        [CreatedByUserId]
    FROM dbo.[VendorComplianceDocument]
    WHERE [VendorId] = @VendorId
    ORDER BY [DocumentType], [CreatedDatetime] DESC;
END;
GO


CREATE OR ALTER PROCEDURE UpdateVendorComplianceDocumentById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @DocumentType NVARCHAR(40) = NULL,
    @IssuingAuthority NVARCHAR(255) = NULL,
    @DocumentNumber NVARCHAR(255) = NULL,
    @Classification NVARCHAR(255) = NULL,
    @IssueDate DATE = NULL,
    @ExpiryDate DATE = NULL,
    @AttachmentId BIGINT = NULL,
    @VerificationStatus NVARCHAR(20) = NULL
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
    FROM dbo.[VendorComplianceDocument] WITH (UPDLOCK)
    WHERE [Id] = @Id;

    IF @RowExists = 0 OR @ExistingRowVersion <> @RowVersion
    BEGIN
        COMMIT TRANSACTION;
        RETURN;
    END;

    UPDATE dbo.[VendorComplianceDocument]
    SET
        [ModifiedDatetime] = @Now,
        [DocumentType] = CASE WHEN @DocumentType IS NULL THEN [DocumentType] ELSE @DocumentType END,
        [IssuingAuthority] = CASE WHEN @IssuingAuthority IS NULL THEN [IssuingAuthority] ELSE @IssuingAuthority END,
        [DocumentNumber] = CASE WHEN @DocumentNumber IS NULL THEN [DocumentNumber] ELSE @DocumentNumber END,
        [Classification] = CASE WHEN @Classification IS NULL THEN [Classification] ELSE @Classification END,
        [IssueDate] = CASE WHEN @IssueDate IS NULL THEN [IssueDate] ELSE @IssueDate END,
        [ExpiryDate] = CASE WHEN @ExpiryDate IS NULL THEN [ExpiryDate] ELSE @ExpiryDate END,
        [AttachmentId] = CASE WHEN @AttachmentId IS NULL THEN [AttachmentId] ELSE @AttachmentId END,
        [VerificationStatus] = CASE WHEN @VerificationStatus IS NULL THEN [VerificationStatus] ELSE @VerificationStatus END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[DocumentType],
        INSERTED.[IssuingAuthority],
        INSERTED.[DocumentNumber],
        INSERTED.[Classification],
        CONVERT(VARCHAR(10), INSERTED.[IssueDate], 23) AS [IssueDate],
        CONVERT(VARCHAR(10), INSERTED.[ExpiryDate], 23) AS [ExpiryDate],
        INSERTED.[AttachmentId],
        INSERTED.[VerificationStatus],
        INSERTED.[CreatedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteVendorComplianceDocumentById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    DELETE FROM dbo.[VendorInsurancePolicy] WHERE [VendorComplianceDocumentId] = @Id;
    DELETE FROM dbo.[VendorComplianceDocument] OUTPUT DELETED.[Id] WHERE [Id] = @Id;
    COMMIT TRANSACTION;
END;
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorComplianceDocument_Vendor')
BEGIN
    ALTER TABLE [dbo].[VendorComplianceDocument]
    ADD CONSTRAINT [FK_VendorComplianceDocument_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorComplianceDocument_Attachment')
BEGIN
    ALTER TABLE [dbo].[VendorComplianceDocument]
    ADD CONSTRAINT [FK_VendorComplianceDocument_Attachment] FOREIGN KEY ([AttachmentId]) REFERENCES [dbo].[Attachment]([Id]);
END
GO

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_VendorComplianceDocument_DocumentType'
      AND parent_object_id = OBJECT_ID('dbo.VendorComplianceDocument')
)
BEGIN
    ALTER TABLE [dbo].[VendorComplianceDocument]
    DROP CONSTRAINT [CK_VendorComplianceDocument_DocumentType];
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_VendorComplianceDocument_DocumentType'
      AND parent_object_id = OBJECT_ID('dbo.VendorComplianceDocument')
)
BEGIN
    ALTER TABLE [dbo].[VendorComplianceDocument]
    ADD CONSTRAINT [CK_VendorComplianceDocument_DocumentType]
        CHECK ([DocumentType] IN ('CERTIFICATE_OF_INSURANCE'));
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_VendorComplianceDocument_VerificationStatus')
BEGIN
    ALTER TABLE [dbo].[VendorComplianceDocument]
    ADD CONSTRAINT [CK_VendorComplianceDocument_VerificationStatus]
        CHECK ([VerificationStatus] IN ('Received', 'Verified', 'Rejected'));
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_VendorComplianceDocument_AttachmentId' AND object_id = OBJECT_ID('dbo.VendorComplianceDocument'))
BEGIN
    CREATE UNIQUE INDEX [UQ_VendorComplianceDocument_AttachmentId]
        ON [dbo].[VendorComplianceDocument] ([AttachmentId])
        WHERE [AttachmentId] IS NOT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VendorComplianceDocument_VendorId' AND object_id = OBJECT_ID('dbo.VendorComplianceDocument'))
BEGIN
    CREATE INDEX [IX_VendorComplianceDocument_VendorId]
        ON [dbo].[VendorComplianceDocument] ([VendorId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VendorComplianceDocument_PublicId' AND object_id = OBJECT_ID('dbo.VendorComplianceDocument'))
BEGIN
    CREATE INDEX [IX_VendorComplianceDocument_PublicId]
        ON [dbo].[VendorComplianceDocument] ([PublicId]);
END
GO
