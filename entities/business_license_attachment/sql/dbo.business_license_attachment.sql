GO

IF OBJECT_ID('dbo.BusinessLicenseAttachment', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BusinessLicenseAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BusinessLicenseId] BIGINT NOT NULL,
    [AttachmentId] BIGINT NOT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BusinessLicenseAttachment_PublicId' AND object_id = OBJECT_ID('dbo.BusinessLicenseAttachment'))
BEGIN
    CREATE UNIQUE INDEX UQ_BusinessLicenseAttachment_PublicId ON dbo.[BusinessLicenseAttachment] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BusinessLicenseAttachment_BusinessLicense')
BEGIN
    ALTER TABLE dbo.[BusinessLicenseAttachment] ADD CONSTRAINT FK_BusinessLicenseAttachment_BusinessLicense
        FOREIGN KEY ([BusinessLicenseId]) REFERENCES dbo.[BusinessLicense]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BusinessLicenseAttachment_Attachment')
BEGIN
    ALTER TABLE dbo.[BusinessLicenseAttachment] ADD CONSTRAINT FK_BusinessLicenseAttachment_Attachment
        FOREIGN KEY ([AttachmentId]) REFERENCES dbo.[Attachment]([Id]);
END
GO

CREATE OR ALTER PROCEDURE CreateBusinessLicenseAttachment
(
    @BusinessLicenseId BIGINT,
    @AttachmentId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BusinessLicenseAttachment] ([CreatedDatetime], [ModifiedDatetime], [BusinessLicenseId], [AttachmentId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BusinessLicenseId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @BusinessLicenseId, @AttachmentId);

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadBusinessLicenseAttachments
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BusinessLicenseId],
        [AttachmentId]
    FROM dbo.[BusinessLicenseAttachment]
    ORDER BY [BusinessLicenseId] ASC, [AttachmentId] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadBusinessLicenseAttachmentById
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
        [BusinessLicenseId],
        [AttachmentId]
    FROM dbo.[BusinessLicenseAttachment]
    WHERE [Id] = @Id;
END;
GO

CREATE OR ALTER PROCEDURE ReadBusinessLicenseAttachmentByPublicId
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
        [BusinessLicenseId],
        [AttachmentId]
    FROM dbo.[BusinessLicenseAttachment]
    WHERE [PublicId] = @PublicId;
END;
GO

CREATE OR ALTER PROCEDURE ReadBusinessLicenseAttachmentsByBusinessLicenseId
(
    @BusinessLicenseId BIGINT
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
        [BusinessLicenseId],
        [AttachmentId]
    FROM dbo.[BusinessLicenseAttachment]
    WHERE [BusinessLicenseId] = @BusinessLicenseId
    ORDER BY [CreatedDatetime] DESC;
END;
GO

CREATE OR ALTER PROCEDURE DeleteBusinessLicenseAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[BusinessLicenseAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BusinessLicenseId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
