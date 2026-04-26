-- Per-file work items for Process Folder runs. The Function App claims
-- one at a time via ClaimNextBillFolderRunItem and processes it via the
-- API's /admin/bill-folder/tick endpoint. No single HTTP call ever runs
-- more than one file's worth of work, so idle timeouts + per-file errors
-- stop affecting the whole run.
--
-- Run: python scripts/run_sql.py entities/bill/sql/dbo.billfolderrunitem.sql

GO

IF OBJECT_ID('dbo.BillFolderRunItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillFolderRunItem]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [RunId] BIGINT NOT NULL,
    [Filename] NVARCHAR(500) NOT NULL,
    [ItemId] NVARCHAR(200) NOT NULL,
    [Status] NVARCHAR(20) NOT NULL,         -- queued | processing | completed | skipped | failed
    [Attempts] INT NOT NULL DEFAULT 0,
    [LastError] NVARCHAR(MAX) NULL,
    [ClaimedAt] DATETIME2(3) NULL,
    [Result] NVARCHAR(MAX) NULL,             -- JSON: { bill_public_id, skip_reason, ... }
    [StartedAt] DATETIME2(3) NULL,
    [CompletedAt] DATETIME2(3) NULL,
    CONSTRAINT [FK_BillFolderRunItem_Run] FOREIGN KEY ([RunId]) REFERENCES [dbo].[BillFolderRun]([Id])
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillFolderRunItem_Status_ClaimedAt')
BEGIN
    CREATE INDEX IX_BillFolderRunItem_Status_ClaimedAt ON [dbo].[BillFolderRunItem]([Status], [ClaimedAt]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillFolderRunItem_RunId_Status')
BEGIN
    CREATE INDEX IX_BillFolderRunItem_RunId_Status ON [dbo].[BillFolderRunItem]([RunId], [Status]);
END
GO


CREATE OR ALTER PROCEDURE CreateBillFolderRunItem
(
    @RunId BIGINT,
    @Filename NVARCHAR(500),
    @ItemId NVARCHAR(200)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillFolderRunItem]
        ([CreatedDatetime], [ModifiedDatetime], [RunId], [Filename], [ItemId], [Status], [Attempts])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RunId],
        INSERTED.[Filename],
        INSERTED.[ItemId],
        INSERTED.[Status],
        INSERTED.[Attempts]
    VALUES (@Now, @Now, @RunId, @Filename, @ItemId, 'queued', 0);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ClaimNextBillFolderRunItem
(
    @ReclaimAfterSeconds INT = 180,
    @MaxAttempts INT = 3
)
AS
BEGIN
    -- SET NOCOUNT ON suppresses UPDATE rowcount messages so pyodbc sees only
    -- the SELECT below as the result set. Without this AND a guaranteed
    -- terminal SELECT, claim_next() crashes with "No results. Previous SQL
    -- was not a query." every time the queue is empty.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @ClaimedId BIGINT;

    ;WITH candidate AS (
        SELECT TOP 1 [Id]
        FROM dbo.[BillFolderRunItem] WITH (UPDLOCK, READPAST, ROWLOCK)
        WHERE [Attempts] < @MaxAttempts
          AND (
              [Status] = 'queued'
              OR (
                  [Status] = 'processing'
                  AND [ClaimedAt] IS NOT NULL
                  AND DATEDIFF(SECOND, [ClaimedAt], @Now) > @ReclaimAfterSeconds
              )
          )
        ORDER BY [Id]
    )
    UPDATE i
    SET
        [Status] = 'processing',
        [ClaimedAt] = @Now,
        [StartedAt] = ISNULL(i.[StartedAt], @Now),
        [Attempts] = i.[Attempts] + 1,
        [ModifiedDatetime] = @Now,
        @ClaimedId = i.[Id]
    FROM dbo.[BillFolderRunItem] i
    INNER JOIN candidate c ON i.[Id] = c.[Id];

    -- Always emit a result set so the caller can fetchone() unconditionally.
    -- WHERE Id = @ClaimedId returns 0 rows when nothing was claimed.
    SELECT
        [Id],
        [PublicId],
        [RunId],
        [Filename],
        [ItemId],
        [Status],
        [Attempts],
        [ClaimedAt],
        [StartedAt]
    FROM dbo.[BillFolderRunItem]
    WHERE [Id] = @ClaimedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateBillFolderRunItemOnSuccess
(
    @PublicId UNIQUEIDENTIFIER,
    @Status NVARCHAR(20),                 -- 'completed' or 'skipped'
    @Result NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[BillFolderRunItem]
    SET
        [Status] = @Status,
        [Result] = @Result,
        [LastError] = NULL,
        [CompletedAt] = @Now,
        [ModifiedDatetime] = @Now
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateBillFolderRunItemOnFailure
(
    @PublicId UNIQUEIDENTIFIER,
    @LastError NVARCHAR(MAX),
    @MaxAttempts INT = 3
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Attempts INT;

    SELECT @Attempts = [Attempts]
    FROM dbo.[BillFolderRunItem]
    WHERE [PublicId] = @PublicId;

    IF @Attempts IS NULL
    BEGIN
        COMMIT TRANSACTION;
        RETURN;
    END

    IF @Attempts >= @MaxAttempts
    BEGIN
        -- Permanent failure after max retries.
        UPDATE dbo.[BillFolderRunItem]
        SET
            [Status] = 'failed',
            [LastError] = @LastError,
            [ClaimedAt] = NULL,
            [CompletedAt] = @Now,
            [ModifiedDatetime] = @Now
        WHERE [PublicId] = @PublicId;
    END
    ELSE
    BEGIN
        -- Return to queue for another attempt.
        UPDATE dbo.[BillFolderRunItem]
        SET
            [Status] = 'queued',
            [LastError] = @LastError,
            [ClaimedAt] = NULL,
            [ModifiedDatetime] = @Now
        WHERE [PublicId] = @PublicId;
    END

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE CheckAndCompleteBillFolderRun
(
    @RunId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @OpenItems INT;
    SELECT @OpenItems = COUNT(*)
    FROM dbo.[BillFolderRunItem]
    WHERE [RunId] = @RunId
      AND [Status] IN ('queued', 'processing');

    IF @OpenItems = 0
    BEGIN
        UPDATE dbo.[BillFolderRun]
        SET
            [Status] = 'completed',
            [CompletedAt] = SYSUTCDATETIME(),
            [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] = @RunId AND [Status] <> 'completed' AND [Status] <> 'failed';
    END

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadBillFolderRunAggregateByPublicId
(
    @RunPublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        r.[Id] AS [RunId],
        r.[PublicId] AS [RunPublicId],
        r.[Status] AS [RunStatus],
        CONVERT(VARCHAR(19), r.[StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), r.[CompletedAt], 120) AS [CompletedAt],
        COUNT(i.[Id]) AS [FilesTotal],
        SUM(CASE WHEN i.[Status] = 'queued'     THEN 1 ELSE 0 END) AS [FilesQueued],
        SUM(CASE WHEN i.[Status] = 'processing' THEN 1 ELSE 0 END) AS [FilesProcessing],
        SUM(CASE WHEN i.[Status] = 'completed'  THEN 1 ELSE 0 END) AS [FilesProcessed],
        SUM(CASE WHEN i.[Status] = 'skipped'    THEN 1 ELSE 0 END) AS [FilesSkipped],
        SUM(CASE WHEN i.[Status] = 'failed'     THEN 1 ELSE 0 END) AS [FilesFailed],
        (
            SELECT TOP 1 [Filename]
            FROM dbo.[BillFolderRunItem]
            WHERE [RunId] = r.[Id] AND [Status] = 'processing'
            ORDER BY [ClaimedAt] DESC
        ) AS [CurrentFile]
    FROM dbo.[BillFolderRun] r
    LEFT JOIN dbo.[BillFolderRunItem] i ON i.[RunId] = r.[Id]
    WHERE r.[PublicId] = @RunPublicId
    GROUP BY r.[Id], r.[PublicId], r.[Status], r.[StartedAt], r.[CompletedAt];

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadBillFolderRunItemErrorsByRunPublicId
(
    @RunPublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        i.[Filename],
        i.[Status],
        i.[LastError],
        i.[Attempts]
    FROM dbo.[BillFolderRunItem] i
    INNER JOIN dbo.[BillFolderRun] r ON i.[RunId] = r.[Id]
    WHERE r.[PublicId] = @RunPublicId
      AND i.[Status] IN ('failed', 'skipped')
    ORDER BY i.[Id];

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadActiveBillFolderRunItemIds
(
    @RecentWindowMinutes INT = 60
)
AS
BEGIN
    -- Returns the SharePoint ItemId of every BillFolderRunItem the
    -- enumerator should skip:
    --   * 'queued' / 'processing' — currently in flight.
    --   * Anything modified inside the recent window — already attempted
    --     (success/skip/fail). Without this guard, files that keep
    --     failing the SharePoint move would be re-enqueued every 5-min
    --     scheduler tick and create a flood of failed-item rows.
    -- After the window expires the file gets another shot, so an
    -- operator who fixes the underlying SP issue doesn't have to
    -- manually trigger anything.
    SET NOCOUNT ON;

    DECLARE @Cutoff DATETIME2(3) = DATEADD(MINUTE, -@RecentWindowMinutes, SYSUTCDATETIME());

    SELECT DISTINCT [ItemId]
    FROM dbo.[BillFolderRunItem]
    WHERE [Status] IN ('queued', 'processing')
       OR [ModifiedDatetime] > @Cutoff;
END;
GO


CREATE OR ALTER PROCEDURE AutoFailStaleBillFolderRuns
(
    @StaleAfterMinutes INT = 30
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Cutoff DATETIME2(3) = DATEADD(MINUTE, -@StaleAfterMinutes, SYSUTCDATETIME());
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- Stale items first (claimed-but-abandoned over the cutoff).
    UPDATE i
    SET
        i.[Status] = 'failed',
        i.[LastError] = 'Item stuck — claimed but never finished',
        i.[CompletedAt] = @Now,
        i.[ModifiedDatetime] = @Now
    FROM dbo.[BillFolderRunItem] i
    INNER JOIN dbo.[BillFolderRun] r ON i.[RunId] = r.[Id]
    WHERE r.[Status] IN ('processing', 'queued')
      AND i.[Status] = 'processing'
      AND i.[ClaimedAt] IS NOT NULL
      AND i.[ClaimedAt] < @Cutoff;

    -- Stale runs (no item activity for the whole window).
    UPDATE r
    SET
        r.[Status] = 'failed',
        r.[CompletedAt] = @Now,
        r.[ModifiedDatetime] = @Now,
        r.[Result] = CONCAT('{"error":"Run timed out after ', @StaleAfterMinutes, ' minutes of inactivity"}')
    FROM dbo.[BillFolderRun] r
    WHERE r.[Status] IN ('processing', 'queued')
      AND NOT EXISTS (
          SELECT 1
          FROM dbo.[BillFolderRunItem] i
          WHERE i.[RunId] = r.[Id]
            AND i.[ModifiedDatetime] > @Cutoff
      )
      AND r.[StartedAt] < @Cutoff;

    COMMIT TRANSACTION;
END;
GO
