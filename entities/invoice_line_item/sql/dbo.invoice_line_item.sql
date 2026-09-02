IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[InvoiceLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [InvoiceId] BIGINT NOT NULL,
    [SourceType] NVARCHAR(50) NOT NULL,
    [BillLineItemId] BIGINT NULL,
    [ExpenseLineItemId] BIGINT NULL,
    [BillCreditLineItemId] BIGINT NULL,
    [EmployeeLaborLineItemId] BIGINT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [Markup] DECIMAL(18,4) NULL,
    [Price] DECIMAL(18,2) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_InvoiceLineItem_Invoice] FOREIGN KEY ([InvoiceId]) REFERENCES [dbo].[Invoice]([Id]),
    CONSTRAINT [FK_InvoiceLineItem_BillLineItem] FOREIGN KEY ([BillLineItemId]) REFERENCES [dbo].[BillLineItem]([Id]),
    CONSTRAINT [FK_InvoiceLineItem_ExpenseLineItem] FOREIGN KEY ([ExpenseLineItemId]) REFERENCES [dbo].[ExpenseLineItem]([Id]),
    CONSTRAINT [FK_InvoiceLineItem_BillCreditLineItem] FOREIGN KEY ([BillCreditLineItemId]) REFERENCES [dbo].[BillCreditLineItem]([Id])
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.InvoiceLineItem') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_InvoiceLineItem_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItem_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD CONSTRAINT [FK_InvoiceLineItem_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_InvoiceId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_InvoiceId ON [dbo].[InvoiceLineItem] ([InvoiceId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_BillLineItemId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_BillLineItemId ON [dbo].[InvoiceLineItem] ([BillLineItemId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_ExpenseLineItemId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_ExpenseLineItemId ON [dbo].[InvoiceLineItem] ([ExpenseLineItemId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_BillCreditLineItemId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_BillCreditLineItemId ON [dbo].[InvoiceLineItem] ([BillCreditLineItemId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_PublicId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_PublicId ON [dbo].[InvoiceLineItem] ([PublicId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'SubCostCodeId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [SubCostCodeId] BIGINT NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'Quantity' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [Quantity] DECIMAL(18,4) NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'Rate' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [Rate] DECIMAL(18,4) NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItem_SubCostCode' AND parent_object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD CONSTRAINT [FK_InvoiceLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_SubCostCodeId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    CREATE INDEX IX_InvoiceLineItem_SubCostCodeId ON [dbo].[InvoiceLineItem] ([SubCostCodeId]);
END
GO


-- EmployeeLabor source column (2026-05-27 migration, ported so base re-runs are self-sufficient)
IF COL_LENGTH('dbo.[InvoiceLineItem]', 'EmployeeLaborLineItemId') IS NULL
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [EmployeeLaborLineItemId] BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItem_EmployeeLaborLineItem')
   AND OBJECT_ID('dbo.[EmployeeLaborLineItem]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem]
    ADD CONSTRAINT [FK_InvoiceLineItem_EmployeeLaborLineItem]
        FOREIGN KEY ([EmployeeLaborLineItemId]) REFERENCES [dbo].[EmployeeLaborLineItem]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_EmployeeLaborLineItemId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    CREATE INDEX [IX_InvoiceLineItem_EmployeeLaborLineItemId]
        ON [dbo].[InvoiceLineItem] ([EmployeeLaborLineItemId]);
END
GO

-- U-238b added QboId/RealmId + UQ_InvoiceLineItem_InvoiceId_QboId live via
-- scripts/migrations/238b_qbo_identity_lines.sql but never ported the DDL into
-- this base file (the same from-scratch-build gap U-277/U-290/U-293 found and
-- fixed for company/address/vendor/bill_line_item) — SetInvoiceLineItemQboIdentity
-- below has silently depended on columns this file never declared. Closed here
-- (U-293b), verbatim against the live migration so a from-scratch build matches
-- prod exactly.
IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.InvoiceLineItem') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.InvoiceLineItem') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_InvoiceLineItem_InvoiceId_QboId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem')
)
BEGIN
    CREATE UNIQUE INDEX UQ_InvoiceLineItem_InvoiceId_QboId ON [dbo].[InvoiceLineItem] ([InvoiceId], [QboId]) WHERE [QboId] IS NOT NULL;
