IF OBJECT_ID('dbo.InvoiceLineItemAttachment', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[InvoiceLineItemAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [InvoiceLineItemId] BIGINT NULL,
    [AttachmentId] BIGINT NULL
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql
-- (which added the column, backfilled 17, applied DEFAULT (17), then tightened to NOT NULL).
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.InvoiceLineItemAttachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.InvoiceLineItemAttachment') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_InvoiceLineItemAttachment_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.InvoiceLineItemAttachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItemAttachment_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD CONSTRAINT [FK_InvoiceLineItemAttachment_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

-- CANONICAL HOME for CreateInvoiceLineItemAttachment (standing single-source-of-truth rule).
-- @CreatedByUserId threading came from scripts/migrations/gap2_adjacent_threading.sql; this
-- file carried a stale 2-param duplicate until it was reconciled here. That duplicate was a
-- live regression hazard: base files use CREATE OR ALTER and get re-run routinely, so applying
-- this file would have reverted the sproc and broken every
-- InvoiceLineItemAttachmentRepository.create call, which always sends CreatedByUserId. Reached
-- via POST /api/v1/create/invoice-line-item-attachment — no parent rollback here, unlike the
-- Bill and Expense siblings, so the failure is a broken endpoint rather than a lost parent row.
-- Do NOT re-add a competing definition in scripts/migrations/.
CREATE OR ALTER PROCEDURE CreateInvoiceLineItemAttachment
(
    @InvoiceLineItemId BIGINT,
    @AttachmentId BIGINT,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[InvoiceLineItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [InvoiceLineItemId], [AttachmentId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceLineItemId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @InvoiceLineItemId, @AttachmentId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadInvoiceLineItemAttachments
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceLineItemId],
        [AttachmentId]
    FROM dbo.[InvoiceLineItemAttachment]
    ORDER BY [InvoiceLineItemId] ASC, [AttachmentId] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadInvoiceLineItemAttachmentById
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
        [InvoiceLineItemId],
        [AttachmentId]
    FROM dbo.[InvoiceLineItemAttachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadInvoiceLineItemAttachmentByPublicId
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
        [InvoiceLineItemId],
        [AttachmentId]
    FROM dbo.[InvoiceLineItemAttachment]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadInvoiceLineItemAttachmentsByInvoiceLineItemId
(
    @InvoiceLineItemId BIGINT
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
        [InvoiceLineItemId],
        [AttachmentId]
    FROM dbo.[InvoiceLineItemAttachment]
    WHERE [InvoiceLineItemId] = @InvoiceLineItemId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeleteInvoiceLineItemAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[InvoiceLineItemAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[InvoiceLineItemId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceLineItemAttachmentsByInvoiceLineItemPublicIds
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
        ilia.[Id],
        ilia.[PublicId],
        ilia.[RowVersion],
        CONVERT(VARCHAR(19), ilia.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), ilia.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        ilia.[InvoiceLineItemId],
        ilia.[AttachmentId],
        ili.[PublicId] AS [InvoiceLineItemPublicId]
    FROM dbo.[InvoiceLineItemAttachment] ilia
    JOIN dbo.[InvoiceLineItem] ili ON ili.[Id] = ilia.[InvoiceLineItemId]
    WHERE ili.[PublicId] IN (SELECT PublicId FROM #Ids);

    DROP TABLE #Ids;

    COMMIT TRANSACTION;
END;
GO


-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItemAttachment_InvoiceLineItem')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD CONSTRAINT [FK_InvoiceLineItemAttachment_InvoiceLineItem] FOREIGN KEY ([InvoiceLineItemId]) REFERENCES [dbo].[InvoiceLineItem]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItemAttachment_Attachment')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD CONSTRAINT [FK_InvoiceLineItemAttachment_Attachment] FOREIGN KEY ([AttachmentId]) REFERENCES [dbo].[Attachment]([Id]);
END
GO
