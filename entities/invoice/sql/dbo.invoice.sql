IF OBJECT_ID('dbo.Invoice', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Invoice]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ProjectId] BIGINT NOT NULL,
    [PaymentTermId] BIGINT NULL,
    [InvoiceDate] DATETIME2(3) NOT NULL,
    [DueDate] DATETIME2(3) NOT NULL,
    [InvoiceNumber] NVARCHAR(50) NOT NULL,
    [TotalAmount] DECIMAL(18,2) NULL,
    [Memo] NVARCHAR(MAX) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_Invoice_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]),
    CONSTRAINT [FK_Invoice_PaymentTerm] FOREIGN KEY ([PaymentTermId]) REFERENCES [dbo].[PaymentTerm]([Id])
);
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Invoice_ProjectId' AND object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
CREATE INDEX IX_Invoice_ProjectId ON [dbo].[Invoice] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Invoice_InvoiceDate' AND object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
CREATE INDEX IX_Invoice_InvoiceDate ON [dbo].[Invoice] ([InvoiceDate]);
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Invoice_InvoiceNumber' AND object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
CREATE INDEX IX_Invoice_InvoiceNumber ON [dbo].[Invoice] ([InvoiceNumber]);
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Invoice_PaymentTermId' AND object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
CREATE INDEX IX_Invoice_PaymentTermId ON [dbo].[Invoice] ([PaymentTermId]);
END
GO

-- Unique constraint to prevent duplicate InvoiceNumber for the same ProjectId
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_Invoice_ProjectId_InvoiceNumber' AND parent_object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
ALTER TABLE [dbo].[Invoice]
ADD CONSTRAINT UQ_Invoice_ProjectId_InvoiceNumber UNIQUE ([ProjectId], [InvoiceNumber]);
END
GO


