GO

IF OBJECT_ID('dbo.ExpenseLineItemAttachment', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ExpenseLineItemAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ExpenseLineItemId] BIGINT NOT NULL,
    [AttachmentId] BIGINT NOT NULL,
    CONSTRAINT [FK_ExpenseLineItemAttachment_ExpenseLineItem] FOREIGN KEY ([ExpenseLineItemId]) REFERENCES [dbo].[ExpenseLineItem]([Id]),
    CONSTRAINT [FK_ExpenseLineItemAttachment_Attachment] FOREIGN KEY ([AttachmentId]) REFERENCES [dbo].[Attachment]([Id]),
    CONSTRAINT [UQ_ExpenseLineItemAttachment_ExpenseLineItemId] UNIQUE ([ExpenseLineItemId])
);
END
GO

-- Migration: add constraints if table already exists without them
IF OBJECT_ID('dbo.ExpenseLineItemAttachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ExpenseLineItemAttachment_ExpenseLineItem' AND parent_object_id = OBJECT_ID('dbo.ExpenseLineItemAttachment'))
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItemAttachment]
    ADD CONSTRAINT [FK_ExpenseLineItemAttachment_ExpenseLineItem] FOREIGN KEY ([ExpenseLineItemId]) REFERENCES [dbo].[ExpenseLineItem]([Id]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItemAttachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ExpenseLineItemAttachment_Attachment' AND parent_object_id = OBJECT_ID('dbo.ExpenseLineItemAttachment'))
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItemAttachment]
    ADD CONSTRAINT [FK_ExpenseLineItemAttachment_Attachment] FOREIGN KEY ([AttachmentId]) REFERENCES [dbo].[Attachment]([Id]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItemAttachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_ExpenseLineItemAttachment_ExpenseLineItemId' AND parent_object_id = OBJECT_ID('dbo.ExpenseLineItemAttachment'))
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItemAttachment]
    ADD CONSTRAINT [UQ_ExpenseLineItemAttachment_ExpenseLineItemId] UNIQUE ([ExpenseLineItemId]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItemAttachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItemAttachment_ExpenseLineItemId' AND object_id = OBJECT_ID('dbo.ExpenseLineItemAttachment'))
BEGIN
    CREATE INDEX IX_ExpenseLineItemAttachment_ExpenseLineItemId ON [dbo].[ExpenseLineItemAttachment] ([ExpenseLineItemId]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItemAttachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItemAttachment_AttachmentId' AND object_id = OBJECT_ID('dbo.ExpenseLineItemAttachment'))
BEGIN
    CREATE INDEX IX_ExpenseLineItemAttachment_AttachmentId ON [dbo].[ExpenseLineItemAttachment] ([AttachmentId]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateExpenseLineItemAttachment
(
    @ExpenseLineItemId BIGINT,
    @AttachmentId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ExpenseLineItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [ExpenseLineItemId], [AttachmentId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ExpenseLineItemId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @ExpenseLineItemId, @AttachmentId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemAttachments
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [ExpenseLineItemId],
        [AttachmentId]
    FROM dbo.[ExpenseLineItemAttachment]
    ORDER BY [ExpenseLineItemId] ASC, [AttachmentId] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemAttachmentById
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
        [ExpenseLineItemId],
        [AttachmentId]
    FROM dbo.[ExpenseLineItemAttachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemAttachmentByPublicId
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
        [ExpenseLineItemId],
        [AttachmentId]
    FROM dbo.[ExpenseLineItemAttachment]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemAttachmentByExpenseLineItemId
(
    @ExpenseLineItemId BIGINT
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
        [ExpenseLineItemId],
        [AttachmentId]
    FROM dbo.[ExpenseLineItemAttachment]
    WHERE [ExpenseLineItemId] = @ExpenseLineItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemAttachmentsByExpenseLineItemPublicIds
(
    @PublicIds NVARCHAR(MAX)  -- comma-separated list of PublicId GUIDs
)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Parse comma-separated GUIDs into a temp table
    CREATE TABLE #Ids (PublicId UNIQUEIDENTIFIER);
    INSERT INTO #Ids (PublicId)
    SELECT TRY_CAST(LTRIM(RTRIM(value)) AS UNIQUEIDENTIFIER)
    FROM STRING_SPLIT(@PublicIds, ',')
    WHERE TRY_CAST(LTRIM(RTRIM(value)) AS UNIQUEIDENTIFIER) IS NOT NULL;

    SELECT
        elia.[Id],
        elia.[PublicId],
        elia.[RowVersion],
        CONVERT(VARCHAR(19), elia.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), elia.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        elia.[ExpenseLineItemId],
        elia.[AttachmentId],
        eli.[PublicId] AS [ExpenseLineItemPublicId]
    FROM dbo.[ExpenseLineItemAttachment] elia
    JOIN dbo.[ExpenseLineItem] eli ON eli.[Id] = elia.[ExpenseLineItemId]
    WHERE eli.[PublicId] IN (SELECT PublicId FROM #Ids);

    DROP TABLE #Ids;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteExpenseLineItemAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ExpenseLineItemAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ExpenseLineItemId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
