-- ===========================================================================
-- qbo.ApiUsage — durable per-month QBO API call meter (U-211, Phase 1 of the
-- staging-removal program).
--
-- One row per (RealmId, MonthKey). Every HTTP round-trip to the QBO API
-- increments the current month's counter atomically; the Python breaker
-- (integrations/intuit/qbo/base/budget.py) compares the returned count
-- against the Intuit CorePlus monthly hard cap (Builder tier: 500,000).
--
-- MonthKey is 'YYYY-MM' in UTC — Intuit resets the cap on the 1st of the
-- calendar month. Old months are retained as a usage history (tiny: one
-- row per realm per month).
-- ===========================================================================

IF NOT EXISTS (
    SELECT 1
    FROM sys.tables t
    INNER JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE s.name = 'qbo' AND t.name = 'ApiUsage'
)
BEGIN
    CREATE TABLE [qbo].[ApiUsage]
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [RealmId] NVARCHAR(64) NOT NULL,
        [MonthKey] CHAR(7) NOT NULL,
        [CallCount] BIGINT NOT NULL CONSTRAINT [DF_ApiUsage_CallCount] DEFAULT (0),
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        CONSTRAINT [UQ_ApiUsage_Realm_Month] UNIQUE ([RealmId], [MonthKey])
    );
END
GO

-- Atomic increment-and-read. The UPDATE-first / INSERT-on-miss /
-- re-UPDATE-on-unique-race pattern is safe under concurrent callers
-- (-w 2 API workers + scheduler): exactly one INSERT wins the unique
-- constraint; the loser lands in the 2601/2627 catch and increments.
-- All branches OUTPUT INTO @Out so the sproc always returns exactly ONE
-- result set (the house "single final SELECT" discipline — pyodc callers
-- do a plain fetchone, no result-set walking).
CREATE OR ALTER PROCEDURE IncrementQboApiUsage
    @RealmId NVARCHAR(64),
    @MonthKey CHAR(7)
AS
BEGIN
    SET NOCOUNT ON;
    -- Every QBO call in every process funnels through this single hot row,
    -- and the app connects autocommit=False (lock held until context-exit
    -- commit). A stalled lock holder must ERROR the waiters (error 1222)
    -- rather than queue them unboundedly — the Python meter fails OPEN on
    -- errors, so a bounded wait converts "fleet-wide QBO freeze" into
    -- "one uncounted call + loud log".
    SET LOCK_TIMEOUT 2000;

    DECLARE @Out TABLE ([CallCount] BIGINT);

    UPDATE [qbo].[ApiUsage]
    SET [CallCount] = [CallCount] + 1,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT INSERTED.[CallCount] INTO @Out
    WHERE [RealmId] = @RealmId AND [MonthKey] = @MonthKey;

    IF @@ROWCOUNT = 0
    BEGIN
        BEGIN TRY
            INSERT INTO [qbo].[ApiUsage] ([RealmId], [MonthKey], [CallCount], [CreatedDatetime])
            OUTPUT INSERTED.[CallCount] INTO @Out
            VALUES (@RealmId, @MonthKey, 1, SYSUTCDATETIME());
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() IN (2601, 2627)
            BEGIN
                UPDATE [qbo].[ApiUsage]
                SET [CallCount] = [CallCount] + 1,
                    [ModifiedDatetime] = SYSUTCDATETIME()
                OUTPUT INSERTED.[CallCount] INTO @Out
                WHERE [RealmId] = @RealmId AND [MonthKey] = @MonthKey;
            END
            ELSE
                THROW;
        END CATCH
    END

    SELECT TOP 1 [CallCount] FROM @Out;
END
GO

-- Read-only view of a month's usage across realms (breaker status checks,
-- future status endpoint). No increment.
CREATE OR ALTER PROCEDURE ReadQboApiUsageByMonth
    @MonthKey CHAR(7)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT [RealmId], [MonthKey], [CallCount], [CreatedDatetime], [ModifiedDatetime]
    FROM [qbo].[ApiUsage]
    WHERE [MonthKey] = @MonthKey;
END
GO
