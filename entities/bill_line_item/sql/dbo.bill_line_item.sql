IF OBJECT_ID('dbo.BillLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BillId] BIGINT NOT NULL,
    [SubCostCodeId] BIGINT NULL,
    [ProjectId] BIGINT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Quantity] DECIMAL(18,4) NULL,
    [Rate] DECIMAL(18,4) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [IsBillable] BIT NULL,
    [IsBilled] BIT NULL,
    [Markup] DECIMAL(18,4) NULL,
    [Price] DECIMAL(18,2) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_BillLineItem_Bill] FOREIGN KEY ([BillId]) REFERENCES [dbo].[Bill]([Id]),
    CONSTRAINT [FK_BillLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]),
    CONSTRAINT [FK_BillLineItem_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id])
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.BillLineItem') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[BillLineItem] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_BillLineItem_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BillLineItem_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[BillLineItem] ADD CONSTRAINT [FK_BillLineItem_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_BillId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_BillId ON [dbo].[BillLineItem] ([BillId]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_SubCostCodeId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_SubCostCodeId ON [dbo].[BillLineItem] ([SubCostCodeId]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_ProjectId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_ProjectId ON [dbo].[BillLineItem] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_PublicId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_PublicId ON [dbo].[BillLineItem] ([PublicId]);
END
GO

-- U-238b added QboId/RealmId + UQ_BillLineItem_BillId_QboId live via
-- scripts/migrations/238b_qbo_identity_lines.sql but never ported the DDL into
-- this base file (the same from-scratch-build gap U-277/U-290 found and fixed
-- for company/address/vendor) — SetBillLineItemQboIdentity below has silently
-- depended on columns this file never declared. Closed here (U-293), verbatim
-- against the live migration so a from-scratch build matches prod exactly.
IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillLineItem') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[BillLineItem] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillLineItem') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[BillLineItem] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_BillLineItem_BillId_QboId' AND object_id = OBJECT_ID('dbo.BillLineItem')
)
BEGIN
    CREATE UNIQUE INDEX UQ_BillLineItem_BillId_QboId ON [dbo].[BillLineItem] ([BillId], [QboId]) WHERE [QboId] IS NOT NULL;
END
GO



-- ===========================================================================
-- U-446b — the terminal lock's IN-TRANSACTION half (Codex P1 #5, the TOCTOU).
--
-- The Python guard in shared/lifecycle/terminal_lock.py reads the parent Bill
-- in one transaction and writes the child row in another. Under RCSI every
-- statement takes its own snapshot, so a line edit that races a completion
-- passes the guard against a `draft` snapshot and then commits AFTER the bill
-- finalizes. Only a predicate inside the writing transaction closes that.
--
-- Shape, in all three mutation sprocs below:
--     SELECT ... FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] IN (...)
-- UPDLOCK is the point: a plain SELECT would read the same stale RCSI snapshot
-- the Python guard did. The U lock conflicts with FinalizeBillById's UPDATE, so
-- the two serialize — whichever gets there second sees the other's outcome.
--
-- LOCK ORDER. Only ONE table is locked by these guards — dbo.Bill. The reads of
-- BillLineItem / BillLineItemAttachment that resolve the parent take no locks
-- (RCSI snapshot reads), so the order that matters is: every guarded statement
-- takes its U lock on Bill BEFORE the DML takes its X lock on the child row,
-- matching what completion itself does (Step 1 header, Step 2 lines). The bill
-- CASCADE delete is not a counter-example: it runs each child delete in its own
-- transaction, so it never holds a child lock while reaching for Bill.
--
-- Where two Bills are involved (a line being MOVED) they are locked LOW id
-- first, then HIGH — see UpdateBillLineItemById. A single `IN (@a, @b)` seek
-- looked equivalent but guarantees no acquisition order, which would let two
-- opposite moves deadlock.
--
-- Those snapshot reads DO mean the parent resolved before the lock can be
-- stale, so each write is additionally bound to the parent it locked — see the
-- `[BillId] = @ParentBillId` predicate on the DELETE.
--
-- On refusal: COMMIT the untouched transaction, THEN RAISERROR. Never ROLLBACK
-- inside a sproc — pyodbc runs autocommit-off, so an in-proc rollback zeroes the
-- implicit outer transaction and SQL Server raises error 266 (CLAUDE.md, the
-- 2026-06-11 result-set discipline). The `STATUS_LOCKED:` prefix is what
-- BillLineItemRepository turns back into a StatusLockedError.
--
-- @AllowTerminalParent DEFAULTS TO 0 — FAIL-CLOSED (U-446c).
-- A call site that forgets the param now gets the guard, instead of silently
-- losing this layer with nothing to notice.
--
-- U-446b shipped it as `= 1` deliberately: that was the only default that made
-- the SQL safe to apply in EITHER order relative to its own deploy, because a
-- `= 0` default would have refused completion's own Step-2 line finalize while
-- the previous image was still serving — turning every completion with draft
-- lines into a 207 until the deploy landed, unhealable by the reclaim watchdog.
-- That window is closed: U-446b is live, every caller passes the param
-- explicitly, and no pre-U-446b image can still be serving.
--
-- The bridge was not hypothetical while it stood — the Contract-Labor rebuild
-- reached `BillLineItemRepository.delete_by_id` directly and silently inherited
-- the permissive default until U-446b made it explicit. The repo-layer pin in
-- tests/test_u446b_terminal_lock.py stays regardless: passing it explicitly is
-- still the contract, the default is just no longer a trapdoor.
-- The Python guard remains the primary; this is defence in depth.
-- ===========================================================================

