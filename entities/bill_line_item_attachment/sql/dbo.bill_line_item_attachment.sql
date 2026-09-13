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

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.BillLineItemAttachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.BillLineItemAttachment') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_BillLineItemAttachment_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.BillLineItemAttachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BillLineItemAttachment_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD CONSTRAINT [FK_BillLineItemAttachment_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

-- CANONICAL HOME for CreateBillLineItemAttachment (standing single-source-of-truth rule).
-- @CreatedByUserId threading came from scripts/migrations/gap2_adjacent_threading.sql; this
-- file carried a stale 2-param duplicate until it was reconciled here. That duplicate was a
-- live regression hazard: base files use CREATE OR ALTER and get re-run routinely, so applying
-- this file would have reverted the sproc and broken every
-- BillLineItemAttachmentRepository.create call (which always sends CreatedByUserId) — and with
-- it every Bill create carrying a PDF, since BillService.create rolls the bill back when the
-- attachment link fails. Same cleanup dbo.bill.sql did for CreateBill on 2026-07-12. Do NOT
-- re-add a competing definition in scripts/migrations/.
-- U-446b: the in-transaction half of the terminal lock, same shape and same
-- ⚠ permissive-default rationale as entities/bill_line_item/sql/dbo.bill_line_item.sql.
-- Attaching evidence to a completed Bill is as much an edit as changing a line.
CREATE OR ALTER PROCEDURE CreateBillLineItemAttachment
(
    @BillLineItemId BIGINT,
    @AttachmentId BIGINT,
    @CreatedByUserId BIGINT = NULL,
    @AllowTerminalParent BIT = 1
)
AS
BEGIN
    -- Required: the guard runs assignment SELECTs before the DML, and with
    -- NOCOUNT off each emits a row-count token that pyodbc reads as the first
    -- "result" (CLAUDE.md, 2026-06-11).
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @ParentBillId BIGINT;

    IF @AllowTerminalParent = 0
    BEGIN
        -- Take the ATTACHMENT lock first, in the same order the attachment
        -- sprocs do (Codex round 4, P1). Without it, linking this file to a
        -- draft Bill could slip in while an attachment update was mid-flight,
        -- and that Bill could then complete — leaving the update to land on
        -- what is now a completed Bill's evidence.
        DECLARE @LockedAttachment BIT;
        SELECT @LockedAttachment = 1 FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @AttachmentId;

        -- Capture AND lock the parent Bill in one statement, so the id we bind
        -- the INSERT to below is the id we actually checked.
        SELECT @ParentBillId = b.[Id]
        FROM dbo.[BillLineItem] li
        INNER JOIN dbo.[Bill] b WITH (UPDLOCK, HOLDLOCK) ON b.[Id] = li.[BillId]
        WHERE li.[Id] = @BillLineItemId;

        IF EXISTS (
            SELECT 1 FROM dbo.[Bill] WHERE [Id] = @ParentBillId AND [Status] = 'completed'
        )
        BEGIN
            COMMIT TRANSACTION;
            RAISERROR('STATUS_LOCKED: attachments cannot be added to a completed Bill.', 16, 1);
            RETURN;
        END
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- INSERT ... SELECT rather than VALUES so the write carries a predicate
    -- (Codex round 3, P1): the line could be MOVED to another Bill between the
    -- check above and this statement, and that Bill could be completed. Binding
    -- to the parent we locked makes attaching to it impossible; a losing race
    -- inserts zero rows and the repo reports the failure.
    INSERT INTO dbo.[BillLineItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [BillLineItemId], [AttachmentId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillLineItemId],
        INSERTED.[AttachmentId]
    SELECT @Now, @Now, @BillLineItemId, @AttachmentId, COALESCE(@CreatedByUserId, 17)
    WHERE @AllowTerminalParent = 1
       OR EXISTS (
            SELECT 1 FROM dbo.[BillLineItem] li
            WHERE li.[Id] = @BillLineItemId AND li.[BillId] = @ParentBillId
          );

    COMMIT TRANSACTION;
END;



GO

-- Scoped by UserProject membership for non-admin actors, via the parent BillLineItem's
-- Bill — the same gap, and the same fix, as ReadBillLineItems. Fails closed: an actor of
-- (NULL, NULL) matches no rows. The INNER JOIN also drops orphan link rows whose
-- BillLineItem is gone, which is correct for a list surface.
CREATE OR ALTER PROCEDURE ReadBillLineItemAttachments
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        blia.[Id],
        blia.[PublicId],
        blia.[RowVersion],
        CONVERT(VARCHAR(19), blia.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), blia.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        blia.[BillLineItemId],
        blia.[AttachmentId]
    FROM dbo.[BillLineItemAttachment] blia
    INNER JOIN dbo.[BillLineItem] bli ON bli.[Id] = blia.[BillLineItemId]
    WHERE dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, bli.[BillId]) = 1
    ORDER BY blia.[BillLineItemId] ASC, blia.[AttachmentId] ASC;

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
    @Id BIGINT,
    @AllowTerminalParent BIT = 1
)
AS
BEGIN
    -- Required: the guard runs assignment SELECTs before the DML, and with
    -- NOCOUNT off each emits a row-count token that pyodbc reads as the first
    -- "result" (CLAUDE.md, 2026-06-11).
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @ParentBillId BIGINT;

    IF @AllowTerminalParent = 0
    BEGIN
        SELECT @ParentBillId = b.[Id]
        FROM dbo.[BillLineItemAttachment] blia
        INNER JOIN dbo.[BillLineItem] li ON li.[Id] = blia.[BillLineItemId]
        INNER JOIN dbo.[Bill] b WITH (UPDLOCK, HOLDLOCK) ON b.[Id] = li.[BillId]
        WHERE blia.[Id] = @Id;

        IF EXISTS (
            SELECT 1 FROM dbo.[Bill] WHERE [Id] = @ParentBillId AND [Status] = 'completed'
        )
        BEGIN
            COMMIT TRANSACTION;
            RAISERROR('STATUS_LOCKED: the attachments of a completed Bill cannot be deleted.', 16, 1);
            RETURN;
        END
    END

    -- Bound to the parent we locked, for the reparent race described in
    -- dbo.bill_line_item.sql's DELETE.
    DELETE FROM dbo.[BillLineItemAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillLineItemId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id
      AND (@AllowTerminalParent = 1
           OR EXISTS (
                SELECT 1 FROM dbo.[BillLineItem] li
                WHERE li.[Id] = dbo.[BillLineItemAttachment].[BillLineItemId]
                  AND li.[BillId] = @ParentBillId
              ));

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


-- U-446b. "Is this Attachment evidence for a completed Bill?"
--
-- The terminal lock guards the LINK row (BillLineItemAttachment), but the
-- Attachment it points at was still freely mutable through the generic
-- `/update/attachment/{id}` and `/delete/attachment/{id}` routes: a caller
-- holding ATTACHMENTS.can_update could repoint `BlobUrl`, rename the file, or
-- archive it, and `can_delete` could destroy the blob outright — the FK is
-- NO ACTION, so the DB delete then fails and the evidence is gone anyway.
--
-- One scalar answers it. COUNT(DISTINCT) because an Attachment may be linked
-- to several line items (BLIA multi-split linking), and any single completed
-- parent is enough to freeze it.
CREATE OR ALTER PROCEDURE CountCompletedBillsByAttachmentId
(
    @AttachmentId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT COUNT(DISTINCT b.[Id]) AS [Count]
    FROM dbo.[BillLineItemAttachment] blia
    INNER JOIN dbo.[BillLineItem] li ON li.[Id] = blia.[BillLineItemId]
    INNER JOIN dbo.[Bill] b ON b.[Id] = li.[BillId]
    WHERE blia.[AttachmentId] = @AttachmentId
      AND b.[Status] = 'completed';
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