CREATE OR ALTER PROCEDURE CreateInvoice
(
    @ProjectId BIGINT,
    @PaymentTermId BIGINT NULL,
    @InvoiceDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @InvoiceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Invoice] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [PaymentTermId], [InvoiceDate], [DueDate], [InvoiceNumber], [TotalAmount], [Memo], [IsDraft], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[PaymentTermId],
        CONVERT(VARCHAR(19), INSERTED.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[InvoiceNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @ProjectId, @PaymentTermId, @InvoiceDate, @DueDate, @InvoiceNumber, @TotalAmount, @Memo, @IsDraft, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoices
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        i.[Id],
        i.[PublicId],
        i.[RowVersion],
        CONVERT(VARCHAR(19), i.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), i.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        i.[ProjectId],
        i.[PaymentTermId],
        CONVERT(VARCHAR(19), i.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), i.[DueDate], 120) AS [DueDate],
        i.[InvoiceNumber],
        i.[TotalAmount],
        i.[Memo],
        i.[IsDraft]
    FROM dbo.[Invoice] i
    WHERE dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, i.[ProjectId]) = 1
    ORDER BY i.[InvoiceDate] DESC, i.[InvoiceNumber] ASC;
    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceById
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
        [ProjectId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [InvoiceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Invoice]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceByPublicId
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
        [ProjectId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [InvoiceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Invoice]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceByInvoiceNumber
(
    @InvoiceNumber NVARCHAR(50)
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
        [ProjectId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [InvoiceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Invoice]
    WHERE [InvoiceNumber] = @InvoiceNumber;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceByInvoiceNumberAndProjectId
(
    @InvoiceNumber NVARCHAR(50),
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
        [ProjectId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [InvoiceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Invoice]
    WHERE [InvoiceNumber] = @InvoiceNumber AND [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateInvoiceById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ProjectId BIGINT,
    @PaymentTermId BIGINT NULL,
    @InvoiceDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @InvoiceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Invoice]
    SET
        [ModifiedDatetime] = @Now,
        [ProjectId] = @ProjectId,
        [PaymentTermId] = @PaymentTermId,
        [InvoiceDate] = @InvoiceDate,
        [DueDate] = @DueDate,
        [InvoiceNumber] = @InvoiceNumber,
        [TotalAmount] = @TotalAmount,
        [Memo] = @Memo,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[PaymentTermId],
        CONVERT(VARCHAR(19), INSERTED.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[InvoiceNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteInvoiceById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Invoice]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[PaymentTermId],
        CONVERT(VARCHAR(19), DELETED.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), DELETED.[DueDate], 120) AS [DueDate],
        DELETED.[InvoiceNumber],
        DELETED.[TotalAmount],
        DELETED.[Memo],
        DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoicesPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @ProjectId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @SortBy NVARCHAR(50) = 'InvoiceDate',
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;
    DECLARE @SortColumn NVARCHAR(50) = CASE @SortBy
        WHEN 'InvoiceNumber' THEN 'InvoiceNumber'
        WHEN 'InvoiceDate' THEN 'InvoiceDate'
        WHEN 'DueDate' THEN 'DueDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'ProjectId' THEN 'ProjectId'
        ELSE 'InvoiceDate'
    END;
    DECLARE @SortDir NVARCHAR(4) = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

    SELECT
        i.[Id],
        i.[PublicId],
        i.[RowVersion],
        CONVERT(VARCHAR(19), i.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), i.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        i.[ProjectId],
        i.[PaymentTermId],
        CONVERT(VARCHAR(19), i.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), i.[DueDate], 120) AS [DueDate],
        i.[InvoiceNumber],
        i.[TotalAmount],
        i.[Memo],
        i.[IsDraft]
    FROM dbo.[Invoice] i
    LEFT JOIN dbo.[Project] p ON i.[ProjectId] = p.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         i.[InvoiceNumber] LIKE '%' + @SearchTerm + '%' OR
         i.[Memo] LIKE '%' + @SearchTerm + '%' OR
         p.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), i.[InvoiceDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), i.[DueDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), i.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@ProjectId IS NULL OR i.[ProjectId] = @ProjectId)
        AND (@StartDate IS NULL OR i.[InvoiceDate] >= @StartDate)
        AND (@EndDate IS NULL OR i.[InvoiceDate] <= @EndDate)
        AND (@IsDraft IS NULL OR i.[IsDraft] = @IsDraft)
        AND dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, i.[ProjectId]) = 1
    ORDER BY
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'InvoiceNumber' THEN i.[InvoiceNumber] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'InvoiceNumber' THEN i.[InvoiceNumber] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'InvoiceDate' THEN i.[InvoiceDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'InvoiceDate' THEN i.[InvoiceDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'DueDate' THEN i.[DueDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'DueDate' THEN i.[DueDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'TotalAmount' THEN i.[TotalAmount] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'TotalAmount' THEN i.[TotalAmount] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'ProjectId' THEN i.[ProjectId] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ProjectId' THEN i.[ProjectId] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;
    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE CountInvoices
(
    @SearchTerm NVARCHAR(255) = NULL,
    @ProjectId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT COUNT(*) AS [TotalCount]
    FROM dbo.[Invoice] i
    LEFT JOIN dbo.[Project] p ON i.[ProjectId] = p.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         i.[InvoiceNumber] LIKE '%' + @SearchTerm + '%' OR
         i.[Memo] LIKE '%' + @SearchTerm + '%' OR
         p.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), i.[InvoiceDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), i.[DueDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), i.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@ProjectId IS NULL OR i.[ProjectId] = @ProjectId)
        AND (@StartDate IS NULL OR i.[InvoiceDate] >= @StartDate)
        AND (@EndDate IS NULL OR i.[InvoiceDate] <= @EndDate)
        AND (@IsDraft IS NULL OR i.[IsDraft] = @IsDraft)
        AND dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, i.[ProjectId]) = 1;
    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceSourceLinkLines
(
    @InvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    -- U-272: LineNum/ServiceDate/LinkedTxnType now read from the dbo-native
    -- InvoiceLineItemSourceProvenance mirror instead of qbo.InvoiceLine via
    -- the qbo.InvoiceLineItemInvoiceLine mapping. LEFT JOIN preserved as-is —
    -- not every InvoiceLineItem has provenance (e.g. never QBO-synced lines).
    SELECT
        ili.[Id] AS [InvoiceLineItemId],
        prov.[LineNum],
        ili.[Amount],
        ili.[Description],
        prov.[ServiceDate],
        ili.[SourceType],
        ili.[BillLineItemId],
        ili.[ExpenseLineItemId],
        ili.[BillCreditLineItemId],
        COALESCE(bli.[ProjectId], eli.[ProjectId], bcli.[ProjectId]) AS [SourceProjectId],
        prov.[LinkedTxnType],
        -- Hard-coded 0 until a later unit classifies markup derivatives via LinkedTxnId.
        CAST(0 AS BIT) AS [ManualDerivative]
    FROM dbo.[InvoiceLineItem] ili
    LEFT JOIN dbo.[InvoiceLineItemSourceProvenance] prov ON prov.[InvoiceLineItemId] = ili.[Id]
    LEFT JOIN dbo.[BillLineItem] bli ON bli.[Id] = ili.[BillLineItemId]
    LEFT JOIN dbo.[ExpenseLineItem] eli ON eli.[Id] = ili.[ExpenseLineItemId]
    LEFT JOIN dbo.[BillCreditLineItem] bcli ON bcli.[Id] = ili.[BillCreditLineItemId]
    WHERE ili.[InvoiceId] = @InvoiceId
    ORDER BY ili.[Id] ASC;
END;
GO


-- ⚠️ DEPLOY ORDER (U-272): this sproc's LineCtx CTE INNER JOINs
-- dbo.InvoiceLineItemSourceProvenance, which starts EMPTY on a fresh apply.
-- Applying this file WITHOUT immediately following with
-- `python scripts/backfill_invoice_line_source_provenance.py --apply` makes
-- this sproc return ZERO propose candidates for every already-mapped invoice
-- until the backfill completes — a silent read regression (no error, no data
-- loss), not a crash. Run the backfill in the SAME maintenance window as this
-- file's apply, before normal traffic re-hits the invoice source-link review UI.
CREATE OR ALTER PROCEDURE ProposeInvoiceSourceLinks
(
    @InvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @RealmId NVARCHAR(50);
    DECLARE @CustomerRefValue NVARCHAR(50);
    DECLARE @ProjectId BIGINT;

    -- U-272: header identity now reads straight off dbo (Phase 2 promoted
    -- Invoice.RealmId; Project.QboId IS the invoice's QBO CustomerRefValue —
    -- verified empirically 982/982 mapped invoices, single-realm system).
    -- Drops the qbo.InvoiceInvoice -> qbo.Invoice hop entirely, including for
    -- @ProjectId, which was always redundant with dbo.Invoice.ProjectId.
    SELECT
        @ProjectId = i.[ProjectId],
        @RealmId = i.[RealmId],
        @CustomerRefValue = p.[QboId]
    FROM dbo.[Invoice] i
    INNER JOIN dbo.[Project] p ON p.[Id] = i.[ProjectId]
    WHERE i.[Id] = @InvoiceId;

    ;WITH LineCtx AS (
        -- U-272: reads the dbo-native provenance mirror instead of
        -- qbo.InvoiceLineItemInvoiceLine -> qbo.InvoiceLine. QboAmount/
        -- QboDescription intentionally do NOT come from ili.Amount/
        -- ili.Description (those are human-editable snapshots — see
        -- InvoiceLineItemUpdate); TRY_CAST preserved (ServiceDate stays a
        -- raw string in the mirror, same as qbo.InvoiceLine.ServiceDate).
        SELECT
            ili.[Id] AS [InvoiceLineItemId],
            prov.[LineNum],
            prov.[QboAmount] AS [QboAmount],
            prov.[QboDescription] AS [QboDescription],
            TRY_CAST(prov.[ServiceDate] AS DATE) AS [ServiceDate],
            prov.[LinkedTxnType],
            prov.[LinkedTxnId],
            prov.[ItemRefValue] AS [QboItemRefValue]
        FROM dbo.[InvoiceLineItem] ili
        INNER JOIN dbo.[InvoiceLineItemSourceProvenance] prov ON prov.[InvoiceLineItemId] = ili.[Id]
        WHERE ili.[InvoiceId] = @InvoiceId
    )
    -- =========================================================================
    -- Tier 0 — direct LinkedTxn -> staged Bill/Purchase (U-186, U-244).
    -- Prefer this over the fingerprint tiers below: it is an exact QBO-id chain,
    -- so resolve_link_proposals (min-tier) picks it first.
    -- KEYSPACE: joins go QBO-string -> QBO-string (qb.QboId=lc.LinkedTxnId);
    -- qb.Id / qp.Id (BIGINT) are NEVER aliased as dbo ids — the dbo line id
    -- comes from the map table (map.BillLineItemId etc.).
    -- RC-mediated Tier-0 arms (LinkedTxn=ReimburseCharge -> rc.SourceTxnId) were
    -- removed as provably dead per U-242: qbo.ReimburseCharge.SourceTxnId is
    -- never populated by QBO at any lifecycle stage — see
    -- docs/rc_source_linking_signal_2026_08_16.md. Only direct Bill/Purchase
    -- LinkedTxn hops (0c/0d) remain.
    -- =========================================================================

    -- Tier 0c: InvoiceLine.LinkedTxn points straight at a Bill (no RC intermediary).
    SELECT
        lc.[InvoiceLineItemId],
        CAST(0 AS TINYINT) AS [Tier],
        N'BillLineItem' AS [SourceType],
        map.[BillLineItemId] AS [SourceLineItemId],
        dbli.[ProjectId] AS [SourceProjectId],
        bl.[LineNum] AS [SourceLineNum],
        CAST(0 AS BIT) AS [DirectDbo]
    FROM LineCtx lc
    INNER JOIN qbo.[Bill] qb ON qb.[QboId] = lc.[LinkedTxnId] AND qb.[RealmId] = @RealmId
    INNER JOIN qbo.[BillLine] bl ON bl.[QboBillId] = qb.[Id]
        AND ABS(bl.[Amount] - lc.[QboAmount]) < 0.01
    INNER JOIN qbo.[BillLineItemBillLine] map ON map.[QboBillLineId] = bl.[Id]
    INNER JOIN dbo.[BillLineItem] dbli ON dbli.[Id] = map.[BillLineItemId]
    WHERE lc.[LinkedTxnType] = N'Bill'

    UNION ALL

    -- Tier 0d: InvoiceLine.LinkedTxn points straight at a Purchase (no RC intermediary).
    SELECT
        lc.[InvoiceLineItemId],
        CAST(0 AS TINYINT) AS [Tier],
        N'ExpenseLineItem' AS [SourceType],
        map.[ExpenseLineItemId] AS [SourceLineItemId],
        deli.[ProjectId] AS [SourceProjectId],
        pl.[LineNum] AS [SourceLineNum],
        CAST(0 AS BIT) AS [DirectDbo]
    FROM LineCtx lc
    INNER JOIN qbo.[Purchase] qp ON qp.[QboId] = lc.[LinkedTxnId] AND qp.[RealmId] = @RealmId
    INNER JOIN qbo.[PurchaseLine] pl ON pl.[QboPurchaseId] = qp.[Id]
        AND ABS(pl.[Amount] - lc.[QboAmount]) < 0.01
    INNER JOIN qbo.[PurchaseLineExpenseLineItem] map ON map.[QboPurchaseLineId] = pl.[Id]
    INNER JOIN dbo.[ExpenseLineItem] deli ON deli.[Id] = map.[ExpenseLineItemId]
    WHERE lc.[LinkedTxnType] = N'Purchase'

    UNION ALL

    SELECT
        lc.[InvoiceLineItemId],
        CAST(1 AS TINYINT) AS [Tier],
        N'BillLineItem' AS [SourceType],
        map.[BillLineItemId] AS [SourceLineItemId],
        dbli.[ProjectId] AS [SourceProjectId],
        bl.[LineNum] AS [SourceLineNum],
        CAST(0 AS BIT) AS [DirectDbo]
    FROM LineCtx lc
    INNER JOIN qbo.[BillLine] bl
        ON bl.[CustomerRefValue] = @CustomerRefValue
        AND ABS(bl.[Amount] - lc.[QboAmount]) < 0.01
        AND COALESCE(bl.[Description], N'') = COALESCE(lc.[QboDescription], N'')
        -- ItemRefValue (U-244) is an OPTIONAL narrowing signal, not a required
        -- identity field like Description/Amount/CustomerRef above: NULL on the
        -- invoice line means "no cost-code signal, don't filter" rather than the
        -- COALESCE-both-empty "both sides must agree" idiom used for those fields.
        AND (lc.[QboItemRefValue] IS NULL OR bl.[ItemRefValue] = lc.[QboItemRefValue])
    INNER JOIN qbo.[Bill] qb ON qb.[Id] = bl.[QboBillId] AND qb.[RealmId] = @RealmId
        AND TRY_CAST(qb.[TxnDate] AS DATE) = lc.[ServiceDate]
    INNER JOIN qbo.[BillLineItemBillLine] map ON map.[QboBillLineId] = bl.[Id]
    INNER JOIN dbo.[BillLineItem] dbli ON dbli.[Id] = map.[BillLineItemId]

    UNION ALL

    SELECT
        lc.[InvoiceLineItemId],
        CAST(2 AS TINYINT) AS [Tier],
        N'ExpenseLineItem' AS [SourceType],
        map.[ExpenseLineItemId] AS [SourceLineItemId],
        deli.[ProjectId] AS [SourceProjectId],
        pl.[LineNum] AS [SourceLineNum],
        CAST(0 AS BIT) AS [DirectDbo]
    FROM LineCtx lc
    INNER JOIN qbo.[PurchaseLine] pl
        ON pl.[CustomerRefValue] = @CustomerRefValue
        AND ABS(pl.[Amount] - lc.[QboAmount]) < 0.01
        AND COALESCE(pl.[Description], N'') = COALESCE(lc.[QboDescription], N'')
        AND (lc.[QboItemRefValue] IS NULL OR pl.[ItemRefValue] = lc.[QboItemRefValue])
    INNER JOIN qbo.[Purchase] qp ON qp.[Id] = pl.[QboPurchaseId] AND qp.[RealmId] = @RealmId
        AND TRY_CAST(qp.[TxnDate] AS DATE) = lc.[ServiceDate]
    INNER JOIN qbo.[PurchaseLineExpenseLineItem] map ON map.[QboPurchaseLineId] = pl.[Id]
    INNER JOIN dbo.[ExpenseLineItem] deli ON deli.[Id] = map.[ExpenseLineItemId]

    UNION ALL

    SELECT
        lc.[InvoiceLineItemId],
        CAST(3 AS TINYINT) AS [Tier],
        N'BillCreditLineItem' AS [SourceType],
        map.[BillCreditLineItemId] AS [SourceLineItemId],
        dbcli.[ProjectId] AS [SourceProjectId],
        vcl.[LineNum] AS [SourceLineNum],
        CAST(0 AS BIT) AS [DirectDbo]
    FROM LineCtx lc
    INNER JOIN qbo.[VendorCreditLine] vcl
        ON vcl.[CustomerRefValue] = @CustomerRefValue
        AND ABS(ABS(vcl.[Amount]) - ABS(lc.[QboAmount])) < 0.01
        AND COALESCE(vcl.[Description], N'') = COALESCE(lc.[QboDescription], N'')
    INNER JOIN qbo.[VendorCredit] qvc ON qvc.[Id] = vcl.[QboVendorCreditId] AND qvc.[RealmId] = @RealmId
        AND TRY_CAST(qvc.[TxnDate] AS DATE) = lc.[ServiceDate]
    INNER JOIN qbo.[VendorCreditLineItemBillCreditLineItem] map ON map.[QboVendorCreditLineId] = vcl.[Id]
    INNER JOIN dbo.[BillCreditLineItem] dbcli ON dbcli.[Id] = map.[BillCreditLineItemId]

    UNION ALL

    SELECT
        lc.[InvoiceLineItemId],
        CAST(1 AS TINYINT) AS [Tier],
        N'BillLineItem' AS [SourceType],
        bli.[Id] AS [SourceLineItemId],
        bli.[ProjectId] AS [SourceProjectId],
        CAST(NULL AS INT) AS [SourceLineNum],
        CAST(1 AS BIT) AS [DirectDbo]
    FROM LineCtx lc
    INNER JOIN dbo.[BillLineItem] bli
        ON ABS(bli.[Amount] - lc.[QboAmount]) < 0.01
        AND COALESCE(bli.[Description], N'') = COALESCE(lc.[QboDescription], N'')
    INNER JOIN dbo.[Bill] b ON b.[Id] = bli.[BillId]
        AND TRY_CAST(b.[BillDate] AS DATE) = lc.[ServiceDate]
    WHERE NOT EXISTS (
        SELECT 1
        FROM qbo.[BillLine] bl
        INNER JOIN qbo.[Bill] qb ON qb.[Id] = bl.[QboBillId] AND qb.[RealmId] = @RealmId
        INNER JOIN qbo.[BillLineItemBillLine] map ON map.[QboBillLineId] = bl.[Id]
        WHERE bl.[CustomerRefValue] = @CustomerRefValue
            AND ABS(bl.[Amount] - lc.[QboAmount]) < 0.01
            AND COALESCE(bl.[Description], N'') = COALESCE(lc.[QboDescription], N'')
            AND (lc.[QboItemRefValue] IS NULL OR bl.[ItemRefValue] = lc.[QboItemRefValue])
            AND TRY_CAST(qb.[TxnDate] AS DATE) = lc.[ServiceDate]
    )
    AND NOT EXISTS (
        SELECT 1
        FROM qbo.[PurchaseLine] pl
        INNER JOIN qbo.[Purchase] qp ON qp.[Id] = pl.[QboPurchaseId] AND qp.[RealmId] = @RealmId
        INNER JOIN qbo.[PurchaseLineExpenseLineItem] map ON map.[QboPurchaseLineId] = pl.[Id]
        WHERE map.[ExpenseLineItemId] IS NOT NULL
            AND pl.[CustomerRefValue] = @CustomerRefValue
            AND ABS(pl.[Amount] - lc.[QboAmount]) < 0.01
            AND COALESCE(pl.[Description], N'') = COALESCE(lc.[QboDescription], N'')
            AND (lc.[QboItemRefValue] IS NULL OR pl.[ItemRefValue] = lc.[QboItemRefValue])
            AND TRY_CAST(qp.[TxnDate] AS DATE) = lc.[ServiceDate]
    )
    AND NOT EXISTS (
        SELECT 1
        FROM qbo.[VendorCreditLine] vcl
        INNER JOIN qbo.[VendorCredit] qvc ON qvc.[Id] = vcl.[QboVendorCreditId] AND qvc.[RealmId] = @RealmId
        INNER JOIN qbo.[VendorCreditLineItemBillCreditLineItem] map ON map.[QboVendorCreditLineId] = vcl.[Id]
        WHERE map.[BillCreditLineItemId] IS NOT NULL
            AND vcl.[CustomerRefValue] = @CustomerRefValue
            AND ABS(ABS(vcl.[Amount]) - ABS(lc.[QboAmount])) < 0.01
            AND COALESCE(vcl.[Description], N'') = COALESCE(lc.[QboDescription], N'')
            AND TRY_CAST(qvc.[TxnDate] AS DATE) = lc.[ServiceDate]
    );
END;
GO


CREATE OR ALTER PROCEDURE BackfillLinkedSourceProjectId
(
    @SourceType NVARCHAR(50),
    @Id BIGINT,
    @ProjectId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    IF @SourceType = N'BillLineItem'
    BEGIN
        UPDATE dbo.[BillLineItem]
        SET [ProjectId] = @ProjectId,
            [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] = @Id AND [ProjectId] IS NULL;
    END
    ELSE IF @SourceType = N'ExpenseLineItem'
    BEGIN
        UPDATE dbo.[ExpenseLineItem]
        SET [ProjectId] = @ProjectId,
            [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] = @Id AND [ProjectId] IS NULL;
    END
    ELSE IF @SourceType = N'BillCreditLineItem'
    BEGIN
        UPDATE dbo.[BillCreditLineItem]
        SET [ProjectId] = @ProjectId,
            [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] = @Id AND [ProjectId] IS NULL;
    END
END;
GO


CREATE OR ALTER PROCEDURE ComputeInvoiceDrawMatrix
(
    @InvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @QboInvoiceId BIGINT;

    SELECT @QboInvoiceId = ii.[QboInvoiceId]
    FROM qbo.[InvoiceInvoice] ii
    WHERE ii.[InvoiceId] = @InvoiceId;

    SELECT
        (SELECT COUNT(*)
         FROM qbo.[InvoiceLine]
         WHERE [QboInvoiceId] = @QboInvoiceId) AS [QboLineCount],
        (SELECT [TotalAmt]
         FROM qbo.[Invoice]
         WHERE [Id] = @QboInvoiceId) AS [QboTotalAmt],
        (SELECT COUNT(*)
         FROM dbo.[InvoiceLineItem]
         WHERE [InvoiceId] = @InvoiceId) AS [DboLineCount],
        (SELECT SUM([Amount])
         FROM dbo.[InvoiceLineItem]
         WHERE [InvoiceId] = @InvoiceId) AS [DboLineSum],
        (SELECT [TotalAmount]
         FROM dbo.[Invoice]
         WHERE [Id] = @InvoiceId) AS [DboTotalAmount],
        (SELECT COUNT(*)
         FROM dbo.[InvoiceLineItem]
         WHERE [InvoiceId] = @InvoiceId
           AND [SourceType] <> N'Manual') AS [SourcedLineCount],
        (SELECT COUNT(*)
         FROM dbo.[InvoiceLineItem] ili
         LEFT JOIN dbo.[BillLineItem] b ON b.[Id] = ili.[BillLineItemId]
         LEFT JOIN dbo.[ExpenseLineItem] e ON e.[Id] = ili.[ExpenseLineItemId]
         LEFT JOIN dbo.[BillCreditLineItem] c ON c.[Id] = ili.[BillCreditLineItemId]
         WHERE ili.[InvoiceId] = @InvoiceId
           AND COALESCE(b.[IsBilled], e.[IsBilled], c.[IsBilled], 0) = 1) AS [BilledSourceCount];
END;
GO

CREATE OR ALTER PROCEDURE SetInvoiceQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @SyncToken NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    IF @QboId IS NOT NULL
    BEGIN
        UPDATE dbo.[Invoice]
        SET [QboId] = NULL, [RealmId] = NULL, [SyncToken] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[Invoice]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [SyncToken] = CASE WHEN @SyncToken IS NOT NULL THEN @SyncToken ELSE [SyncToken] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[QboId],
        INSERTED.[RealmId],
        INSERTED.[SyncToken],
        @Stolen AS [Stolen]
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
         OR (@SyncToken IS NOT NULL AND ([SyncToken] IS NULL OR [SyncToken] <> @SyncToken))
      );
END;
GO
