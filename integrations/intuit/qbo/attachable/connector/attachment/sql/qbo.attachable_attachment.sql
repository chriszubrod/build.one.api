-- Attachable to Attachment Mapping Table
-- Links QBO Attachable to BuildOne Attachment (1:1 relationship)

DROP TABLE IF EXISTS [qbo].[AttachableAttachment];
GO

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
GO


DROP PROCEDURE IF EXISTS CreateAttachableAttachment;
GO

CREATE PROCEDURE CreateAttachableAttachment
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


DROP PROCEDURE IF EXISTS ReadAttachableAttachmentById;
GO

CREATE PROCEDURE ReadAttachableAttachmentById
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


DROP PROCEDURE IF EXISTS ReadAttachableAttachmentByAttachmentId;
GO

CREATE PROCEDURE ReadAttachableAttachmentByAttachmentId
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


DROP PROCEDURE IF EXISTS ReadAttachableAttachmentByQboAttachableId;
GO

CREATE PROCEDURE ReadAttachableAttachmentByQboAttachableId
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


DROP PROCEDURE IF EXISTS DeleteAttachableAttachmentById;
GO

CREATE PROCEDURE DeleteAttachableAttachmentById
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