END
GO

CREATE OR ALTER PROCEDURE CreateInvoiceLineItem
(
    @InvoiceId BIGINT,
    @SourceType NVARCHAR(50),
    @BillLineItemId BIGINT NULL,
    @ExpenseLineItemId BIGINT NULL,
    @BillCreditLineItemId BIGINT NULL,
    @EmployeeLaborLineItemId BIGINT = NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[InvoiceLineItem]
        ([CreatedDatetime], [ModifiedDatetime], [InvoiceId], [SourceType],
         [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
         [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft],
         [CreatedByUserId])
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceId], INSERTED.[SourceType],
        INSERTED.[BillLineItemId], INSERTED.[ExpenseLineItemId], INSERTED.[BillCreditLineItemId],
        INSERTED.[EmployeeLaborLineItemId],
        INSERTED.[SubCostCodeId], INSERTED.[Description], INSERTED.[Quantity], INSERTED.[Rate],
        INSERTED.[Amount], INSERTED.[Markup], INSERTED.[Price], INSERTED.[IsDraft]
    VALUES (@Now, @Now, @InvoiceId, @SourceType,
            @BillLineItemId, @ExpenseLineItemId, @BillCreditLineItemId, @EmployeeLaborLineItemId,
            @SubCostCodeId, @Description, @Quantity, @Rate, @Amount, @Markup, @Price, @IsDraft,
            COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceLineItems
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceId], [SourceType],
        [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft],
        -- U-362: added QboId/RealmId — InvoiceInvoiceConnector.preload_caches()
        -- feeds this into the line connector's readopt candidate pool
        -- (_manual_line_candidates), which depends on this column to tell a
        -- stamped line from an unstamped one; omitting it made the whole
        -- caches_preloaded=True path readopt-blind (every cached line looked
        -- unstamped), the same self-rollback bug class as the other 4 sprocs
        -- fixed in this unit.
        [QboId], [RealmId]
    FROM dbo.[InvoiceLineItem]
    ORDER BY [CreatedDatetime] DESC;
    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceId], [SourceType],
        [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft],
        -- U-362: added QboId/RealmId — the dbo-only line fast path's post-stamp
        -- re-read (read_by_id) verifies a stamp landed by comparing this column;
        -- omitting it made every CREATE self-rollback (same class of bug U-361's
        -- code review caught in ReadBillCreditLineItemById).
        [QboId], [RealmId]
    FROM dbo.[InvoiceLineItem]
    WHERE [Id] = @Id;
    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceLineItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceId], [SourceType],
        [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft],
        [QboId], [RealmId]
    FROM dbo.[InvoiceLineItem]
    WHERE [PublicId] = @PublicId;
    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceLineItemsByInvoiceId
