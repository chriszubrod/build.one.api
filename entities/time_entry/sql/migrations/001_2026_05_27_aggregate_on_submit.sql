-- =============================================================================
-- 2026-05-27 — Phase 4: TimeEntry → ContractLabor / EmployeeLabor aggregation.
--
-- AggregateTimeEntryOnSubmit(@TimeEntryId)
--
--   Fires when a TimeEntry transitions draft → submitted. Branches on
--   User.EmployeeId vs User.VendorId to route into the right downstream
--   labor table:
--     - User.EmployeeId set → dbo.EmployeeLabor (no Bill — flows to Invoice)
--     - User.VendorId   set → dbo.ContractLabor (Bill generation as today)
--
--   Per decision #2: aggregation grain = (Worker × Project × Day). Multiple
--   TimeLogs same (worker, project, day) collapse via SUM(Duration).
--   Multiple TimeLogs same worker different projects same day produce
--   multiple rows (one per project).
--
--   Per decision #3: billing period is semi-monthly. WorkDate day ≤ 15 →
--   period is 1st–15th of the same month. WorkDate day ≥ 16 → period is
--   16th–EOMONTH(WorkDate).
--
--   Per decision #4: rate lock at bill/invoice generation. This sproc
--   captures rate at aggregation time as a snapshot; if rates change later
--   the aggregation can be re-run idempotently to pick up new values.
--
--   Per decision #5: idempotent upsert on natural key. Re-submitting after
--   reject→edit overwrites the existing aggregated row in place.
--
--   Rate-source 'none' (Worker has no default rate AND no per-project
--   override): row still upserts in 'pending_review' with NULL rate +
--   Description annotation so the office sees it on the bills page.
--
--   Returns: one row per affected (Project, WorkDate) tuple:
--     { TargetTable, TargetRowId, ProjectId, WorkDate, TotalHours,
--       HourlyRate, Markup, RateSource, Status, Note }
--
--   Errors raised (callers handle):
--     - TimeEntry not found
--     - User has neither EmployeeId nor VendorId (worker not configured)
--     - User has BOTH (XOR violated — defense-in-depth)
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


