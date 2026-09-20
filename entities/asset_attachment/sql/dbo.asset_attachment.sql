GO

IF OBJECT_ID('dbo.AssetAttachment', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[AssetAttachment]
(
    [Id] INT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [AssetId] INT NOT NULL,
    [AttachmentId] BIGINT NOT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_AssetAttachment_PublicId' AND object_id = OBJECT_ID('dbo.AssetAttachment'))
BEGIN
    CREATE UNIQUE INDEX UQ_AssetAttachment_PublicId ON dbo.[AssetAttachment] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AssetAttachment_Asset')
BEGIN
    ALTER TABLE dbo.[AssetAttachment] ADD CONSTRAINT FK_AssetAttachment_Asset
        FOREIGN KEY ([AssetId]) REFERENCES dbo.[Asset]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AssetAttachment_Attachment')
BEGIN
    ALTER TABLE dbo.[AssetAttachment] ADD CONSTRAINT FK_AssetAttachment_Attachment
        FOREIGN KEY ([AttachmentId]) REFERENCES dbo.[Attachment]([Id]);
END
GO

CREATE OR ALTER PROCEDURE CreateAssetAttachment
(
    @AssetId INT,
    @AttachmentId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[AssetAttachment] ([CreatedDatetime], [ModifiedDatetime], [AssetId], [AttachmentId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[AssetId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @AssetId, @AttachmentId);

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetAttachments
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [AssetId],
        [AttachmentId]
    FROM dbo.[AssetAttachment]
    ORDER BY [AssetId] ASC, [AttachmentId] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetAttachmentByPublicId
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
        [AssetId],
        [AttachmentId]
    FROM dbo.[AssetAttachment]
    WHERE [PublicId] = @PublicId;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetAttachmentsByAssetId
(
    @AssetId INT
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
        [AssetId],
        [AttachmentId]
    FROM dbo.[AssetAttachment]
    WHERE [AssetId] = @AssetId
    ORDER BY [CreatedDatetime] DESC;
END;
GO

CREATE OR ALTER PROCEDURE DeleteAssetAttachmentById
(
    @Id INT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[AssetAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[AssetId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteAssetAttachmentsByAssetId
(
    @AssetId INT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[AssetAttachment]
    WHERE [AssetId] = @AssetId;

    COMMIT TRANSACTION;
END;
GO
