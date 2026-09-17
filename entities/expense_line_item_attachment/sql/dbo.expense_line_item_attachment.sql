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

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql
-- (which added the column, backfilled 17, applied DEFAULT (17), then tightened to NOT NULL).
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.ExpenseLineItemAttachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.ExpenseLineItemAttachment') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItemAttachment] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_ExpenseLineItemAttachment_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.ExpenseLineItemAttachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ExpenseLineItemAttachment_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItemAttachment] ADD CONSTRAINT [FK_ExpenseLineItemAttachment_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
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

-- CANONICAL HOME for CreateExpenseLineItemAttachment (standing single-source-of-truth rule).
-- @CreatedByUserId threading came from scripts/migrations/gap2_adjacent_threading.sql; this
-- file carried a stale 2-param duplicate until it was reconciled here. That duplicate was a
-- live regression hazard: base files use CREATE OR ALTER and get re-run routinely, so applying
-- this file would have reverted the sproc and broken every
-- ExpenseLineItemAttachmentRepository.create call (which always sends CreatedByUserId) — and
-- with it every Expense create carrying a receipt, since ExpenseService.create rolls the
-- expense back when the attachment link fails. Same cleanup dbo.bill.sql did for CreateBill on
-- 2026-07-12 and dbo.bill_line_item_attachment.sql did for its own sproc. Do NOT re-add a
-- competing definition in scripts/migrations/.
CREATE OR ALTER PROCEDURE CreateExpenseLineItemAttachment
(
    @ExpenseLineItemId BIGINT,
    @AttachmentId BIGINT,
    @CreatedByUserId BIGINT = NULL,
    @AllowTerminalParent BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @ParentExpenseId BIGINT;

    -- LOCK UNCONDITIONALLY, REFUSE CONDITIONALLY (U-446c, ported U-468).
    BEGIN
        -- Take the ATTACHMENT lock first, matching the Bill sibling's order.
        DECLARE @LockedAttachment BIT;
        SELECT @LockedAttachment = 1 FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @AttachmentId;

        SELECT @ParentExpenseId = e.[Id]
        FROM dbo.[ExpenseLineItem] li
        INNER JOIN dbo.[Expense] e WITH (UPDLOCK, HOLDLOCK) ON e.[Id] = li.[ExpenseId]
        WHERE li.[Id] = @ExpenseLineItemId;

        IF @AllowTerminalParent = 0 AND EXISTS (
            SELECT 1 FROM dbo.[Expense] WHERE [Id] = @ParentExpenseId AND [Status] = 'completed'
        )
        BEGIN
            COMMIT TRANSACTION;
            RAISERROR('STATUS_LOCKED: attachments cannot be added to a completed Expense.', 16, 1);
            RETURN;
        END
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ExpenseLineItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [ExpenseLineItemId], [AttachmentId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ExpenseLineItemId],
        INSERTED.[AttachmentId]
    SELECT @Now, @Now, @ExpenseLineItemId, @AttachmentId, COALESCE(@CreatedByUserId, 17)
    WHERE @AllowTerminalParent = 1
       OR EXISTS (
            SELECT 1 FROM dbo.[ExpenseLineItem] li
            WHERE li.[Id] = @ExpenseLineItemId AND li.[ExpenseId] = @ParentExpenseId
          );

    COMMIT TRANSACTION;
END;
GO


GO

-- Scoped by UserProject membership for non-admin actors, via the parent
-- ExpenseLineItem's Expense — the same gap, and the same fix, as
-- ReadExpenseLineItems. Fails closed: an actor of (NULL, NULL) matches no rows.
CREATE OR ALTER PROCEDURE ReadExpenseLineItemAttachments
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        elia.[Id],
        elia.[PublicId],
        elia.[RowVersion],
        CONVERT(VARCHAR(19), elia.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), elia.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        elia.[ExpenseLineItemId],
        elia.[AttachmentId]
    FROM dbo.[ExpenseLineItemAttachment] elia
    INNER JOIN dbo.[ExpenseLineItem] eli ON eli.[Id] = elia.[ExpenseLineItemId]
    WHERE dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, eli.[ExpenseId]) = 1
    ORDER BY elia.[ExpenseLineItemId] ASC, elia.[AttachmentId] ASC;

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
    SET NOCOUNT ON;
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
    @Id BIGINT,
    @AllowTerminalParent BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @ParentExpenseId BIGINT;

    -- LOCK UNCONDITIONALLY, REFUSE CONDITIONALLY (U-446c, ported U-468).
    BEGIN
        SELECT @ParentExpenseId = e.[Id]
        FROM dbo.[ExpenseLineItemAttachment] elia
        INNER JOIN dbo.[ExpenseLineItem] li ON li.[Id] = elia.[ExpenseLineItemId]
        INNER JOIN dbo.[Expense] e WITH (UPDLOCK, HOLDLOCK) ON e.[Id] = li.[ExpenseId]
        WHERE elia.[Id] = @Id;

        IF @AllowTerminalParent = 0 AND EXISTS (
            SELECT 1 FROM dbo.[Expense] WHERE [Id] = @ParentExpenseId AND [Status] = 'completed'
        )
        BEGIN
            COMMIT TRANSACTION;
            RAISERROR('STATUS_LOCKED: the attachments of a completed Expense cannot be deleted.', 16, 1);
            RETURN;
        END
    END

    DELETE FROM dbo.[ExpenseLineItemAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ExpenseLineItemId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id
      AND (@AllowTerminalParent = 1
           OR EXISTS (
                SELECT 1 FROM dbo.[ExpenseLineItem] li
                WHERE li.[Id] = dbo.[ExpenseLineItemAttachment].[ExpenseLineItemId]
                  AND li.[ExpenseId] = @ParentExpenseId
              ));

    COMMIT TRANSACTION;
END;
GO


-- U-468. "Is this Attachment evidence for a completed Expense?"
--
-- The terminal lock guards the LINK row (ExpenseLineItemAttachment), but the
-- Attachment it points at was still freely mutable through the generic
-- `/update/attachment/{id}` and `/delete/attachment/{id}` routes: a caller
-- holding ATTACHMENTS.can_update could repoint `BlobUrl`, rename the file, or
-- archive it — the Bill-only COUNT and the Bill-only in-transaction walk both
-- saw zero completed Bills and let the write land.
--
-- One scalar answers it. COUNT(DISTINCT) because an Attachment may be linked
-- to several line items, and any single completed parent is enough to freeze it.
CREATE OR ALTER PROCEDURE CountCompletedExpensesByAttachmentId
(
    @AttachmentId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT COUNT(DISTINCT e.[Id]) AS [Count]
    FROM dbo.[ExpenseLineItemAttachment] elia
    INNER JOIN dbo.[ExpenseLineItem] li ON li.[Id] = elia.[ExpenseLineItemId]
    INNER JOIN dbo.[Expense] e ON e.[Id] = li.[ExpenseId]
    WHERE elia.[AttachmentId] = @AttachmentId
      AND e.[Status] = 'completed';
END;
GO