CREATE OR ALTER PROCEDURE CreateBillLineItem
(
    @BillId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @IsBilled BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL,
    @AllowTerminalParent BIT = 0
)
AS
BEGIN
    -- SET NOCOUNT ON is REQUIRED now, not cosmetic (CLAUDE.md, 2026-06-11).
    -- The guard below runs assignment SELECTs before the DML; with NOCOUNT off
    -- each emits a row-count token that pyodbc surfaces as the FIRST "result",
    -- and cursor.fetchone() then raises "No results. Previous SQL was not a
    -- query" instead of returning the OUTPUT row. Single-statement
    -- INSERT/UPDATE ... OUTPUT sprocs survive without it by accident; this one
    -- is no longer single-statement.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    -- LOCK UNCONDITIONALLY, REFUSE CONDITIONALLY (U-446c, Codex).
    -- @AllowTerminalParent says whether this caller may WRITE to a completed
    -- parent. It must not decide whether to take the lock: an exempt writer
    -- that skips it is invisible to every other transaction, so a cascade
    -- holding this Bill no longer blocks it and the serialization the guards
    -- depend on silently disappears for exactly the callers that mutate most.
    DECLARE @LockedParents INT;
    SELECT @LockedParents = COUNT(*)
    FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK)
    WHERE [Id] = @BillId AND [Status] = 'completed';

    IF @AllowTerminalParent = 0 AND @LockedParents > 0
    BEGIN
        COMMIT TRANSACTION;
        RAISERROR('STATUS_LOCKED: line items cannot be added to a completed Bill.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillLineItem] ([CreatedDatetime], [ModifiedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Description], [Quantity], [Rate], [Amount], [IsBillable], [IsBilled], [Markup], [Price], [IsDraft], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillId],
        INSERTED.[SubCostCodeId],
        INSERTED.[ProjectId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[IsBilled],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @BillId, @SubCostCodeId, @ProjectId, @Description, @Quantity, @Rate, @Amount, @IsBillable, @IsBilled, @Markup, @Price, @IsDraft, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


-- Scoped by UserProject membership for non-admin actors, exactly as ReadBills is
-- (entities/bill/sql/dbo.bill.sql). This list path was the one unscoped read left on the
-- entity — read_by_id / read_by_public_id / read_by_bill_id / read_by_project_id all gate
-- in the service layer via assert_can_access_*, but read_all had no gate at either layer,
-- so GET /api/v1/get/bill_line_items returned every line item's amounts, descriptions and
-- projects to any caller holding BILLS can_read.
--
-- New params take = NULL defaults so an older caller (or an unapplied deploy) still binds.
-- Note the fail-closed asymmetry that default implies: UserCanAccessBill(NULL, NULL, ...)
-- returns 0 for every row, so a caller that omits them gets an EMPTY list, never the whole
-- table. That is the safe direction and it matches shared/access.py's 2026-05-12 rule that
-- a missing actor no longer bypasses.
CREATE OR ALTER PROCEDURE ReadBillLineItems
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        bli.[Id],
        bli.[PublicId],
        bli.[RowVersion],
        CONVERT(VARCHAR(19), bli.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), bli.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        bli.[BillId],
        bli.[SubCostCodeId],
        bli.[ProjectId],
        bli.[Description],
        bli.[Quantity],
        bli.[Rate],
        bli.[Amount],
        bli.[IsBillable],
        bli.[IsBilled],
        bli.[Markup],
        bli.[Price],
        bli.[IsDraft]
    FROM dbo.[BillLineItem] bli
    WHERE dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, bli.[BillId]) = 1
    ORDER BY bli.[CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadBillLineItemById
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
        [BillId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft],
        [QboId],
        [RealmId]
    FROM dbo.[BillLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE ReadBillLineItemByPublicId
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
        [BillId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft],
        [QboId],
        [RealmId]
    FROM dbo.[BillLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO





CREATE OR ALTER PROCEDURE ReadBillLineItemsByBillId
(
    @BillId BIGINT
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
        [BillId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft],
        [QboId],
        [RealmId]
    FROM dbo.[BillLineItem]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO





CREATE OR ALTER PROCEDURE UpdateBillLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @BillId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @IsBilled BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = NULL,
    @AllowTerminalParent BIT = 0
)
AS
BEGIN
    -- SET NOCOUNT ON is REQUIRED now, not cosmetic (CLAUDE.md, 2026-06-11).
    -- The guard below runs assignment SELECTs before the DML; with NOCOUNT off
    -- each emits a row-count token that pyodbc surfaces as the FIRST "result",
    -- and cursor.fetchone() then raises "No results. Previous SQL was not a
    -- query" instead of returning the OUTPUT row. Single-statement
    -- INSERT/UPDATE ... OUTPUT sprocs survive without it by accident; this one
    -- is no longer single-statement.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    -- LOCK UNCONDITIONALLY, REFUSE CONDITIONALLY (U-446c, Codex). This sproc is
    -- THE mover: a cascade holding a Bill relies on a reparent blocking here.
    -- When the lock was inside the exempt check, an exempt move took no lock at
    -- all and could reparent a line out from under a cascade mid-cleanup.
    BEGIN
        -- BOTH parents: the one the line is on now, and the one it is being
        -- moved to. Guarding only the current parent let a line be re-pointed
        -- ONTO a completed Bill (the same hole the Python layer had).
        DECLARE @CurrentBillId BIGINT;
        SELECT @CurrentBillId = [BillId] FROM dbo.[BillLineItem] WHERE [Id] = @Id;

        -- Acquired LOW id first, then HIGH, always. `IN (@a, @b)` looked
        -- equivalent but guarantees no acquisition order (Codex round 3, P2):
        -- two moves in opposite directions, A->B and B->A, could take the two U
        -- locks in opposing order and deadlock. Ascending id is a total order,
        -- so every writer queues the same way. When the two ids are equal (an
        -- ordinary edit, not a move) the second seek re-locks the row it
        -- already holds, which is free.
        DECLARE @LoBillId BIGINT = CASE WHEN @CurrentBillId IS NULL THEN @BillId
                                        WHEN @CurrentBillId <= @BillId THEN @CurrentBillId
                                        ELSE @BillId END;
        DECLARE @HiBillId BIGINT = CASE WHEN @CurrentBillId IS NULL THEN @BillId
                                        WHEN @CurrentBillId <= @BillId THEN @BillId
                                        ELSE @CurrentBillId END;

        DECLARE @LockedParents INT = 0;
        SELECT @LockedParents = @LockedParents + COUNT(*)
        FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @LoBillId AND [Status] = 'completed';

        SELECT @LockedParents = @LockedParents + COUNT(*)
        FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @HiBillId AND [Id] <> @LoBillId AND [Status] = 'completed';

        IF @AllowTerminalParent = 0 AND @LockedParents > 0
        BEGIN
            COMMIT TRANSACTION;
            RAISERROR('STATUS_LOCKED: the line items of a completed Bill cannot be changed.', 16, 1);
            RETURN;
        END
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[BillLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [BillId] = @BillId,
        [SubCostCodeId] = @SubCostCodeId,
        [ProjectId] = @ProjectId,
        [Description] = @Description,
        [Quantity] = @Quantity,
        [Rate] = @Rate,
        [Amount] = @Amount,
        [IsBillable] = @IsBillable,
        [IsBilled] = @IsBilled,
        [Markup] = @Markup,
        [Price] = @Price,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillId],
        INSERTED.[SubCostCodeId],
        INSERTED.[ProjectId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[IsBilled],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft],
        INSERTED.[QboId],
        INSERTED.[RealmId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO





-- ===========================================================================
-- U-446c — DELETING A LINE ITEM IS ONE TRANSACTION.
--
-- Supersedes DeleteBillLineItemById for the service path. The Python cascade
-- it replaces committed its dependent cleanup (invoice lines, ContractLabor FK
-- clear) in SEPARATE transactions BEFORE the guarded line delete — so a
-- completion landing in that gap produced `status_locked` AFTER those rows were
-- already gone. Holding the parent lock across the whole thing is the fix.
--
-- ALSO CLOSES A LATENT 547: the Python path never cleared
-- BillLineItemAttachment, whose FK to BillLineItem is NO ACTION (4,003 live
-- link rows). Deleting a line that still had its attachment link failed. The
-- bill-level cascade cleared it first, which is why this only bit the
-- standalone path. Attachment ROWS and blobs are left alone — only the link.
--
-- ContractLaborLineItem is SET_NULL and needs no step.
-- ===========================================================================
CREATE OR ALTER PROCEDURE DeleteBillLineItemCascadeById
(
    @Id BIGINT,
    @AllowTerminalParent BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @ParentBillId BIGINT = NULL;
    SELECT @ParentBillId = [BillId] FROM dbo.[BillLineItem] WHERE [Id] = @Id;

    -- No early RETURN when the line is missing: @ParentBillId stays NULL, every
    -- statement below matches nothing, and the final DELETE's OUTPUT yields an
    -- EMPTY result set. A bare RETURN produced NO result set, on which pyodbc's
    -- fetchone() raises "No results. Previous SQL was not a query".
    -- Lock the parent and HOLD it for the cascade, whether or not the caller is
    -- exempt: the lock is what serializes this against FinalizeBillById, and an
    -- exempt caller still must not interleave with a completion.
    DECLARE @ParentStatus NVARCHAR(20) = NULL;
    SELECT @ParentStatus = [Status]
    FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK)
    WHERE [Id] = @ParentBillId;

    IF @AllowTerminalParent = 0 AND @ParentStatus = 'completed'
    BEGIN
        COMMIT TRANSACTION;
        RAISERROR('STATUS_LOCKED: the line items of a completed Bill cannot be deleted.', 16, 1);
        RETURN;
    END

    -- RE-READ UNDER THE LOCK before destroying anything (Codex P0).
    -- @ParentBillId came from a snapshot read, so the line could have been
    -- MOVED between that read and the lock above. Only the final DELETE was
    -- bound to the locked parent, so the child cleanup below would have
    -- destroyed links, invoice rows and provenance belonging to a line that now
    -- lives on a DIFFERENT bill — possibly a completed one — while the delete
    -- itself matched nothing and the caller saw a bare "not found".
    --
    -- One re-check is enough: once this transaction holds the lock on
    -- @ParentBillId AND the line is confirmed to be on it, the line cannot move
    -- again, because any mover must take that same lock (UpdateBillLineItemById
    -- locks both the current and target parent).
    DECLARE @StillOnLockedParent BIT = 0;
    SELECT @StillOnLockedParent = 1
    FROM dbo.[BillLineItem]
    WHERE [Id] = @Id AND [BillId] = @ParentBillId;

    IF @StillOnLockedParent = 1
    BEGIN
        DELETE FROM dbo.[BillLineItemAttachment] WHERE [BillLineItemId] = @Id;

        -- InvoiceLineItem has TWO NO ACTION children of its own — this is what
        -- DeleteInvoiceLineItemsByBillLineItemId (the sproc behind the repo
        -- call this cascade replaced) does, and dropping it cost a 547 on the
        -- first rehearsal against real data. InvoiceLineItemAttachment is empty
        -- today; InvoiceLineItemSourceProvenance has ~30k rows.
        DELETE ila
        FROM dbo.[InvoiceLineItemAttachment] ila
        JOIN dbo.[InvoiceLineItem] ili ON ili.[Id] = ila.[InvoiceLineItemId]
        WHERE ili.[BillLineItemId] = @Id;

        DELETE prov
        FROM dbo.[InvoiceLineItemSourceProvenance] prov
        JOIN dbo.[InvoiceLineItem] ili ON ili.[Id] = prov.[InvoiceLineItemId]
        WHERE ili.[BillLineItemId] = @Id;

        DELETE FROM dbo.[InvoiceLineItem] WHERE [BillLineItemId] = @Id;

        UPDATE dbo.[ContractLabor]
        SET [BillLineItemId] = NULL,
            [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [BillLineItemId] = @Id;
    END

    -- Bound to the parent we locked: if the line was MOVED between the snapshot
    -- read above and here, zero rows match and the caller gets "not found"
    -- rather than a deletion off a bill this transaction never checked.
    DELETE FROM dbo.[BillLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillId],
        DELETED.[SubCostCodeId],
        DELETED.[ProjectId],
        DELETED.[Description],
        DELETED.[Quantity],
        DELETED.[Rate],
        DELETED.[Amount],
        DELETED.[IsBillable],
        DELETED.[IsBilled],
        DELETED.[Markup],
        DELETED.[Price],
        DELETED.[IsDraft]
    WHERE [Id] = @Id AND [BillId] = @ParentBillId;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE DeleteBillLineItemById
(
    @Id BIGINT,
    @AllowTerminalParent BIT = 0
)
AS
BEGIN
    -- SET NOCOUNT ON is REQUIRED now, not cosmetic (CLAUDE.md, 2026-06-11).
    -- The guard below runs assignment SELECTs before the DML; with NOCOUNT off
    -- each emits a row-count token that pyodbc surfaces as the FIRST "result",
    -- and cursor.fetchone() then raises "No results. Previous SQL was not a
    -- query" instead of returning the OUTPUT row. Single-statement
    -- INSERT/UPDATE ... OUTPUT sprocs survive without it by accident; this one
    -- is no longer single-statement.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    -- LOCK UNCONDITIONALLY, REFUSE CONDITIONALLY (U-446c, Codex).
    DECLARE @ParentBillId BIGINT;
    SELECT @ParentBillId = [BillId] FROM dbo.[BillLineItem] WHERE [Id] = @Id;

    DECLARE @LockedParents INT;
    SELECT @LockedParents = COUNT(*)
    FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK)
    WHERE [Id] = @ParentBillId AND [Status] = 'completed';

    IF @AllowTerminalParent = 0 AND @LockedParents > 0
    BEGIN
        COMMIT TRANSACTION;
        RAISERROR('STATUS_LOCKED: the line items of a completed Bill cannot be deleted.', 16, 1);
        RETURN;
    END

    -- The `[BillId] = @ParentBillId` half is what makes the guard above
    -- authoritative (Codex round 3, P1). @ParentBillId is resolved by a
    -- snapshot read, so between that read and here the line could have been
    -- MOVED to another Bill which then completed — the guard would have
    -- checked the old parent and this DELETE would have removed a line from a
    -- completed one. Binding the DELETE to the parent we actually locked makes
    -- that impossible: if the line moved, zero rows match and the caller gets
    -- "not found" instead of a wrong deletion.
    DELETE FROM dbo.[BillLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillId],
        DELETED.[SubCostCodeId],
        DELETED.[ProjectId],
        DELETED.[Description],
        DELETED.[Quantity],
        DELETED.[Rate],
        DELETED.[Amount],
        DELETED.[IsBillable],
        DELETED.[IsBilled],
        DELETED.[Markup],
        DELETED.[Price],
        DELETED.[IsDraft]
    WHERE [Id] = @Id
      AND (@AllowTerminalParent = 1 OR [BillId] = @ParentBillId);

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE ReadBillLineItemsByProjectId
(
    @ProjectId BIGINT
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
        [BillId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[BillLineItem]
    WHERE [ProjectId] = @ProjectId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- ReadBillLineItemBoxLinks — per-line-item (multi-project bills supported).
-- =====================================================================
-- Returns one row per dbo.BillLineItem on the bill, including line items
-- whose project has no Box mapping (LEFT JOIN — Box columns NULL in that
-- case). The router merges per-row results back into the line-item list
-- by BillLineItemId so the React table can show or hide icons per row.
--
-- Doc class for bills is fixed at 'invoices' (the project's `14 - Invoices`
-- Box folder). Workbook is one per project (UNIQUE on ProjectId).

CREATE OR ALTER PROCEDURE dbo.ReadBillLineItemBoxLinks (@BillId BIGINT)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        bli.[Id]                AS BillLineItemId,
        f.[BoxFolderId]         AS BoxInvoicesFolderId,
        pw.[BoxFileId]          AS BoxWorkbookFileId,
        pw.[WorksheetName]      AS BoxWorkbookWorksheetName
    FROM dbo.[BillLineItem] bli
    LEFT JOIN [box].[ProjectFolder] pf
        ON pf.[ProjectId] = bli.[ProjectId]
       AND pf.[DocClass]  = N'invoices'
    LEFT JOIN [box].[Folder] f
        ON f.[Id] = pf.[BoxFolderId]
    LEFT JOIN [box].[ProjectWorkbook] pw
        ON pw.[ProjectId] = bli.[ProjectId]
    WHERE bli.[BillId] = @BillId;
END;
GO
CREATE OR ALTER PROCEDURE SetBillLineItemQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    -- U-293-dw: QboId is only unique within its own parent transaction, so it
    -- is not a complete identity without RealmId. Defense-in-depth alongside
    -- the Python-layer guard in stamp_line_identity_or_warn — only ever set
    -- QboId to a NEW value when RealmId will end up populated, either from
    -- this call or from the row's own already-stamped value. A row with no
    -- realm anywhere (this call or prior) stays fully unstamped rather than
    -- landing in the QboId-set/RealmId-NULL half state found live in prod.
    DECLARE @ExistingQboId NVARCHAR(50), @ExistingRealmId NVARCHAR(50);
    SELECT @ExistingQboId = [QboId], @ExistingRealmId = [RealmId] FROM dbo.[BillLineItem] WHERE [Id] = @Id;
    DECLARE @RealmComplete BIT = CASE WHEN @RealmId IS NOT NULL OR @ExistingRealmId IS NOT NULL THEN 1 ELSE 0 END;

    IF @QboId IS NOT NULL AND @RealmComplete = 1
    BEGIN
        UPDATE sib SET sib.[QboId] = NULL, sib.[RealmId] = NULL, sib.[ModifiedDatetime] = SYSUTCDATETIME()
        FROM dbo.[BillLineItem] sib
        INNER JOIN dbo.[BillLineItem] tgt ON tgt.[BillId] = sib.[BillId]
        WHERE tgt.[Id] = @Id AND sib.[Id] <> @Id AND sib.[QboId] = @QboId;

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[BillLineItem]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL AND @RealmComplete = 1 THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND @RealmComplete = 1 AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
      );

    -- Reflect what's actually stored, not the raw input params — @RealmComplete
    -- can skip the QboId write above, and echoing @QboId regardless would tell
    -- a caller a stamp succeeded when it didn't (no current caller reads these
    -- 2 columns from this result, only [Stolen] — but a future one shouldn't
    -- be misled). Computed from the pre-read + the same CASE logic as the
    -- UPDATE above rather than a second table read — the values are already
    -- fully known at this point.
    DECLARE @FinalQboId NVARCHAR(50) = CASE WHEN @QboId IS NOT NULL AND @RealmComplete = 1 THEN @QboId ELSE @ExistingQboId END;
    DECLARE @FinalRealmId NVARCHAR(50) = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE @ExistingRealmId END;
    SELECT @Id AS [Id], @FinalQboId AS [QboId], @FinalRealmId AS [RealmId], @Stolen AS [Stolen];
END;
GO

-- U-293: parent-scoped direct identity read for the line fast path. A QBO line
-- id is unique only within its parent transaction (confirmed against live prod:
-- real cross-parent QboId collisions exist for every line family), matching the
-- live UQ_BillLineItem_BillId_QboId index this keys against — never look up a
-- line by QboId alone.
CREATE OR ALTER PROCEDURE ReadBillLineItemByBillIdAndQboId
(
    @BillId BIGINT,
    @QboId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BillId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft],
        [QboId],
        [RealmId]
    FROM dbo.[BillLineItem]
    WHERE [BillId] = @BillId AND [QboId] = @QboId;
END;
GO
