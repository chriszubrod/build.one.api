GO

IF OBJECT_ID('dbo.ContractorsLicenseAttachment', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ContractorsLicenseAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ContractorsLicenseId] BIGINT NOT NULL,
    [AttachmentId] BIGINT NOT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_ContractorsLicenseAttachment_PublicId' AND object_id = OBJECT_ID('dbo.ContractorsLicenseAttachment'))
BEGIN
    CREATE UNIQUE INDEX UQ_ContractorsLicenseAttachment_PublicId ON dbo.[ContractorsLicenseAttachment] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ContractorsLicenseAttachment_ContractorsLicense')
BEGIN
    ALTER TABLE dbo.[ContractorsLicenseAttachment] ADD CONSTRAINT FK_ContractorsLicenseAttachment_ContractorsLicense
        FOREIGN KEY ([ContractorsLicenseId]) REFERENCES dbo.[ContractorsLicense]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ContractorsLicenseAttachment_Attachment')
BEGIN
    ALTER TABLE dbo.[ContractorsLicenseAttachment] ADD CONSTRAINT FK_ContractorsLicenseAttachment_Attachment
        FOREIGN KEY ([AttachmentId]) REFERENCES dbo.[Attachment]([Id]);
END
GO

CREATE OR ALTER PROCEDURE CreateContractorsLicenseAttachment
(
    @ContractorsLicenseId BIGINT,
    @AttachmentId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ContractorsLicenseAttachment] ([CreatedDatetime], [ModifiedDatetime], [ContractorsLicenseId], [AttachmentId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ContractorsLicenseId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @ContractorsLicenseId, @AttachmentId);

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractorsLicenseAttachments
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [ContractorsLicenseId],
        [AttachmentId]
    FROM dbo.[ContractorsLicenseAttachment]
    ORDER BY [ContractorsLicenseId] ASC, [AttachmentId] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractorsLicenseAttachmentById
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
        [ContractorsLicenseId],
        [AttachmentId]
    FROM dbo.[ContractorsLicenseAttachment]
    WHERE [Id] = @Id;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractorsLicenseAttachmentByPublicId
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
        [ContractorsLicenseId],
        [AttachmentId]
    FROM dbo.[ContractorsLicenseAttachment]
    WHERE [PublicId] = @PublicId;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractorsLicenseAttachmentsByContractorsLicenseId
(
    @ContractorsLicenseId BIGINT
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
        [ContractorsLicenseId],
        [AttachmentId]
    FROM dbo.[ContractorsLicenseAttachment]
    WHERE [ContractorsLicenseId] = @ContractorsLicenseId
    ORDER BY [CreatedDatetime] DESC;
END;
GO

CREATE OR ALTER PROCEDURE DeleteContractorsLicenseAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[ContractorsLicenseAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ContractorsLicenseId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
