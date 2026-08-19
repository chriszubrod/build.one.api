GO

-- =============================================================================
-- qbo.ReimburseCharge — durable staging for QBO ReimburseCharge records (U-186).
--
-- QBO auto-creates a ReimburseCharge (RC) for every Bill/Purchase line marked
-- Billable with a CustomerRef. Captured on scheduler cadence for invoice-line
-- linking (LinkedTxn matching against qbo.InvoiceLine.LinkedTxnId).
--
-- SourceTxnType/SourceTxnId/SourceTxnLineId (a reverse Bill/Purchase pointer)
-- were retired (U-280): measured 2026-08-16 (U-242) QBO never populates them at
-- any lifecycle stage (100% NULL across all live rows, re-confirmed 2026-08-19);
-- the one sproc that would have read them (a Tier-0 arm in
-- ProposeInvoiceSourceLinks) was already removed as provably dead by U-244. See
-- docs/rc_source_linking_signal_2026_08_16.md.
--
-- KEYSPACE: QboId is a QBO STRING id (qbo.*.Id BIGINT keyspace is disjoint —
-- never conflate). Pull-only staging: no delete / reconcile-delete sproc.
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
    [HasBeenInvoiced] BIT NULL
);
END
GO

-- Drop redundant non-unique composite before adding the unique index on the same pair.
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboReimburseCharge_QboId_RealmId' AND object_id = OBJECT_ID('qbo.ReimburseCharge'))
BEGIN
    DROP INDEX IX_QboReimburseCharge_QboId_RealmId ON [qbo].[ReimburseCharge];
END
GO

IF OBJECT_ID('qbo.ReimburseCharge', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes i
    WHERE i.name = 'UQ_QboReimburseCharge_QboId_RealmId'
      AND i.object_id = OBJECT_ID('qbo.ReimburseCharge')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.ignore_dup_key = 0
      AND (
          SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
                 WITHIN GROUP (ORDER BY ic.key_ordinal)
          FROM sys.index_columns ic
          WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
      ) = N'QboId,RealmId'
)
BEGIN
CREATE UNIQUE INDEX UQ_QboReimburseCharge_QboId_RealmId ON [qbo].[ReimburseCharge] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
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

-- U-280: retire the dead SourceTxn* identity columns (100% NULL, no live reader
-- — see the file header). Index first (a column can't be dropped while an
-- index depends on it), then the columns. Both guards are idempotent so a
-- re-run against an already-migrated database (or a fresh CREATE TABLE above,
-- which no longer declares these columns) is a no-op.
IF OBJECT_ID('qbo.ReimburseCharge', 'U') IS NOT NULL AND EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboReimburseCharge_SourceTxnId' AND object_id = OBJECT_ID('qbo.ReimburseCharge'))
BEGIN
    DROP INDEX IX_QboReimburseCharge_SourceTxnId ON [qbo].[ReimburseCharge];
END
GO

IF OBJECT_ID('qbo.ReimburseCharge', 'U') IS NOT NULL AND EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('qbo.ReimburseCharge') AND name = 'SourceTxnType')
BEGIN
    ALTER TABLE [qbo].[ReimburseCharge] DROP COLUMN [SourceTxnType], [SourceTxnId], [SourceTxnLineId];
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
    @HasBeenInvoiced BIT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[ReimburseCharge] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [RealmId],
        [CustomerRefValue], [CustomerRefName], [TxnDate], [Amount], [HasBeenInvoiced]
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
        INSERTED.[HasBeenInvoiced]
    VALUES (
        @Now, @Now, @QboId, @RealmId,
        @CustomerRefValue, @CustomerRefName, @TxnDate, @Amount, @HasBeenInvoiced
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
        [HasBeenInvoiced]
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
        [HasBeenInvoiced]
    FROM [qbo].[ReimburseCharge]
    WHERE [RealmId] = @RealmId
    ORDER BY [TxnDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboReimburseChargeByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @RealmId NVARCHAR(50),
    @CustomerRefValue NVARCHAR(50),
    @CustomerRefName NVARCHAR(255),
    @TxnDate NVARCHAR(50),
    @Amount DECIMAL(18,2),
    @HasBeenInvoiced BIT
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
        [HasBeenInvoiced] = CASE WHEN @HasBeenInvoiced IS NULL THEN [HasBeenInvoiced] ELSE @HasBeenInvoiced END
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
        INSERTED.[HasBeenInvoiced]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
