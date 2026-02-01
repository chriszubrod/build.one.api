GO

IF OBJECT_ID('ms.DriveItemAttachment', 'U') IS NULL
BEGIN
CREATE TABLE [ms].[DriveItemAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [AttachmentId] BIGINT NOT NULL,
    [MsDriveItemId] BIGINT NOT NULL,
    CONSTRAINT [UQ_DriveItemAttachment_AttachmentId] UNIQUE ([AttachmentId]),
    CONSTRAINT [UQ_DriveItemAttachment_MsDriveItemId] UNIQUE ([MsDriveItemId]),
    CONSTRAINT [FK_DriveItemAttachment_Attachment] FOREIGN KEY ([AttachmentId]) REFERENCES [dbo].[Attachment]([Id]) ON DELETE CASCADE,
    CONSTRAINT [FK_DriveItemAttachment_DriveItem] FOREIGN KEY ([MsDriveItemId]) REFERENCES [ms].[DriveItem]([Id]) ON DELETE CASCADE
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateDriveItemAttachment
(
    @AttachmentId BIGINT,
    @MsDriveItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[DriveItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [AttachmentId], [MsDriveItemId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[AttachmentId],
        INSERTED.[MsDriveItemId]
    VALUES (@Now, @Now, @AttachmentId, @MsDriveItemId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadDriveItemAttachmentById
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
        [MsDriveItemId]
    FROM [ms].[DriveItemAttachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadDriveItemAttachmentByAttachmentId
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
        [MsDriveItemId]
    FROM [ms].[DriveItemAttachment]
    WHERE [AttachmentId] = @AttachmentId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadDriveItemAttachmentByMsDriveItemId
(
    @MsDriveItemId BIGINT
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
        [MsDriveItemId]
    FROM [ms].[DriveItemAttachment]
    WHERE [MsDriveItemId] = @MsDriveItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteDriveItemAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[AttachmentId],
        DELETED.[MsDriveItemId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteDriveItemAttachmentByAttachmentId
(
    @AttachmentId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[AttachmentId],
        DELETED.[MsDriveItemId]
    WHERE [AttachmentId] = @AttachmentId;

    COMMIT TRANSACTION;
END;
GO

