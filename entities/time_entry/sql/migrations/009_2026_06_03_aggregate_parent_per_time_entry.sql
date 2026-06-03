-- =============================================================================
-- 2026-06-03 — Aggregator bug fix: parent ContractLabor / EmployeeLabor totals
-- =============================================================================
--
-- Two bugs in the previous migration (008):
--
--   1. `SELECT @var = col WHERE no_match` is a no-op in T-SQL — the variable
--      keeps its prior value rather than being NULLed. The cursor loop's
--      `SELECT @ExistingClId = [Id] FROM ContractLabor WHERE ...ProjectId=...`
--      meant bucket #2 (different project) kept bucket #1's SCOPE_IDENTITY,
--      took the UPDATE branch instead of INSERT, and overwrote bucket #1's
--      parent with its own TotalHours. Each subsequent bucket overwrote
--      again. End state: parent.TotalHours = LAST bucket's hours,
--      parent.ProjectId = FIRST bucket's project — neither right.
--
--   2. Conceptual: parent should be one row per (worker, day, billing period),
--      with N line items per project — matches the React Edit / View pages.
--      Previous lookup keyed on (VendorId, ProjectId, WorkDate, BillingPeriod)
--      was a per-project-parent intent that conflicts with the line-item
--      model and triggers bug #1.
--
-- Fix:
--   - Move parent upsert OUT of the cursor.
--   - Parent identity = SourceTimeEntryId (1:1 with the TimeEntry).
--   - Parent.TotalHours = SUM(bucket.TotalHours).
--   - Parent.ProjectId  = the single bucket's project when N=1, else NULL.
--   - Parent.HourlyRate / Markup / TotalAmount: single bucket → that
--     bucket's values; multi-project → NULL (line items carry per-project
--     rates, parent aggregate is meaningless).
--   - Cursor loop now ONLY upserts line items.
--
-- Mirrors the same fix on the EmployeeLabor branch (same bug, same shape).
-- =============================================================================

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
        @WorkerName = LTRIM(RTRIM(ISNULL([Firstname], N'') + N' ' + ISNULL([Lastname], N'')))
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

    -- Semi-monthly billing period (decision #3).
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

    -- Per-project buckets.
    DECLARE @Buckets TABLE (
        ProjectId    BIGINT        NULL,
        TotalHours   DECIMAL(6,2)  NOT NULL,
        ConcatNotes  NVARCHAR(MAX) NULL
    );

    INSERT INTO @Buckets (ProjectId, TotalHours, ConcatNotes)
    SELECT
        tl.[ProjectId],
        SUM(ISNULL(tl.[Duration], 0)),
        STRING_AGG(NULLIF(LTRIM(RTRIM(ISNULL(tl.[Note], N''))), N''), N'; ')
            WITHIN GROUP (ORDER BY tl.[ClockIn])
    FROM dbo.[TimeLog] tl
    WHERE tl.[TimeEntryId] = @TimeEntryId
      AND (tl.[LogType] IS NULL OR tl.[LogType] = 'work')
    GROUP BY tl.[ProjectId];

    DECLARE @Results TABLE (
        TargetTable    NVARCHAR(30)  NOT NULL,
        TargetRowId    BIGINT        NULL,
        LineItemRowId  BIGINT        NULL,
        ProjectId      BIGINT        NULL,
        WorkDate       DATE          NOT NULL,
        TotalHours     DECIMAL(6,2)  NOT NULL,
        HourlyRate     DECIMAL(18,4) NULL,
        Markup         DECIMAL(18,4) NULL,
        RateSource     NVARCHAR(20)  NULL,
        Status         NVARCHAR(20)  NOT NULL,
        Note           NVARCHAR(500) NULL
    );

    -- ─── Parent-level aggregates ───────────────────────────────────────────
    DECLARE @BucketCount     INT;
    DECLARE @ParentTotalHrs  DECIMAL(6,2);
    DECLARE @ParentProjectId BIGINT;
    DECLARE @ParentRate      DECIMAL(18,4);
    DECLARE @ParentMarkup    DECIMAL(18,4);
    DECLARE @ParentAmount    DECIMAL(18,2);
    DECLARE @ParentRateSrc   NVARCHAR(20);
    DECLARE @ParentDesc      NVARCHAR(MAX) = NULL;
    DECLARE @ParentNote      NVARCHAR(500) = NULL;

    SELECT
        @BucketCount    = COUNT(*),
        @ParentTotalHrs = SUM(TotalHours)
    FROM @Buckets;

    IF @BucketCount = 0
    BEGIN
        -- No work logs (only breaks, or no logs at all). Nothing to aggregate.
        SELECT TargetTable, TargetRowId, LineItemRowId, ProjectId,
               CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
               TotalHours, HourlyRate, Markup, RateSource, Status, Note
        FROM @Results;
        RETURN;
    END

    IF @BucketCount = 1
    BEGIN
        SELECT TOP 1 @ParentProjectId = ProjectId FROM @Buckets;

        IF @EmployeeId IS NOT NULL
        BEGIN
            DECLARE @RateE_Parent TABLE (HourlyRate DECIMAL(18,4) NULL, Markup DECIMAL(18,4) NULL, RateSource NVARCHAR(20) NULL);
            INSERT INTO @RateE_Parent
            EXEC dbo.ReadEffectiveRateForEmployeeProject @EmployeeId = @EmployeeId, @ProjectId = @ParentProjectId;
            SELECT TOP 1 @ParentRate = HourlyRate, @ParentMarkup = Markup, @ParentRateSrc = RateSource FROM @RateE_Parent;
        END
        ELSE
        BEGIN
            DECLARE @RateV_Parent TABLE (HourlyRate DECIMAL(18,4) NULL, Markup DECIMAL(18,4) NULL, RateSource NVARCHAR(20) NULL);
            INSERT INTO @RateV_Parent
            EXEC dbo.ReadEffectiveRateForVendorProject @VendorId = @VendorId, @ProjectId = @ParentProjectId;
            SELECT TOP 1 @ParentRate = HourlyRate, @ParentMarkup = Markup, @ParentRateSrc = RateSource FROM @RateV_Parent;
        END

        IF @ParentRate IS NOT NULL
        BEGIN
            SET @ParentAmount = @ParentTotalHrs * @ParentRate * (1 + ISNULL(@ParentMarkup, 0));
        END
        ELSE
        BEGIN
            SET @ParentDesc = N'Rate not configured for ' + @WorkerName
                + N' on Project Id=' + ISNULL(CAST(@ParentProjectId AS NVARCHAR(20)), N'(none)')
                + N'. Set a default on the Worker or add a per-project override.';
            SET @ParentNote = N'rate_source=none';
        END
    END
    ELSE
    BEGIN
        -- Multi-project: parent ProjectId / rate / markup / amount are
        -- meaningless aggregates. Leave NULL — the per-project values live
        -- on the line items.
        SET @ParentProjectId = NULL;
        SET @ParentRate      = NULL;
        SET @ParentMarkup    = NULL;
        SET @ParentAmount    = NULL;
        SET @ParentRateSrc   = 'multi_project';
    END

    DECLARE @Status NVARCHAR(20) = 'pending_review';

    -- ─── Parent upsert: ONE row per TimeEntry, keyed on SourceTimeEntryId ──
    DECLARE @ParentRowId BIGINT;

    IF @EmployeeId IS NOT NULL
    BEGIN
        SELECT @ParentRowId = [Id]
        FROM dbo.[EmployeeLabor]
        WHERE [SourceTimeEntryId] = @TimeEntryId;

        IF @ParentRowId IS NULL
        BEGIN
            INSERT INTO dbo.[EmployeeLabor]
                ([CreatedDatetime], [ModifiedDatetime], [EmployeeId], [ProjectId], [WorkDate],
                 [BillingPeriodStart], [BillingPeriodEnd], [TotalHours], [HourlyRate], [Markup],
                 [TotalAmount], [Description], [Status], [SourceTimeEntryId])
            VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @EmployeeId, @ParentProjectId, @WorkDate,
                    @BillingPeriodStart, @BillingPeriodEnd, @ParentTotalHrs, @ParentRate, @ParentMarkup,
                    @ParentAmount, @ParentDesc, @Status, @TimeEntryId);
            SET @ParentRowId = SCOPE_IDENTITY();
        END
        ELSE
        BEGIN
            IF EXISTS (SELECT 1 FROM dbo.[EmployeeLabor] WHERE [Id] = @ParentRowId AND [Status] = 'invoiced')
            BEGIN
                -- Frozen — already invoiced; surface a note + skip child upserts.
                SET @ParentNote = COALESCE(@ParentNote + N'; ', N'') + N'frozen — already invoiced, skipped';
                INSERT INTO @Results VALUES (N'EmployeeLabor', @ParentRowId, NULL, @ParentProjectId, @WorkDate,
                                             @ParentTotalHrs, @ParentRate, @ParentMarkup, @ParentRateSrc, @Status, @ParentNote);

                SELECT TargetTable, TargetRowId, LineItemRowId, ProjectId,
                       CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
                       TotalHours, HourlyRate, Markup, RateSource, Status, Note
                FROM @Results;
                RETURN;
            END

            UPDATE dbo.[EmployeeLabor]
            SET [ModifiedDatetime]  = SYSUTCDATETIME(),
                [ProjectId]         = @ParentProjectId,
                [TotalHours]        = @ParentTotalHrs,
                [HourlyRate]        = @ParentRate,
                [Markup]            = @ParentMarkup,
                [TotalAmount]       = @ParentAmount,
                [Description]       = @ParentDesc,
                [BillingPeriodEnd]  = @BillingPeriodEnd,
                [SourceTimeEntryId] = @TimeEntryId
            WHERE [Id] = @ParentRowId;
        END
    END
    ELSE
    BEGIN
        SELECT @ParentRowId = [Id]
        FROM dbo.[ContractLabor]
        WHERE [SourceTimeEntryId] = @TimeEntryId;

        IF @ParentRowId IS NULL
        BEGIN
            INSERT INTO dbo.[ContractLabor]
                ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ProjectId], [WorkDate],
                 [BillingPeriodStart], [TotalHours], [HourlyRate], [Markup], [TotalAmount],
                 [Description], [Status], [BillVendorId], [EmployeeName], [SourceTimeEntryId])
            VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @VendorId, @ParentProjectId, @WorkDate,
                    @BillingPeriodStart, @ParentTotalHrs, @ParentRate, @ParentMarkup, @ParentAmount,
                    @ParentDesc, @Status, @VendorId, @WorkerName, @TimeEntryId);
            SET @ParentRowId = SCOPE_IDENTITY();
        END
        ELSE
        BEGIN
            IF EXISTS (SELECT 1 FROM dbo.[ContractLabor] WHERE [Id] = @ParentRowId AND [Status] = 'billed')
            BEGIN
                SET @ParentNote = COALESCE(@ParentNote + N'; ', N'') + N'frozen — already billed, skipped';
                INSERT INTO @Results VALUES (N'ContractLabor', @ParentRowId, NULL, @ParentProjectId, @WorkDate,
                                             @ParentTotalHrs, @ParentRate, @ParentMarkup, @ParentRateSrc, @Status, @ParentNote);

                SELECT TargetTable, TargetRowId, LineItemRowId, ProjectId,
                       CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
                       TotalHours, HourlyRate, Markup, RateSource, Status, Note
                FROM @Results;
                RETURN;
            END

            UPDATE dbo.[ContractLabor]
            SET [ModifiedDatetime]  = SYSUTCDATETIME(),
                [ProjectId]         = @ParentProjectId,
                [TotalHours]        = @ParentTotalHrs,
                [HourlyRate]        = @ParentRate,
                [Markup]            = @ParentMarkup,
                [TotalAmount]       = @ParentAmount,
                [Description]       = @ParentDesc,
                [SourceTimeEntryId] = @TimeEntryId
            WHERE [Id] = @ParentRowId;
        END
    END

    -- ─── Per-bucket line-item upserts ──────────────────────────────────────
    DECLARE @ProjectId    BIGINT;
    DECLARE @TotalHours   DECIMAL(6,2);
    DECLARE @ConcatNotes  NVARCHAR(MAX);

    DECLARE bucket_cur CURSOR LOCAL FAST_FORWARD FOR
        SELECT ProjectId, TotalHours, ConcatNotes FROM @Buckets;

    OPEN bucket_cur;
    FETCH NEXT FROM bucket_cur INTO @ProjectId, @TotalHours, @ConcatNotes;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        DECLARE @HourlyRate     DECIMAL(18,4) = NULL;
        DECLARE @Markup         DECIMAL(18,4) = NULL;
        DECLARE @RateSource     NVARCHAR(20)  = 'none';
        DECLARE @TotalAmount    DECIMAL(18,2) = NULL;
        DECLARE @LineItemRowId  BIGINT        = NULL;
        DECLARE @LineNote       NVARCHAR(500) = NULL;

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

        IF @HourlyRate IS NOT NULL
        BEGIN
            SET @TotalAmount = @TotalHours * @HourlyRate * (1 + ISNULL(@Markup, 0));
        END
        ELSE
        BEGIN
            SET @LineNote = N'rate_source=none for Project Id=' + ISNULL(CAST(@ProjectId AS NVARCHAR(20)), N'(none)');
        END

        IF @EmployeeId IS NOT NULL
        BEGIN
            -- Line-item lookup: NULL-defend before SELECT to avoid the
            -- same `SELECT @var = ... WHERE no_match` no-op trap on the
            -- parent. (Belt-and-suspenders — line items are upserted
            -- within a single parent so collision is less likely, but
            -- the bug class is real either way.)
            SET @LineItemRowId = NULL;
            SELECT @LineItemRowId = [Id]
            FROM dbo.[EmployeeLaborLineItem]
            WHERE [EmployeeLaborId]   = @ParentRowId
              AND [SourceTimeEntryId] = @TimeEntryId
              AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId));

            IF @LineItemRowId IS NULL
            BEGIN
                INSERT INTO dbo.[EmployeeLaborLineItem]
                    ([CreatedDatetime], [ModifiedDatetime], [EmployeeLaborId], [LineDate], [ProjectId],
                     [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
                     [IsBillable], [IsOverhead], [SourceTimeEntryId])
                VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @ParentRowId, @WorkDate, @ProjectId,
                        NULL, @ConcatNotes, @TotalHours, @HourlyRate, @Markup, @TotalAmount,
                        1, 0, @TimeEntryId);
                SET @LineItemRowId = SCOPE_IDENTITY();
            END
            ELSE
            BEGIN
                -- Preserve PM edits: SubCostCodeId, Description, IsBillable,
                -- IsOverhead, InvoiceLineItemId all left alone.
                UPDATE dbo.[EmployeeLaborLineItem]
                SET [ModifiedDatetime] = SYSUTCDATETIME(),
                    [Hours]    = @TotalHours,
                    [Rate]     = @HourlyRate,
                    [Markup]   = @Markup,
                    [Price]    = @TotalAmount,
                    [LineDate] = @WorkDate
                WHERE [Id] = @LineItemRowId;
            END

            INSERT INTO @Results VALUES (N'EmployeeLabor', @ParentRowId, @LineItemRowId, @ProjectId, @WorkDate,
                                         @TotalHours, @HourlyRate, @Markup, @RateSource, @Status, @LineNote);
        END
        ELSE
        BEGIN
            SET @LineItemRowId = NULL;
            SELECT @LineItemRowId = [Id]
            FROM dbo.[ContractLaborLineItem]
            WHERE [ContractLaborId]   = @ParentRowId
              AND [SourceTimeEntryId] = @TimeEntryId
              AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId));

            IF @LineItemRowId IS NULL
            BEGIN
                INSERT INTO dbo.[ContractLaborLineItem]
                    ([CreatedDatetime], [ModifiedDatetime], [ContractLaborId], [LineDate], [ProjectId],
                     [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
                     [IsBillable], [IsOverhead], [SourceTimeEntryId])
                VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @ParentRowId, @WorkDate, @ProjectId,
                        NULL, @ConcatNotes, @TotalHours, @HourlyRate, @Markup, @TotalAmount,
                        1, 0, @TimeEntryId);
                SET @LineItemRowId = SCOPE_IDENTITY();
            END
            ELSE
            BEGIN
                UPDATE dbo.[ContractLaborLineItem]
                SET [ModifiedDatetime] = SYSUTCDATETIME(),
                    [Hours]    = @TotalHours,
                    [Rate]     = @HourlyRate,
                    [Markup]   = @Markup,
                    [Price]    = @TotalAmount,
                    [LineDate] = @WorkDate
                WHERE [Id] = @LineItemRowId;
            END

            INSERT INTO @Results VALUES (N'ContractLabor', @ParentRowId, @LineItemRowId, @ProjectId, @WorkDate,
                                         @TotalHours, @HourlyRate, @Markup, @RateSource, @Status, @LineNote);
        END

        FETCH NEXT FROM bucket_cur INTO @ProjectId, @TotalHours, @ConcatNotes;
    END

    CLOSE bucket_cur;
    DEALLOCATE bucket_cur;

    SELECT TargetTable, TargetRowId, LineItemRowId, ProjectId,
           CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
           TotalHours, HourlyRate, Markup, RateSource, Status, Note
    FROM @Results;
END;
GO

PRINT 'Migration 009: AggregateTimeEntryOnSubmit parent-upsert fix applied.';
