-- Attachable to Attachment Mapping Table
-- Links QBO Attachable to BuildOne Attachment (1:1 relationship)

GO

IF OBJECT_ID('qbo.AttachableAttachment', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[AttachableAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [AttachmentId] BIGINT NOT NULL,
    [QboAttachableId] BIGINT NOT NULL,
    CONSTRAINT [UQ_AttachableAttachment_AttachmentId] UNIQUE ([AttachmentId]),
    CONSTRAINT [UQ_AttachableAttachment_QboAttachableId] UNIQUE ([QboAttachableId])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateAttachableAttachment
(
    @AttachmentId BIGINT,
    @QboAttachableId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[AttachableAttachment] ([CreatedDatetime], [ModifiedDatetime], [AttachmentId], [QboAttachableId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[AttachmentId],
        INSERTED.[QboAttachableId]
    VALUES (@Now, @Now, @AttachmentId, @QboAttachableId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadAttachableAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [AttachmentId],
        [QboAttachableId]
    FROM [qbo].[AttachableAttachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadAttachableAttachmentByAttachmentId
(
    @AttachmentId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [AttachmentId],
        [QboAttachableId]
    FROM [qbo].[AttachableAttachment]
    WHERE [AttachmentId] = @AttachmentId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadAttachableAttachmentByQboAttachableId
(
    @QboAttachableId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [AttachmentId],
        [QboAttachableId]
    FROM [qbo].[AttachableAttachment]
    WHERE [QboAttachableId] = @QboAttachableId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteAttachableAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[AttachableAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[AttachmentId],
        DELETED.[QboAttachableId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