CREATE OR ALTER PROCEDURE dbo.AggregateTimeEntryOnSubmit
(
    @TimeEntryId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @UserId       BIGINT;
    DECLARE @WorkDate     DATE;
    DECLARE @EmployeeId   BIGINT;
    DECLARE @VendorId     BIGINT;
    DECLARE @WorkerName   NVARCHAR(310);

    SELECT @UserId = [UserId], @WorkDate = [WorkDate]
    FROM dbo.[TimeEntry]
    WHERE [Id] = @TimeEntryId;

    IF @UserId IS NULL
    BEGIN
        RAISERROR('TimeEntry %d not found.', 16, 1, @TimeEntryId);
        RETURN;
    END

    SELECT
        @EmployeeId = [EmployeeId],
        @VendorId   = [VendorId],
        @WorkerName = ISNULL([Firstname], '') + ' ' + ISNULL([Lastname], '')
    FROM dbo.[User]
    WHERE [Id] = @UserId;

    IF @EmployeeId IS NOT NULL AND @VendorId IS NOT NULL
    BEGIN
        RAISERROR('User %d has both EmployeeId and VendorId set (XOR violated).', 16, 1, @UserId);
        RETURN;
    END

    IF @EmployeeId IS NULL AND @VendorId IS NULL
    BEGIN
        RAISERROR(
            'User %d has no worker linkage (User.EmployeeId and VendorId both NULL). Set one via UserProfile before submitting TimeEntries for billing.',
            16, 1, @UserId
        );
        RETURN;
    END

    -- Billing period — semi-monthly, hardcoded per decision #3.
    DECLARE @BillingPeriodStart DATE;
    DECLARE @BillingPeriodEnd   DATE;
    IF DAY(@WorkDate) <= 15
    BEGIN
        SET @BillingPeriodStart = DATEFROMPARTS(YEAR(@WorkDate), MONTH(@WorkDate), 1);
        SET @BillingPeriodEnd   = DATEFROMPARTS(YEAR(@WorkDate), MONTH(@WorkDate), 15);
    END
    ELSE
    BEGIN
        SET @BillingPeriodStart = DATEFROMPARTS(YEAR(@WorkDate), MONTH(@WorkDate), 16);
        SET @BillingPeriodEnd   = EOMONTH(@WorkDate);
    END

    -- Group TimeLogs by ProjectId (NULL allowed — "no project" rolls up too)
    -- and sum Duration. LogType filter — only 'work' counts; 'break' excluded.
    DECLARE @Buckets TABLE (
        ProjectId    BIGINT NULL,
        TotalHours   DECIMAL(6,2) NOT NULL
    );

    INSERT INTO @Buckets (ProjectId, TotalHours)
    SELECT
        tl.[ProjectId],
        SUM(ISNULL(tl.[Duration], 0))
    FROM dbo.[TimeLog] tl
    WHERE tl.[TimeEntryId] = @TimeEntryId
      AND (tl.[LogType] IS NULL OR tl.[LogType] = 'work')
    GROUP BY tl.[ProjectId];

    -- Result rows (for diagnostics + caller logging)
    DECLARE @Results TABLE (
        TargetTable  NVARCHAR(30)  NOT NULL,
        TargetRowId  BIGINT        NULL,
        ProjectId    BIGINT        NULL,
        WorkDate     DATE          NOT NULL,
        TotalHours   DECIMAL(6,2)  NOT NULL,
        HourlyRate   DECIMAL(18,4) NULL,
        Markup       DECIMAL(18,4) NULL,
        RateSource   NVARCHAR(20)  NULL,
        Status       NVARCHAR(20)  NOT NULL,
        Note         NVARCHAR(255) NULL
    );

    DECLARE @ProjectId   BIGINT;
    DECLARE @TotalHours  DECIMAL(6,2);

    DECLARE bucket_cur CURSOR LOCAL FAST_FORWARD FOR
        SELECT ProjectId, TotalHours FROM @Buckets;

    OPEN bucket_cur;
    FETCH NEXT FROM bucket_cur INTO @ProjectId, @TotalHours;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        DECLARE @HourlyRate DECIMAL(18,4) = NULL;
        DECLARE @Markup     DECIMAL(18,4) = NULL;
        DECLARE @RateSource NVARCHAR(20)  = 'none';
        DECLARE @TotalAmount DECIMAL(18,2) = NULL;
        DECLARE @Status     NVARCHAR(20)  = 'pending_review';
        DECLARE @Description NVARCHAR(MAX) = NULL;
        DECLARE @Note       NVARCHAR(255) = NULL;

        -- Resolve effective rate. Both lookup sprocs handle NULL project the
        -- same way: COALESCE override → default; if both NULL, returns 'none'.
        IF @EmployeeId IS NOT NULL
        BEGIN
            DECLARE @RateE TABLE (HourlyRate DECIMAL(18,4) NULL, Markup DECIMAL(18,4) NULL, RateSource NVARCHAR(20) NULL);
            INSERT INTO @RateE
            EXEC dbo.ReadEffectiveRateForEmployeeProject @EmployeeId = @EmployeeId, @ProjectId = @ProjectId;
            SELECT TOP 1 @HourlyRate = HourlyRate, @Markup = Markup, @RateSource = RateSource FROM @RateE;
            DELETE FROM @RateE;
        END
        ELSE
        BEGIN
            DECLARE @RateV TABLE (HourlyRate DECIMAL(18,4) NULL, Markup DECIMAL(18,4) NULL, RateSource NVARCHAR(20) NULL);
            INSERT INTO @RateV
            EXEC dbo.ReadEffectiveRateForVendorProject @VendorId = @VendorId, @ProjectId = @ProjectId;
            SELECT TOP 1 @HourlyRate = HourlyRate, @Markup = Markup, @RateSource = RateSource FROM @RateV;
            DELETE FROM @RateV;
        END

        -- Compute total amount only when we have a rate. Markup may be NULL
        -- (treated as 0). When rate is missing we annotate and skip the math.
        IF @HourlyRate IS NOT NULL
        BEGIN
            SET @TotalAmount = @TotalHours * @HourlyRate * (1 + ISNULL(@Markup, 0));
        END
        ELSE
        BEGIN
            SET @Description = N'Rate not configured for ' + @WorkerName
                + N' on Project Id=' + ISNULL(CAST(@ProjectId AS NVARCHAR(20)), N'(none)')
                + N'. Set a default on the Worker or add a per-project override.';
            SET @Note = N'rate_source=none';
        END

        IF @EmployeeId IS NOT NULL
        BEGIN
            -- Upsert EmployeeLabor on natural key.
            DECLARE @ExistingElId BIGINT;
            SELECT @ExistingElId = [Id]
            FROM dbo.[EmployeeLabor]
            WHERE [EmployeeId] = @EmployeeId
              AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId))
              AND [WorkDate] = @WorkDate
              AND [BillingPeriodStart] = @BillingPeriodStart;

            IF @ExistingElId IS NULL
            BEGIN
                INSERT INTO dbo.[EmployeeLabor]
                    ([CreatedDatetime], [ModifiedDatetime], [EmployeeId], [ProjectId], [WorkDate],
                     [BillingPeriodStart], [BillingPeriodEnd], [TotalHours], [HourlyRate], [Markup],
                     [TotalAmount], [Description], [Status], [SourceTimeEntryId])
                VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @EmployeeId, @ProjectId, @WorkDate,
                        @BillingPeriodStart, @BillingPeriodEnd, @TotalHours, @HourlyRate, @Markup,
                        @TotalAmount, @Description, @Status, @TimeEntryId);
                SET @ExistingElId = SCOPE_IDENTITY();
            END
            ELSE
            BEGIN
                -- Only overwrite if row is still mutable (not yet invoiced).
                -- Once 'invoiced', the row is frozen — re-aggregation would
                -- desync the live Invoice. Caller sees this via the Results
                -- row's Note.
                IF EXISTS (SELECT 1 FROM dbo.[EmployeeLabor] WHERE [Id] = @ExistingElId AND [Status] = 'invoiced')
                BEGIN
                    SET @Note = COALESCE(@Note + N'; ', N'') + N'frozen — already invoiced, skipped';
                END
                ELSE
                BEGIN
                    UPDATE dbo.[EmployeeLabor]
                    SET [ModifiedDatetime] = SYSUTCDATETIME(),
                        [TotalHours]   = @TotalHours,
                        [HourlyRate]   = @HourlyRate,
                        [Markup]       = @Markup,
                        [TotalAmount]  = @TotalAmount,
                        [Description]  = @Description,
                        [BillingPeriodEnd] = @BillingPeriodEnd,
                        [SourceTimeEntryId] = @TimeEntryId
                    WHERE [Id] = @ExistingElId;
                END
            END

            INSERT INTO @Results VALUES (N'EmployeeLabor', @ExistingElId, @ProjectId, @WorkDate,
                                         @TotalHours, @HourlyRate, @Markup, @RateSource, @Status, @Note);
        END
        ELSE
        BEGIN
            -- Upsert ContractLabor. The existing table has more columns; we
            -- only fill the ones aggregation cares about + leave Excel-import
            -- legacy columns NULL (per Phase 5 schema rationalization decision).
            DECLARE @ExistingClId BIGINT;
            SELECT @ExistingClId = [Id]
            FROM dbo.[ContractLabor]
            WHERE [VendorId] = @VendorId
              AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId))
              AND [WorkDate] = @WorkDate
              AND [BillingPeriodStart] = @BillingPeriodStart;

            IF @ExistingClId IS NULL
            BEGIN
                -- EmployeeName is the legacy raw-string column; for TT-sourced
                -- rows we populate it with the User's name so existing
                -- ContractLabor list views still render a label.
                INSERT INTO dbo.[ContractLabor]
                    ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ProjectId], [WorkDate],
                     [BillingPeriodStart], [TotalHours], [HourlyRate], [Markup], [TotalAmount],
                     [Description], [Status], [BillVendorId], [EmployeeName])
                VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @VendorId, @ProjectId, @WorkDate,
                        @BillingPeriodStart, @TotalHours, @HourlyRate, @Markup, @TotalAmount,
                        @Description, @Status, @VendorId, @WorkerName);
                SET @ExistingClId = SCOPE_IDENTITY();
            END
            ELSE
            BEGIN
                -- Mirror EmployeeLabor: skip if already billed (terminal state).
                IF EXISTS (SELECT 1 FROM dbo.[ContractLabor] WHERE [Id] = @ExistingClId AND [Status] = 'billed')
                BEGIN
                    SET @Note = COALESCE(@Note + N'; ', N'') + N'frozen — already billed, skipped';
                END
                ELSE
                BEGIN
                    UPDATE dbo.[ContractLabor]
                    SET [ModifiedDatetime] = SYSUTCDATETIME(),
                        [TotalHours]  = @TotalHours,
                        [HourlyRate]  = @HourlyRate,
                        [Markup]      = @Markup,
                        [TotalAmount] = @TotalAmount,
                        [Description] = @Description
                    WHERE [Id] = @ExistingClId;
                END
            END

            INSERT INTO @Results VALUES (N'ContractLabor', @ExistingClId, @ProjectId, @WorkDate,
                                         @TotalHours, @HourlyRate, @Markup, @RateSource, @Status, @Note);
        END

        FETCH NEXT FROM bucket_cur INTO @ProjectId, @TotalHours;
    END

    CLOSE bucket_cur;
    DEALLOCATE bucket_cur;

    -- Surface the per-project results so the calling Python can log them.
    SELECT TargetTable, TargetRowId, ProjectId,
           CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
           TotalHours, HourlyRate, Markup, RateSource, Status, Note
    FROM @Results;
END;
GO

PRINT 'AggregateTimeEntryOnSubmit sproc created.';
