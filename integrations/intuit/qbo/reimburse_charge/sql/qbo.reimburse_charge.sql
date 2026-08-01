GO

-- =============================================================================
-- qbo.ReimburseCharge — durable staging for QBO ReimburseCharge records (U-186).
--
-- QBO auto-creates a ReimburseCharge (RC) for every Bill/Purchase line marked
-- Billable with a CustomerRef. Each RC carries a reverse LinkedTxn back to the
-- source Bill/Purchase (+ that source's line id). QBO DROPS that reverse
-- LinkedTxn once the RC is consumed by an invoice (HasBeenInvoiced=true, KI-32),
-- so we capture RCs into this table on scheduler cadence WHILE still un-invoiced
-- and PRESERVE the captured source pointer across the invoiced-flip re-pull.
--
-- Deterministic Tier-0 invoice-line linking then resolves:
--   qbo.InvoiceLine.LinkedTxnId  ->  qbo.ReimburseCharge (QboId)
--                                ->  source Bill/Purchase (SourceTxnId)
--                                ->  dbo Bill/Expense line item.
--
-- KEYSPACE: QboId / SourceTxnId / SourceTxnLineId are QBO STRING ids
-- (qbo.*.Id BIGINT keyspace is disjoint — never conflate). Pull-only staging:
-- no delete / reconcile-delete sproc (a captured pointer must survive the flip).
-- =============================================================================

GO

IF OBJECT_ID('qbo.ReimburseCharge', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[ReimburseCharge]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [CustomerRefValue] NVARCHAR(50) NULL,
    [CustomerRefName] NVARCHAR(255) NULL,
    [TxnDate] NVARCHAR(50) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [HasBeenInvoiced] BIT NULL,
    [SourceTxnType] NVARCHAR(50) NULL,
    [SourceTxnId] NVARCHAR(50) NULL,
    [SourceTxnLineId] NVARCHAR(50) NULL
);
END
GO

IF OBJECT_ID('qbo.ReimburseCharge', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboReimburseCharge_QboId_RealmId' AND object_id = OBJECT_ID('qbo.ReimburseCharge'))
BEGIN
CREATE INDEX IX_QboReimburseCharge_QboId_RealmId ON [qbo].[ReimburseCharge] ([QboId], [RealmId]);
END
GO

IF OBJECT_ID('qbo.ReimburseCharge', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboReimburseCharge_RealmId' AND object_id = OBJECT_ID('qbo.ReimburseCharge'))
BEGIN
CREATE INDEX IX_QboReimburseCharge_RealmId ON [qbo].[ReimburseCharge] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.ReimburseCharge', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboReimburseCharge_CustomerRefValue' AND object_id = OBJECT_ID('qbo.ReimburseCharge'))
BEGIN
CREATE INDEX IX_QboReimburseCharge_CustomerRefValue ON [qbo].[ReimburseCharge] ([CustomerRefValue]);
END
GO

IF OBJECT_ID('qbo.ReimburseCharge', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboReimburseCharge_SourceTxnId' AND object_id = OBJECT_ID('qbo.ReimburseCharge'))
BEGIN
CREATE INDEX IX_QboReimburseCharge_SourceTxnId ON [qbo].[ReimburseCharge] ([SourceTxnId]);
END
GO


-- ReimburseCharge Stored Procedures

GO

CREATE OR ALTER PROCEDURE CreateQboReimburseCharge
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @CustomerRefValue NVARCHAR(50),
    @CustomerRefName NVARCHAR(255),
    @TxnDate NVARCHAR(50),
    @Amount DECIMAL(18,2),
    @HasBeenInvoiced BIT,
    @SourceTxnType NVARCHAR(50),
    @SourceTxnId NVARCHAR(50),
    @SourceTxnLineId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[ReimburseCharge] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [RealmId],
        [CustomerRefValue], [CustomerRefName], [TxnDate], [Amount], [HasBeenInvoiced],
        [SourceTxnType], [SourceTxnId], [SourceTxnLineId]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[RealmId],
        INSERTED.[CustomerRefValue],
        INSERTED.[CustomerRefName],
        INSERTED.[TxnDate],
        INSERTED.[Amount],
        INSERTED.[HasBeenInvoiced],
        INSERTED.[SourceTxnType],
        INSERTED.[SourceTxnId],
        INSERTED.[SourceTxnLineId]
    VALUES (
        @Now, @Now, @QboId, @RealmId,
        @CustomerRefValue, @CustomerRefName, @TxnDate, @Amount, @HasBeenInvoiced,
        @SourceTxnType, @SourceTxnId, @SourceTxnLineId
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboReimburseChargeByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50)
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
        [QboId],
        [RealmId],
        [CustomerRefValue],
        [CustomerRefName],
        [TxnDate],
        [Amount],
        [HasBeenInvoiced],
        [SourceTxnType],
        [SourceTxnId],
        [SourceTxnLineId]
    FROM [qbo].[ReimburseCharge]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboReimburseChargesByRealmId
(
    @RealmId NVARCHAR(50)
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
        [QboId],
        [RealmId],
        [CustomerRefValue],
        [CustomerRefName],
        [TxnDate],
        [Amount],
        [HasBeenInvoiced],
        [SourceTxnType],
        [SourceTxnId],
        [SourceTxnLineId]
    FROM [qbo].[ReimburseCharge]
    WHERE [RealmId] = @RealmId
    ORDER BY [TxnDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

-- Invoiced-flip preserve: on the re-pull that flips HasBeenInvoiced=true, QBO no
-- longer returns the RC's reverse LinkedTxn, so the incoming SourceTxn* fields
-- arrive NULL. CASE-WHEN-preserve keeps the pointer captured while un-invoiced.
-- (Same NULL-coalescing UPDATE idiom the other qbo.* staging sprocs use.)
CREATE OR ALTER PROCEDURE UpdateQboReimburseChargeByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @RealmId NVARCHAR(50),
    @CustomerRefValue NVARCHAR(50),
    @CustomerRefName NVARCHAR(255),
    @TxnDate NVARCHAR(50),
    @Amount DECIMAL(18,2),
    @HasBeenInvoiced BIT,
    @SourceTxnType NVARCHAR(50),
    @SourceTxnId NVARCHAR(50),
    @SourceTxnLineId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[ReimburseCharge]
    SET
        [ModifiedDatetime] = @Now,
        [RealmId] = CASE WHEN @RealmId IS NULL THEN [RealmId] ELSE @RealmId END,
        [CustomerRefValue] = CASE WHEN @CustomerRefValue IS NULL THEN [CustomerRefValue] ELSE @CustomerRefValue END,
        [CustomerRefName] = CASE WHEN @CustomerRefName IS NULL THEN [CustomerRefName] ELSE @CustomerRefName END,
        [TxnDate] = CASE WHEN @TxnDate IS NULL THEN [TxnDate] ELSE @TxnDate END,
        [Amount] = CASE WHEN @Amount IS NULL THEN [Amount] ELSE @Amount END,
        [HasBeenInvoiced] = CASE WHEN @HasBeenInvoiced IS NULL THEN [HasBeenInvoiced] ELSE @HasBeenInvoiced END,
        -- Preserve a captured source pointer when the incoming re-pull carries NULL
        -- (the HasBeenInvoiced=true flip drops the RC's reverse LinkedTxn).
        [SourceTxnType] = CASE WHEN @SourceTxnType IS NULL THEN [SourceTxnType] ELSE @SourceTxnType END,
        [SourceTxnId] = CASE WHEN @SourceTxnId IS NULL THEN [SourceTxnId] ELSE @SourceTxnId END,
        [SourceTxnLineId] = CASE WHEN @SourceTxnLineId IS NULL THEN [SourceTxnLineId] ELSE @SourceTxnLineId END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[RealmId],
        INSERTED.[CustomerRefValue],
        INSERTED.[CustomerRefName],
        INSERTED.[TxnDate],
        INSERTED.[Amount],
        INSERTED.[HasBeenInvoiced],
        INSERTED.[SourceTxnType],
        INSERTED.[SourceTxnId],
        INSERTED.[SourceTxnLineId]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
