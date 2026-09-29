-- RampTransactionFollowUp — persisted delinquency state for U-549 chaser.
-- Run: ./.venv/bin/python scripts/run_sql.py entities/ramp_transaction_follow_up/sql/dbo.ramp_transaction_follow_up.sql

GO

IF OBJECT_ID('dbo.RampTransactionFollowUp', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[RampTransactionFollowUp]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RampTransactionId] NVARCHAR(64) NOT NULL,
    [CardHolderRampUserId] NVARCHAR(64) NULL,
    [CardHolderName] NVARCHAR(256) NULL,
    [MerchantName] NVARCHAR(512) NULL,
    [Amount] DECIMAL(19,4) NULL,
    [TransactionDate] NVARCHAR(40) NULL,
    [NeedsMemo] BIT NOT NULL DEFAULT 0,
    [NeedsReceipt] BIT NOT NULL DEFAULT 0,
    [FirstSeenAt] DATETIME2(3) NOT NULL,
    [LastDraftedAt] DATETIME2(3) NULL,
    [DraftMessageId] NVARCHAR(256) NULL,
    [LastNotifiedAt] DATETIME2(3) NULL,
    [NotifyCount] INT NOT NULL DEFAULT 0,
    [EscalatedAt] DATETIME2(3) NULL,
    [ResolvedAt] DATETIME2(3) NULL,
    [GoneFromRampCount] INT NOT NULL DEFAULT 0,
    [GoneFromRampAt] DATETIME2(3) NULL,
    [CreatedAt] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    [UpdatedAt] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    [RowVersion] ROWVERSION NOT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_RampTransactionFollowUp_PublicId' AND object_id = OBJECT_ID('dbo.RampTransactionFollowUp'))
BEGIN
    CREATE UNIQUE INDEX UQ_RampTransactionFollowUp_PublicId
        ON dbo.[RampTransactionFollowUp] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_RampTransactionFollowUp_RampTransactionId' AND object_id = OBJECT_ID('dbo.RampTransactionFollowUp'))