(
    @InvoiceId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceId], [SourceType],
        [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft],
        -- U-362: added QboId/RealmId — InvoiceInvoiceConnector._has_qbo_line_
        -- provenance and the line connector's readopt-candidate scan both read
        -- this column off rows from this sproc now (dbo-native, no more
        -- qbo.InvoiceLineItemInvoiceLine mapping hop); omitting it silently
        -- read every line as unstamped.
        [QboId], [RealmId]
    FROM dbo.[InvoiceLineItem]
    WHERE [InvoiceId] = @InvoiceId
    ORDER BY [CreatedDatetime] ASC;
    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateInvoiceLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @InvoiceId BIGINT,
    @SourceType NVARCHAR(50),
    @BillLineItemId BIGINT NULL,
    @ExpenseLineItemId BIGINT NULL,
    @BillCreditLineItemId BIGINT NULL,
    @EmployeeLaborLineItemId BIGINT = NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- Source FKs use CASE WHEN preserve-on-NULL so partial updates don't
    -- orphan the link to the source line. Same pattern as existing source
    -- columns.
    UPDATE dbo.[InvoiceLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [InvoiceId] = @InvoiceId,
        [SourceType] = @SourceType,
        [BillLineItemId]          = CASE WHEN @BillLineItemId          IS NULL THEN [BillLineItemId]          ELSE @BillLineItemId          END,
        [ExpenseLineItemId]       = CASE WHEN @ExpenseLineItemId       IS NULL THEN [ExpenseLineItemId]       ELSE @ExpenseLineItemId       END,
        [BillCreditLineItemId]    = CASE WHEN @BillCreditLineItemId    IS NULL THEN [BillCreditLineItemId]    ELSE @BillCreditLineItemId    END,
        [EmployeeLaborLineItemId] = CASE WHEN @EmployeeLaborLineItemId IS NULL THEN [EmployeeLaborLineItemId] ELSE @EmployeeLaborLineItemId END,
        [SubCostCodeId] = @SubCostCodeId,
        [Description] = @Description,
        [Quantity] = @Quantity,
        [Rate] = @Rate,
        [Amount] = @Amount,
        [Markup] = @Markup,
        [Price] = @Price,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceId], INSERTED.[SourceType],
        INSERTED.[BillLineItemId], INSERTED.[ExpenseLineItemId], INSERTED.[BillCreditLineItemId],
        INSERTED.[EmployeeLaborLineItemId],
        INSERTED.[SubCostCodeId], INSERTED.[Description], INSERTED.[Quantity], INSERTED.[Rate],
        INSERTED.[Amount], INSERTED.[Markup], INSERTED.[Price], INSERTED.[IsDraft],
        -- U-362: the HIT-path update return value should carry the row's real
        -- dbo-native identity, not silently read back as None (same class of
        -- bug U-361's code review caught in UpdateBillCreditLineItemById).
        INSERTED.[QboId], INSERTED.[RealmId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE LinkInvoiceLineItemSource
(
    @InvoiceLineItemId BIGINT,
    @SourceType NVARCHAR(50),
    @BillLineItemId BIGINT = NULL,
    @ExpenseLineItemId BIGINT = NULL,
    @BillCreditLineItemId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    IF @SourceType IS NULL
        THROW 50000, 'LinkInvoiceLineItemSource: @SourceType is required.', 1;

    DECLARE @FkCount INT =
        (CASE WHEN @BillLineItemId IS NOT NULL THEN 1 ELSE 0 END)
        + (CASE WHEN @ExpenseLineItemId IS NOT NULL THEN 1 ELSE 0 END)
        + (CASE WHEN @BillCreditLineItemId IS NOT NULL THEN 1 ELSE 0 END);

    IF @FkCount <> 1
        THROW 50000, 'LinkInvoiceLineItemSource: exactly one of @BillLineItemId, @ExpenseLineItemId, @BillCreditLineItemId must be non-NULL.', 1;

    IF @SourceType = N'BillLineItem' AND @BillLineItemId IS NULL
        THROW 50000, 'LinkInvoiceLineItemSource: @SourceType BillLineItem requires @BillLineItemId.', 1;

    IF @SourceType = N'ExpenseLineItem' AND @ExpenseLineItemId IS NULL
        THROW 50000, 'LinkInvoiceLineItemSource: @SourceType ExpenseLineItem requires @ExpenseLineItemId.', 1;

    IF @SourceType = N'BillCreditLineItem' AND @BillCreditLineItemId IS NULL
        THROW 50000, 'LinkInvoiceLineItemSource: @SourceType BillCreditLineItem requires @BillCreditLineItemId.', 1;

    IF @SourceType NOT IN (N'BillLineItem', N'ExpenseLineItem', N'BillCreditLineItem')
        OR (@SourceType <> N'BillLineItem' AND @BillLineItemId IS NOT NULL)
        OR (@SourceType <> N'ExpenseLineItem' AND @ExpenseLineItemId IS NOT NULL)
        OR (@SourceType <> N'BillCreditLineItem' AND @BillCreditLineItemId IS NOT NULL)
        THROW 50000, 'LinkInvoiceLineItemSource: @SourceType must match the single non-NULL source FK column.', 1;

    UPDATE dbo.[InvoiceLineItem]
    SET
        [SourceType] = @SourceType,
        [BillLineItemId] = @BillLineItemId,
        [ExpenseLineItemId] = @ExpenseLineItemId,
        [BillCreditLineItemId] = @BillCreditLineItemId,
        [EmployeeLaborLineItemId] = NULL,
        -- U-344 path B: a relabel ONTO BillCreditLineItem must land the same
        -- signed-negative invariant the create-time write enforces (Phase A),
        -- atomically with the FK repoint (no separate follow-up write to race
        -- a concurrent RowVersion bump). -ABS is idempotent (a no-op on an
        -- already-negative Price/Amount) and NULL-safe (a NULL stays NULL).
        -- Every OTHER @SourceType leaves Price/Amount untouched — in
        -- particular ExpenseLineItem's own sign convention (Expense.IsCredit)
        -- is out of this sproc's scope and must not be second-guessed here.
        [Price] = CASE
            WHEN @SourceType = N'BillCreditLineItem' AND [Price] IS NOT NULL
                 THEN -ABS([Price])
            ELSE [Price]
        END,
        [Amount] = CASE
            WHEN @SourceType = N'BillCreditLineItem' AND [Amount] IS NOT NULL
                 THEN -ABS([Amount])
            ELSE [Amount]
        END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceId],
        INSERTED.[SourceType],
        INSERTED.[BillLineItemId],
        INSERTED.[ExpenseLineItemId],
        INSERTED.[BillCreditLineItemId],
        INSERTED.[EmployeeLaborLineItemId],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft]
    WHERE [Id] = @InvoiceLineItemId;
END;
GO


CREATE OR ALTER PROCEDURE NullifyInvoiceLineItemsByBillLineItemId
(
    @BillLineItemId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[InvoiceLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [BillLineItemId] = NULL
    WHERE [BillLineItemId] = @BillLineItemId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteInvoiceLineItemsByBillLineItemId
(
    @BillLineItemId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Remove InvoiceLineItemAttachment join records first (FK constraint)
    DELETE ila
    FROM dbo.InvoiceLineItemAttachment ila
    JOIN dbo.InvoiceLineItem ili ON ili.Id = ila.InvoiceLineItemId
    WHERE ili.BillLineItemId = @BillLineItemId;

    -- Remove InvoiceLineItemSourceProvenance rows first (FK constraint, U-272)
    DELETE prov
    FROM dbo.InvoiceLineItemSourceProvenance prov
    JOIN dbo.InvoiceLineItem ili ON ili.Id = prov.InvoiceLineItemId
    WHERE ili.BillLineItemId = @BillLineItemId;

    -- Delete the InvoiceLineItem records
    DELETE FROM dbo.InvoiceLineItem
    WHERE BillLineItemId = @BillLineItemId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteInvoiceLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    -- U-272 made this sproc multi-statement (a DELETE now precedes the
    -- OUTPUT-clause DELETE below) — SET NOCOUNT ON is required or the leading
    -- DELETE's rowcount message becomes the first "result" pyodbc's
    -- cursor.fetchone() reads, hiding the OUTPUT row (see CLAUDE.md's
    -- "Stored procedure result-set discipline").
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    -- U-272: clear the un-cascaded InvoiceLineItemSourceProvenance row in the
    -- SAME transaction as the header delete (not a separate, independently
    -- committed Python call) — closes the race where a concurrent QBO pull
    -- re-inserts a provenance row between two separately-committed steps.
    DELETE FROM dbo.[InvoiceLineItemSourceProvenance]
    WHERE [InvoiceLineItemId] = @Id;

    DELETE FROM dbo.[InvoiceLineItem]
    OUTPUT
        DELETED.[Id], DELETED.[PublicId], DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[InvoiceId], DELETED.[SourceType],
        DELETED.[BillLineItemId], DELETED.[ExpenseLineItemId], DELETED.[BillCreditLineItemId],
        DELETED.[SubCostCodeId], DELETED.[Description], DELETED.[Quantity], DELETED.[Rate],
        DELETED.[Amount], DELETED.[Markup], DELETED.[Price], DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE SetInvoiceLineItemQboIdentity
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
    -- is not a complete identity without RealmId. Only ever set QboId to a
    -- NEW value when RealmId will end up populated, either from this call or
    -- from the row's own already-stamped value — mirrors
    -- dbo.bill_line_item.sql's SetBillLineItemQboIdentity guard exactly.
    DECLARE @ExistingQboId NVARCHAR(50), @ExistingRealmId NVARCHAR(50);
    SELECT @ExistingQboId = [QboId], @ExistingRealmId = [RealmId] FROM dbo.[InvoiceLineItem] WHERE [Id] = @Id;
    DECLARE @RealmComplete BIT = CASE WHEN @RealmId IS NOT NULL OR @ExistingRealmId IS NOT NULL THEN 1 ELSE 0 END;

    IF @QboId IS NOT NULL AND @RealmComplete = 1
    BEGIN
        UPDATE sib SET sib.[QboId] = NULL, sib.[RealmId] = NULL, sib.[ModifiedDatetime] = SYSUTCDATETIME()
        FROM dbo.[InvoiceLineItem] sib
        INNER JOIN dbo.[InvoiceLineItem] tgt ON tgt.[InvoiceId] = sib.[InvoiceId]
        WHERE tgt.[Id] = @Id AND sib.[Id] <> @Id AND sib.[QboId] = @QboId;

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[InvoiceLineItem]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL AND @RealmComplete = 1 THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND @RealmComplete = 1 AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
      );

    DECLARE @FinalQboId NVARCHAR(50) = CASE WHEN @QboId IS NOT NULL AND @RealmComplete = 1 THEN @QboId ELSE @ExistingQboId END;
    DECLARE @FinalRealmId NVARCHAR(50) = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE @ExistingRealmId END;
    SELECT @Id AS [Id], @FinalQboId AS [QboId], @FinalRealmId AS [RealmId], @Stolen AS [Stolen];
END;
GO

-- U-293b: parent-scoped direct identity read for the line fast path, mirroring
-- dbo.bill_line_item.sql's ReadBillLineItemByBillIdAndQboId. A QBO line id is
-- unique only within its parent transaction (confirmed against live prod: real
-- cross-parent QboId collisions exist for every line family), matching the live
-- UQ_InvoiceLineItem_InvoiceId_QboId index this keys against — never look up a
-- line by QboId alone.
CREATE OR ALTER PROCEDURE ReadInvoiceLineItemByInvoiceIdAndQboId
(
    @InvoiceId BIGINT,
    @QboId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceId], [SourceType],
        [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft],
        [QboId], [RealmId]
    FROM dbo.[InvoiceLineItem]
    WHERE [InvoiceId] = @InvoiceId AND [QboId] = @QboId;
END;
GO


-- =========================================================================
-- U-272 (staging-removal Phase 3): dbo-native QBO source-link provenance,
-- 1:1 with InvoiceLineItem. Distinct from InvoiceLineItem.Amount/Description
-- (user-editable "snapshot" fields, see InvoiceLineItemUpdate) — QboAmount/
-- QboDescription are an immutable mirror of the QBO invoice line as last
-- pulled, needed so a human edit never corrupts ProposeInvoiceSourceLinks'
-- fingerprint matching. ServiceDate stays NVARCHAR(50) (raw QBO string, not
-- DATE) to preserve ProposeInvoiceSourceLinks' existing TRY_CAST-to-NULL
-- behavior on malformed QBO dates rather than throwing on insert.
-- =========================================================================
IF OBJECT_ID('dbo.InvoiceLineItemSourceProvenance', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[InvoiceLineItemSourceProvenance]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [InvoiceLineItemId] BIGINT NOT NULL,
    [LineNum] INT NULL,
    [QboAmount] DECIMAL(18,2) NULL,
    [QboDescription] NVARCHAR(4000) NULL,
    [ServiceDate] NVARCHAR(50) NULL,
    [LinkedTxnType] NVARCHAR(64) NULL,
    [LinkedTxnId] NVARCHAR(50) NULL,
    [ItemRefValue] NVARCHAR(50) NULL,
    CONSTRAINT [FK_InvoiceLineItemSourceProvenance_InvoiceLineItem] FOREIGN KEY ([InvoiceLineItemId]) REFERENCES [dbo].[InvoiceLineItem]([Id]),
    CONSTRAINT [UQ_InvoiceLineItemSourceProvenance_InvoiceLineItemId] UNIQUE ([InvoiceLineItemId])
);
END
GO

-- No separate IX_...InvoiceLineItemId index: UQ_InvoiceLineItemSourceProvenance_InvoiceLineItemId
-- above already creates a unique index on that same single column.


CREATE OR ALTER PROCEDURE UpsertInvoiceLineItemSourceProvenance
(
    @InvoiceLineItemId BIGINT,
    @LineNum INT,
    @QboAmount DECIMAL(18,2),
    @QboDescription NVARCHAR(4000),
    @ServiceDate NVARCHAR(50),
    @LinkedTxnType NVARCHAR(64),
    @LinkedTxnId NVARCHAR(50),
    @ItemRefValue NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    -- Mirror semantics, not merge: every field is SET unconditionally (no CASE
    -- WHEN NULL-preserve guard) because this table always reflects the most
    -- recent QBO pull, same as qbo.InvoiceLine's own UpdateQboInvoiceLineById.
    -- UPDATE-first / INSERT-fallback via a retry loop (not a duplicated
    -- CATCH-block UPDATE) so the SET list appears exactly once: a concurrent
    -- caller that wins the INSERT race just makes the next pass's UPDATE find
    -- the row (same shape as qbo.api_usage.sql's IncrementQboApiUsage, which
    -- accepts the same duplication for its much shorter 1-field SET list).
    DECLARE @Retry BIT = 1;
    WHILE @Retry = 1
    BEGIN
        SET @Retry = 0;

        UPDATE dbo.[InvoiceLineItemSourceProvenance]
        SET
            [LineNum] = @LineNum,
            [QboAmount] = @QboAmount,
            [QboDescription] = @QboDescription,
            [ServiceDate] = @ServiceDate,
            [LinkedTxnType] = @LinkedTxnType,
            [LinkedTxnId] = @LinkedTxnId,
            [ItemRefValue] = @ItemRefValue,
            [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [InvoiceLineItemId] = @InvoiceLineItemId;

        IF @@ROWCOUNT = 0
        BEGIN
            BEGIN TRY
                INSERT INTO dbo.[InvoiceLineItemSourceProvenance] (
                    [CreatedDatetime], [ModifiedDatetime], [InvoiceLineItemId],
                    [LineNum], [QboAmount], [QboDescription], [ServiceDate],
                    [LinkedTxnType], [LinkedTxnId], [ItemRefValue]
                )
                VALUES (
                    SYSUTCDATETIME(), SYSUTCDATETIME(), @InvoiceLineItemId,
                    @LineNum, @QboAmount, @QboDescription, @ServiceDate,
                    @LinkedTxnType, @LinkedTxnId, @ItemRefValue
                );
            END TRY
            BEGIN CATCH
                -- A concurrent caller for the same InvoiceLineItemId won the
                -- race and inserted between this pass's UPDATE-miss and
                -- INSERT. Loop back instead of raising — the UPDATE above
                -- will find that row on the next pass (at most one extra
                -- pass in practice; the loop form, not a hardcoded single
                -- retry, keeps this correct under N-way concurrency too).
                IF ERROR_NUMBER() IN (2601, 2627)
                    SET @Retry = 1;
                ELSE
                    THROW;
            END CATCH
        END
    END
END;
GO
