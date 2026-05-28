-- =============================================================================
-- 2026-05-27 — Phase 5: re-issue AggregateTimeEntryOnSubmit to stamp
-- SourceTimeEntryId on the ContractLabor path too (Phase 4 only stamped it on
-- EmployeeLabor because ContractLabor.SourceTimeEntryId didn't exist yet).
--
-- Re-run AFTER:
--   - entities/contract_labor/sql/migrations/2026_05_27_source_time_entry_id.sql
--
-- Idempotent (CREATE OR ALTER).
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
            DECLARE @ExistingClId BIGINT;
            SELECT @ExistingClId = [Id]
            FROM dbo.[ContractLabor]
            WHERE [VendorId] = @VendorId
              AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId))
              AND [WorkDate] = @WorkDate
              AND [BillingPeriodStart] = @BillingPeriodStart;

            IF @ExistingClId IS NULL
            BEGIN
                -- Phase 5 — now stamps SourceTimeEntryId on the vendor path
                -- too (column was added in
                -- contract_labor/sql/migrations/2026_05_27_source_time_entry_id.sql).
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
                END
                ELSE
                BEGIN
                    UPDATE dbo.[ContractLabor]
                    SET [ModifiedDatetime] = SYSUTCDATETIME(),
                        [TotalHours]  = @TotalHours,
                        [HourlyRate]  = @HourlyRate,
                        [Markup]      = @Markup,
                        [TotalAmount] = @TotalAmount,
                        [Description] = @Description,
                        [SourceTimeEntryId] = @TimeEntryId
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

    SELECT TargetTable, TargetRowId, ProjectId,
           CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
           TotalHours, HourlyRate, Markup, RateSource, Status, Note
    FROM @Results;
END;
GO

PRINT 'AggregateTimeEntryOnSubmit re-issued with ContractLabor.SourceTimeEntryId support.';
