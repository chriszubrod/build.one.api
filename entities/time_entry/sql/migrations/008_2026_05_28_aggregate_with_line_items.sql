-- =============================================================================
-- 2026-05-28 — Phase 0 fix: re-issue AggregateTimeEntryOnSubmit to also create
-- (and idempotently update) the child line item per (parent, project) bucket.
--
-- The v1+v2 sproc only created parent ContractLabor / EmployeeLabor rows. But
-- bill_service.generate_bills_for_vendor walks ContractLaborLineItem rows to
-- produce BillLineItems and silently skips parents with no children — so
-- TT-sourced aggregation produced unbillable rows.
--
-- This re-issue:
--   - extends @Buckets with a STRING_AGG of TimeLog notes per (worker, project)
--     so the auto-created line item carries something descriptive
--   - after each parent INSERT or UPDATE, upserts one child line item keyed on
--     (parent_id, SourceTimeEntryId, ProjectId)
--     - INSERT: SubCostCodeId NULL (PM assigns), Description = concat of TimeLog
--       notes, IsBillable = 1, IsOverhead = 0
--     - UPDATE: only auto-recomputable fields (Hours / Rate / Markup / Price /
--       LineDate). SubCostCodeId, Description, IsBillable, IsOverhead,
--       BillLineItemId / InvoiceLineItemId are preserved — re-aggregation
--       on reject→edit→resubmit will NOT clobber the PM's prior edits.
--   - widens the @Results @Note column to NVARCHAR(500) and adds LineItemRowId
--     so callers see both the parent and the child ids in one round trip
--
-- Re-run AFTER migration 003 (adds SourceTimeEntryId column to both line item
-- tables). Idempotent (CREATE OR ALTER).
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

    -- Semi-monthly billing period per decision #3.
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

    -- Group work logs by ProjectId; collect concatenated notes for line-item
    -- Description on first insert. Break logs (LogType='break') excluded;
    -- null-project work logs allowed (become an overhead-style row).
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
        DECLARE @Status         NVARCHAR(20)  = 'pending_review';
        DECLARE @Description    NVARCHAR(MAX) = NULL;
        DECLARE @Note           NVARCHAR(500) = NULL;
        DECLARE @LineItemRowId  BIGINT        = NULL;

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
            SET @Description = N'Rate not configured for ' + @WorkerName
                + N' on Project Id=' + ISNULL(CAST(@ProjectId AS NVARCHAR(20)), N'(none)')
                + N'. Set a default on the Worker or add a per-project override.';
            SET @Note = N'rate_source=none';
        END

        IF @EmployeeId IS NOT NULL
        BEGIN
            -- ── Parent EmployeeLabor upsert ──────────────────────────────
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
                IF EXISTS (SELECT 1 FROM dbo.[EmployeeLabor] WHERE [Id] = @ExistingElId AND [Status] = 'invoiced')
                BEGIN
                    SET @Note = COALESCE(@Note + N'; ', N'') + N'frozen — already invoiced, skipped';
                    SET @ExistingElId = NULL;  -- skip child line-item touch when parent is frozen
                END
                ELSE
                BEGIN
                    UPDATE dbo.[EmployeeLabor]
                    SET [ModifiedDatetime]    = SYSUTCDATETIME(),
                        [TotalHours]          = @TotalHours,
                        [HourlyRate]          = @HourlyRate,
                        [Markup]              = @Markup,
                        [TotalAmount]         = @TotalAmount,
                        [Description]         = @Description,
                        [BillingPeriodEnd]    = @BillingPeriodEnd,
                        [SourceTimeEntryId]   = @TimeEntryId
                    WHERE [Id] = @ExistingElId;
                END
            END

            -- ── Child EmployeeLaborLineItem upsert ───────────────────────
            IF @ExistingElId IS NOT NULL
            BEGIN
                SELECT @LineItemRowId = [Id]
                FROM dbo.[EmployeeLaborLineItem]
                WHERE [EmployeeLaborId]   = @ExistingElId
                  AND [SourceTimeEntryId] = @TimeEntryId
                  AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId));

                IF @LineItemRowId IS NULL
                BEGIN
                    INSERT INTO dbo.[EmployeeLaborLineItem]
                        ([CreatedDatetime], [ModifiedDatetime], [EmployeeLaborId], [LineDate], [ProjectId],
                         [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
                         [IsBillable], [IsOverhead], [SourceTimeEntryId])
                    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @ExistingElId, @WorkDate, @ProjectId,
                            NULL, @ConcatNotes, @TotalHours, @HourlyRate, @Markup, @TotalAmount,
                            1, 0, @TimeEntryId);
                    SET @LineItemRowId = SCOPE_IDENTITY();
                END
                ELSE
                BEGIN
                    -- Preserve PM edits: SubCostCodeId, Description, IsBillable,
                    -- IsOverhead, InvoiceLineItemId all left alone. Only the
                    -- auto-recomputable fields update.
                    UPDATE dbo.[EmployeeLaborLineItem]
                    SET [ModifiedDatetime] = SYSUTCDATETIME(),
                        [Hours]    = @TotalHours,
                        [Rate]     = @HourlyRate,
                        [Markup]   = @Markup,
                        [Price]    = @TotalAmount,
                        [LineDate] = @WorkDate
                    WHERE [Id] = @LineItemRowId;
                END
            END

            INSERT INTO @Results VALUES (N'EmployeeLabor', @ExistingElId, @LineItemRowId, @ProjectId, @WorkDate,
                                         @TotalHours, @HourlyRate, @Markup, @RateSource, @Status, @Note);
        END
        ELSE
        BEGIN
            -- ── Parent ContractLabor upsert ──────────────────────────────
            DECLARE @ExistingClId BIGINT;
            SELECT @ExistingClId = [Id]
            FROM dbo.[ContractLabor]
            WHERE [VendorId] = @VendorId
              AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId))
              AND [WorkDate] = @WorkDate
              AND [BillingPeriodStart] = @BillingPeriodStart;

            IF @ExistingClId IS NULL
            BEGIN
                INSERT INTO dbo.[ContractLabor]
                    ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ProjectId], [WorkDate],
                     [BillingPeriodStart], [TotalHours], [HourlyRate], [Markup], [TotalAmount],
                     [Description], [Status], [BillVendorId], [EmployeeName], [SourceTimeEntryId])
                VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @VendorId, @ProjectId, @WorkDate,
                        @BillingPeriodStart, @TotalHours, @HourlyRate, @Markup, @TotalAmount,
                        @Description, @Status, @VendorId, @WorkerName, @TimeEntryId);
                SET @ExistingClId = SCOPE_IDENTITY();
            END
            ELSE
            BEGIN
                IF EXISTS (SELECT 1 FROM dbo.[ContractLabor] WHERE [Id] = @ExistingClId AND [Status] = 'billed')
                BEGIN
                    SET @Note = COALESCE(@Note + N'; ', N'') + N'frozen — already billed, skipped';
                    SET @ExistingClId = NULL;  -- skip child line-item touch
                END
                ELSE
                BEGIN
                    UPDATE dbo.[ContractLabor]
                    SET [ModifiedDatetime]  = SYSUTCDATETIME(),
                        [TotalHours]        = @TotalHours,
                        [HourlyRate]        = @HourlyRate,
                        [Markup]            = @Markup,
                        [TotalAmount]       = @TotalAmount,
                        [Description]       = @Description,
                        [SourceTimeEntryId] = @TimeEntryId
                    WHERE [Id] = @ExistingClId;
                END
            END

            -- ── Child ContractLaborLineItem upsert ───────────────────────
            IF @ExistingClId IS NOT NULL
            BEGIN
                SELECT @LineItemRowId = [Id]
                FROM dbo.[ContractLaborLineItem]
                WHERE [ContractLaborId]   = @ExistingClId
                  AND [SourceTimeEntryId] = @TimeEntryId
                  AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId));

                IF @LineItemRowId IS NULL
                BEGIN
                    INSERT INTO dbo.[ContractLaborLineItem]
                        ([CreatedDatetime], [ModifiedDatetime], [ContractLaborId], [LineDate], [ProjectId],
                         [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
                         [IsBillable], [IsOverhead], [SourceTimeEntryId])
                    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @ExistingClId, @WorkDate, @ProjectId,
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
            END

            INSERT INTO @Results VALUES (N'ContractLabor', @ExistingClId, @LineItemRowId, @ProjectId, @WorkDate,
                                         @TotalHours, @HourlyRate, @Markup, @RateSource, @Status, @Note);
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

PRINT 'AggregateTimeEntryOnSubmit re-issued with line-item upsert + STRING_AGG description.';
