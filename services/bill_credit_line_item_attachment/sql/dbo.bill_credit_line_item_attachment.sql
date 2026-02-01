GO

IF OBJECT_ID('dbo.BillCreditLineItemAttachment', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillCreditLineItemAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BillCreditLineItemId] BIGINT NULL,
    [AttachmentId] BIGINT NULL
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateBillCreditLineItemAttachment
(
    @BillCreditLineItemId BIGINT,
    @AttachmentId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillCreditLineItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [BillCreditLineItemId], [AttachmentId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillCreditLineItemId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @BillCreditLineItemId, @AttachmentId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadBillCreditLineItemAttachments
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BillCreditLineItemId],
        [AttachmentId]
    FROM dbo.[BillCreditLineItemAttachment]
    ORDER BY [BillCreditLineItemId] ASC, [AttachmentId] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadBillCreditLineItemAttachmentById
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
        [BillCreditLineItemId],
        [AttachmentId]
    FROM dbo.[BillCreditLineItemAttachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadBillCreditLineItemAttachmentByPublicId
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
        [BillCreditLineItemId],
        [AttachmentId]
    FROM dbo.[BillCreditLineItemAttachment]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadBillCreditLineItemAttachmentByBillCreditLineItemId
(
    @BillCreditLineItemId BIGINT
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
        [BillCreditLineItemId],
        [AttachmentId]
    FROM dbo.[BillCreditLineItemAttachment]
    WHERE [BillCreditLineItemId] = @BillCreditLineItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteBillCreditLineItemAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[BillCreditLineItemAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillCreditLineItemId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