BEGIN
    CREATE UNIQUE INDEX UQ_RampTransactionFollowUp_RampTransactionId
        ON dbo.[RampTransactionFollowUp] ([RampTransactionId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_RampTransactionFollowUp_ResolvedAt' AND object_id = OBJECT_ID('dbo.RampTransactionFollowUp'))
BEGIN
    CREATE INDEX IX_RampTransactionFollowUp_ResolvedAt
        ON dbo.[RampTransactionFollowUp] ([ResolvedAt]);
END
GO

-- U-573: consecutive-miss counter + terminal "gone from Ramp" stamp. A row whose
-- Ramp transaction 404s GONE_FROM_RAMP_MISS_THRESHOLD sweeps IN A ROW leaves the
-- refetch set (the *ids* sproc below); it stays an OPEN follow-up item and is still
-- returned by the full-row read that feeds the digest.
IF COL_LENGTH('dbo.RampTransactionFollowUp', 'GoneFromRampCount') IS NULL
BEGIN
    ALTER TABLE dbo.[RampTransactionFollowUp] ADD [GoneFromRampCount] INT NOT NULL DEFAULT 0;
END
GO

IF COL_LENGTH('dbo.RampTransactionFollowUp', 'GoneFromRampAt') IS NULL
BEGIN
    ALTER TABLE dbo.[RampTransactionFollowUp] ADD [GoneFromRampAt] DATETIME2(3) NULL;
END
GO


CREATE OR ALTER PROCEDURE UpsertRampTransactionFollowUp
(
    @RampTransactionId NVARCHAR(64),
    @CardHolderRampUserId NVARCHAR(64) = NULL,
    @CardHolderName NVARCHAR(256) = NULL,
    @MerchantName NVARCHAR(512) = NULL,
    @Amount DECIMAL(19,4) = NULL,
    @TransactionDate NVARCHAR(40) = NULL,
    @NeedsMemo BIT = 0,
    @NeedsReceipt BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[RampTransactionFollowUp] WITH (HOLDLOCK) AS target
    USING (
        SELECT @RampTransactionId AS RampTransactionId
    ) AS source
    ON target.[RampTransactionId] = source.RampTransactionId
    WHEN MATCHED AND target.[ResolvedAt] IS NULL THEN
        UPDATE SET
            [CardHolderRampUserId] = CASE WHEN @CardHolderRampUserId IS NOT NULL THEN @CardHolderRampUserId ELSE target.[CardHolderRampUserId] END,
            [CardHolderName] = CASE WHEN @CardHolderName IS NOT NULL THEN @CardHolderName ELSE target.[CardHolderName] END,
            [MerchantName] = CASE WHEN @MerchantName IS NOT NULL THEN @MerchantName ELSE target.[MerchantName] END,
            [Amount] = CASE WHEN @Amount IS NOT NULL THEN @Amount ELSE target.[Amount] END,
            [TransactionDate] = CASE WHEN @TransactionDate IS NOT NULL THEN @TransactionDate ELSE target.[TransactionDate] END,
            [NeedsMemo] = @NeedsMemo,
            [NeedsReceipt] = @NeedsReceipt,
            [UpdatedAt] = @Now
    WHEN NOT MATCHED THEN
        INSERT
            ([RampTransactionId], [CardHolderRampUserId], [CardHolderName],
             [MerchantName], [Amount], [TransactionDate], [NeedsMemo], [NeedsReceipt],
             [FirstSeenAt], [CreatedAt], [UpdatedAt])
        VALUES
            (@RampTransactionId, @CardHolderRampUserId, @CardHolderName,
             @MerchantName, @Amount, @TransactionDate, @NeedsMemo, @NeedsReceipt,
             @Now, @Now, @Now)
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[RampTransactionId],
        INSERTED.[CardHolderRampUserId],
        INSERTED.[CardHolderName],
        INSERTED.[MerchantName],
        INSERTED.[Amount],
        INSERTED.[TransactionDate],
        INSERTED.[NeedsMemo],
        INSERTED.[NeedsReceipt],
        CONVERT(VARCHAR(30), INSERTED.[FirstSeenAt], 126) AS [FirstSeenAt],
        CONVERT(VARCHAR(30), INSERTED.[LastDraftedAt], 126) AS [LastDraftedAt],
        INSERTED.[DraftMessageId],
        CONVERT(VARCHAR(30), INSERTED.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        INSERTED.[NotifyCount],
        CONVERT(VARCHAR(30), INSERTED.[EscalatedAt], 126) AS [EscalatedAt],
        CONVERT(VARCHAR(30), INSERTED.[ResolvedAt], 126) AS [ResolvedAt],
        INSERTED.[GoneFromRampCount],
        CONVERT(VARCHAR(30), INSERTED.[GoneFromRampAt], 126) AS [GoneFromRampAt],
        CONVERT(VARCHAR(30), INSERTED.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), INSERTED.[UpdatedAt], 126) AS [UpdatedAt];

    COMMIT TRANSACTION;
END
GO


CREATE OR ALTER PROCEDURE MarkRampTransactionFollowUpResolved
(
    @RampTransactionId NVARCHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[RampTransactionFollowUp]
    SET [ResolvedAt] = @Now,
        [UpdatedAt] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[RampTransactionId],
        INSERTED.[CardHolderRampUserId],
        INSERTED.[CardHolderName],
        INSERTED.[MerchantName],
        INSERTED.[Amount],
        INSERTED.[TransactionDate],
        INSERTED.[NeedsMemo],
        INSERTED.[NeedsReceipt],
        CONVERT(VARCHAR(30), INSERTED.[FirstSeenAt], 126) AS [FirstSeenAt],
        CONVERT(VARCHAR(30), INSERTED.[LastDraftedAt], 126) AS [LastDraftedAt],
        INSERTED.[DraftMessageId],
        CONVERT(VARCHAR(30), INSERTED.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        INSERTED.[NotifyCount],
        CONVERT(VARCHAR(30), INSERTED.[EscalatedAt], 126) AS [EscalatedAt],
        CONVERT(VARCHAR(30), INSERTED.[ResolvedAt], 126) AS [ResolvedAt],
        INSERTED.[GoneFromRampCount],
        CONVERT(VARCHAR(30), INSERTED.[GoneFromRampAt], 126) AS [GoneFromRampAt],
        CONVERT(VARCHAR(30), INSERTED.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), INSERTED.[UpdatedAt], 126) AS [UpdatedAt]
    WHERE [RampTransactionId] = @RampTransactionId
      AND [ResolvedAt] IS NULL;

    COMMIT TRANSACTION;
END
GO


CREATE OR ALTER PROCEDURE ReadUnresolvedRampTransactionFollowUpIds
AS
BEGIN
    SET NOCOUNT ON;

    -- U-573: retired rows (Ramp has 404'd them GoneFromRampCount sweeps in a row)
    -- leave the REFETCH set only. They are still open items and still appear in
    -- ReadUnresolvedRampTransactionFollowUps below, which feeds the digest.
    SELECT [RampTransactionId]
    FROM dbo.[RampTransactionFollowUp]
    WHERE [ResolvedAt] IS NULL
      AND [GoneFromRampAt] IS NULL;
END
GO


CREATE OR ALTER PROCEDURE ReadUnresolvedRampTransactionFollowUps
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        r.[Id],
        r.[PublicId],
        r.[RowVersion],
        r.[RampTransactionId],
        r.[CardHolderRampUserId],
        r.[CardHolderName],
        r.[MerchantName],
        r.[Amount],
        r.[TransactionDate],
        r.[NeedsMemo],
        r.[NeedsReceipt],
        CONVERT(VARCHAR(30), r.[FirstSeenAt], 126) AS [FirstSeenAt],
        CONVERT(VARCHAR(30), r.[LastDraftedAt], 126) AS [LastDraftedAt],
        r.[DraftMessageId],
        CONVERT(VARCHAR(30), r.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        r.[NotifyCount],
        CONVERT(VARCHAR(30), r.[EscalatedAt], 126) AS [EscalatedAt],
        CONVERT(VARCHAR(30), r.[ResolvedAt], 126) AS [ResolvedAt],
        r.[GoneFromRampCount],
        CONVERT(VARCHAR(30), r.[GoneFromRampAt], 126) AS [GoneFromRampAt],
        CONVERT(VARCHAR(30), r.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), r.[UpdatedAt], 126) AS [UpdatedAt]
    FROM dbo.[RampTransactionFollowUp] r
    -- U-573: deliberately NOT filtered on [GoneFromRampAt]. A retired row is still
    -- an OPEN follow-up item and must still reach the cardholder's digest; we only
    -- stopped asking Ramp about it. Filtering here silently drops someone's items.
    WHERE r.[ResolvedAt] IS NULL
    ORDER BY r.[CardHolderRampUserId], r.[FirstSeenAt];
END
GO


CREATE OR ALTER PROCEDURE RecordRampTransactionFollowUpGoneFromRamp
(
    @RampTransactionId NVARCHAR(64),
    @Threshold INT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- T-SQL evaluates every SET right-hand side against the PRE-update row, so the
    -- CASE below reads the old count; `old + 1 >= @Threshold` is the crossing test.
    -- The [GoneFromRampAt] IS NULL predicate makes the stamp fire exactly once, so
    -- a returned row means "crossed the threshold on THIS call".
    UPDATE dbo.[RampTransactionFollowUp]
    SET [GoneFromRampCount] = [GoneFromRampCount] + 1,
        [GoneFromRampAt] = CASE
            WHEN [GoneFromRampCount] + 1 >= @Threshold THEN @Now
            ELSE NULL
        END,
        [UpdatedAt] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[RampTransactionId],
        INSERTED.[CardHolderRampUserId],
        INSERTED.[CardHolderName],
        INSERTED.[MerchantName],
        INSERTED.[Amount],
        INSERTED.[TransactionDate],
        INSERTED.[NeedsMemo],
        INSERTED.[NeedsReceipt],
        CONVERT(VARCHAR(30), INSERTED.[FirstSeenAt], 126) AS [FirstSeenAt],
        CONVERT(VARCHAR(30), INSERTED.[LastDraftedAt], 126) AS [LastDraftedAt],
        INSERTED.[DraftMessageId],
        CONVERT(VARCHAR(30), INSERTED.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        INSERTED.[NotifyCount],
        CONVERT(VARCHAR(30), INSERTED.[EscalatedAt], 126) AS [EscalatedAt],
        CONVERT(VARCHAR(30), INSERTED.[ResolvedAt], 126) AS [ResolvedAt],
        INSERTED.[GoneFromRampCount],
        CONVERT(VARCHAR(30), INSERTED.[GoneFromRampAt], 126) AS [GoneFromRampAt],
        CONVERT(VARCHAR(30), INSERTED.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), INSERTED.[UpdatedAt], 126) AS [UpdatedAt]
    WHERE [RampTransactionId] = @RampTransactionId
      AND [ResolvedAt] IS NULL
      AND [GoneFromRampAt] IS NULL;

    COMMIT TRANSACTION;
END
GO


CREATE OR ALTER PROCEDURE ResetRampTransactionFollowUpGoneFromRampCount
(
    @RampTransactionId NVARCHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- CONSECUTIVE is the point: any successful fetch zeroes the run, so a transient
    -- Ramp 404 can never retire a live row. The GoneFromRampCount <> 0 predicate
    -- keeps the common case (nothing to reset) from churning UpdatedAt/RowVersion
    -- on every straggler on every sweep.
    UPDATE dbo.[RampTransactionFollowUp]
    SET [GoneFromRampCount] = 0,
        [UpdatedAt] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[RampTransactionId],
        INSERTED.[CardHolderRampUserId],
        INSERTED.[CardHolderName],
        INSERTED.[MerchantName],
        INSERTED.[Amount],
        INSERTED.[TransactionDate],
        INSERTED.[NeedsMemo],
        INSERTED.[NeedsReceipt],
        CONVERT(VARCHAR(30), INSERTED.[FirstSeenAt], 126) AS [FirstSeenAt],
        CONVERT(VARCHAR(30), INSERTED.[LastDraftedAt], 126) AS [LastDraftedAt],
        INSERTED.[DraftMessageId],
        CONVERT(VARCHAR(30), INSERTED.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        INSERTED.[NotifyCount],
        CONVERT(VARCHAR(30), INSERTED.[EscalatedAt], 126) AS [EscalatedAt],
        CONVERT(VARCHAR(30), INSERTED.[ResolvedAt], 126) AS [ResolvedAt],
        INSERTED.[GoneFromRampCount],
        CONVERT(VARCHAR(30), INSERTED.[GoneFromRampAt], 126) AS [GoneFromRampAt],
        CONVERT(VARCHAR(30), INSERTED.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), INSERTED.[UpdatedAt], 126) AS [UpdatedAt]
    WHERE [RampTransactionId] = @RampTransactionId
      AND [ResolvedAt] IS NULL
      AND [GoneFromRampCount] <> 0;

    COMMIT TRANSACTION;
END
GO
