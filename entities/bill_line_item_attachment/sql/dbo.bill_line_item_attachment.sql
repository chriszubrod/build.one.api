GO

IF OBJECT_ID('dbo.BillLineItemAttachment', 'U') IS NULL
BEGIN
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
END
GO


GO

CREATE OR ALTER PROCEDURE CreateBillLineItemAttachment
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



GO

CREATE OR ALTER PROCEDURE ReadBillLineItemAttachments
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



GO

CREATE OR ALTER PROCEDURE ReadBillLineItemAttachmentById
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



GO

CREATE OR ALTER PROCEDURE ReadBillLineItemAttachmentByPublicId
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



GO

CREATE OR ALTER PROCEDURE ReadBillLineItemAttachmentByBillLineItemId
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



GO

CREATE OR ALTER PROCEDURE DeleteBillLineItemAttachmentById
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
GO


CREATE OR ALTER PROCEDURE ReadBillLineItemAttachmentsByBillLineItemPublicIds
(
    @PublicIds NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    CREATE TABLE #Ids (PublicId UNIQUEIDENTIFIER);
    INSERT INTO #Ids (PublicId)
    SELECT TRY_CAST(LTRIM(RTRIM(value)) AS UNIQUEIDENTIFIER)
    FROM STRING_SPLIT(@PublicIds, ',')
    WHERE TRY_CAST(LTRIM(RTRIM(value)) AS UNIQUEIDENTIFIER) IS NOT NULL;

    SELECT
        blia.[Id],
        blia.[PublicId],
        blia.[RowVersion],
        CONVERT(VARCHAR(19), blia.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), blia.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        blia.[BillLineItemId],
        blia.[AttachmentId],
        bli.[PublicId] AS [BillLineItemPublicId]
    FROM dbo.[BillLineItemAttachment] blia
    JOIN dbo.[BillLineItem] bli ON bli.[Id] = blia.[BillLineItemId]
    WHERE bli.[PublicId] IN (SELECT PublicId FROM #Ids);

    DROP TABLE #Ids;

    COMMIT TRANSACTION;
END;
GO


-- Count BillLineItemAttachment records for a given AttachmentId
CREATE OR ALTER PROCEDURE CountBillLineItemAttachmentsByAttachmentId
(
    @AttachmentId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT COUNT(*) AS [Count]
    FROM dbo.[BillLineItemAttachment]
    WHERE [AttachmentId] = @AttachmentId;
END;
GO


-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BillLineItemAttachment_BillLineItem')
BEGIN
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD CONSTRAINT [FK_BillLineItemAttachment_BillLineItem] FOREIGN KEY ([BillLineItemId]) REFERENCES [dbo].[BillLineItem]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BillLineItemAttachment_Attachment')
BEGIN
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD CONSTRAINT [FK_BillLineItemAttachment_Attachment] FOREIGN KEY ([AttachmentId]) REFERENCES [dbo].[Attachment]([Id]);
END
GO

-- 1-to-1: each BillLineItem has at most one attachment
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_BillLineItemAttachment_BillLineItemId' AND parent_object_id = OBJECT_ID('dbo.BillLineItemAttachment'))
BEGIN
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD CONSTRAINT [UQ_BillLineItemAttachment_BillLineItemId] UNIQUE ([BillLineItemId]);
END
GO
