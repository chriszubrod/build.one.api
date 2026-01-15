DROP TABLE IF EXISTS [dbo].[BillLineItemAttachment];
GO

CREATE TABLE [dbo].[BillLineItemAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BillLineItemId] BIGINT NULL,
    [AttachmentId] BIGINT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateBillLineItemAttachment;
GO

CREATE PROCEDURE CreateBillLineItemAttachment
(
    @BillLineItemId BIGINT,
    @AttachmentId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillLineItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [BillLineItemId], [AttachmentId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillLineItemId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @BillLineItemId, @AttachmentId);

    COMMIT TRANSACTION;
END;

EXEC CreateBillLineItemAttachment
    @BillLineItemId = 1,
    @AttachmentId = 1;
GO


DROP PROCEDURE IF EXISTS ReadBillLineItemAttachments;
GO

CREATE PROCEDURE ReadBillLineItemAttachments
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BillLineItemId],
        [AttachmentId]
    FROM dbo.[BillLineItemAttachment]
    ORDER BY [BillLineItemId] ASC, [AttachmentId] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadBillLineItemAttachments;
GO


DROP PROCEDURE IF EXISTS ReadBillLineItemAttachmentById;
GO

CREATE PROCEDURE ReadBillLineItemAttachmentById
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
        [BillLineItemId],
        [AttachmentId]
    FROM dbo.[BillLineItemAttachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadBillLineItemAttachmentById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadBillLineItemAttachmentByPublicId;
GO

CREATE PROCEDURE ReadBillLineItemAttachmentByPublicId
(
    @PublicId UNIQUEIDENTIFIER
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
        [BillLineItemId],
        [AttachmentId]
    FROM dbo.[BillLineItemAttachment]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadBillLineItemAttachmentByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadBillLineItemAttachmentByBillLineItemId;
GO

CREATE PROCEDURE ReadBillLineItemAttachmentByBillLineItemId
(
    @BillLineItemId BIGINT
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
        [BillLineItemId],
        [AttachmentId]
    FROM dbo.[BillLineItemAttachment]
    WHERE [BillLineItemId] = @BillLineItemId;

    COMMIT TRANSACTION;
END;

EXEC ReadBillLineItemAttachmentByBillLineItemId
    @BillLineItemId = 1;
GO


DROP PROCEDURE IF EXISTS DeleteBillLineItemAttachmentById;
GO

CREATE PROCEDURE DeleteBillLineItemAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[BillLineItemAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillLineItemId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteBillLineItemAttachmentById
    @Id = 2;
GO


SELECT * FROM dbo.BillLineItemAttachment;