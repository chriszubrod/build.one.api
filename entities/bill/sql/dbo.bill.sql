IF OBJECT_ID('dbo.Bill', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Bill]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NULL,
    [PaymentTermId] BIGINT NULL,
    [BillDate] DATETIME2(3) NOT NULL,
    [DueDate] DATETIME2(3) NOT NULL,
    [BillNumber] NVARCHAR(50) NULL,
    [TotalAmount] DECIMAL(18,2) NULL,
    [Memo] NVARCHAR(MAX) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    [IntakeSource] NVARCHAR(20) NULL,
    [IntakeSourceDetail] NVARCHAR(100) NULL,
    CONSTRAINT [FK_Bill_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id]),
    CONSTRAINT [FK_Bill_PaymentTerm] FOREIGN KEY ([PaymentTermId]) REFERENCES [dbo].[PaymentTerm]([Id])
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_Bill_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Bill_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD CONSTRAINT [FK_Bill_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

-- Additive: IntakeSource + IntakeSourceDetail capture how a bill arrived
-- (manual UI / agent / script). Set-once at create. Pre-existing rows
-- stay NULL.
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'IntakeSource')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [IntakeSource] NVARCHAR(20) NULL;
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'IntakeSourceDetail')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [IntakeSourceDetail] NVARCHAR(100) NULL;
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_VendorId' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE INDEX IX_Bill_VendorId ON [dbo].[Bill] ([VendorId]);
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_BillDate' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE INDEX IX_Bill_BillDate ON [dbo].[Bill] ([BillDate]);
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_BillNumber' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE INDEX IX_Bill_BillNumber ON [dbo].[Bill] ([BillNumber]);
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_PaymentTermId' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE INDEX IX_Bill_PaymentTermId ON [dbo].[Bill] ([PaymentTermId]);
END
GO

-- Additive: QboId/RealmId/SyncToken (U-238a dbo-native identity) were added
-- out-of-band before this base file was made canonical — the base CREATE TABLE
-- above never declared them, which would abort a from-scratch build at
-- SetBillQboIdentity's (and now ReadBillByQboIdAndRealmId's) CREATE PROCEDURE
-- time (SQL error 207). Idempotent, no-op-safe against live — columns + the
-- unique index already exist there. Same gap/fix as U-277's dbo.company.sql.
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

-- ===========================================================================
-- U-445 (U-357 Phase 3, LS-03a-1) — the canonical `Status` column.
--
-- Bill had no status of its own. U-443 DERIVED one per request from
-- `IsDraft x the latest Review row`, which was correct but unfilterable: you
-- cannot post-filter a paginated page without making `count` lie. This is the
-- stored column the `?status=` filter — and the Bills page tabs — need.
--
-- ORDER BELOW IS LOAD-BEARING. `Status` defaults to 'draft', so the moment the
-- column exists all 20,224 completed bills read 'draft' and CK_Bill_Status_IsDraft
-- would be violated. Columns, THEN backfill, THEN constraints — and the CHECKs
-- go on WITH CHECK so SQL Server validates all 20,266 existing rows, which is
-- itself the proof that the backfill was right.
--
-- `IsDraft` REMAINS A REAL, WRITTEN COLUMN in this unit. Retiring it (drop +
-- re-add as PERSISTED computed) is U-446. Dual-writing is what removes the
-- old-image deploy window entirely: CK_Bill_Status_IsDraft makes drift between
-- the two impossible at the database level, so an API image that knows nothing
-- about Status still cannot produce an inconsistent row.
-- ===========================================================================
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'Status')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [Status] NVARCHAR(20) NOT NULL
        CONSTRAINT [DF_Bill_Status] DEFAULT ('draft');
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'StatusDatetime')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [StatusDatetime] DATETIME2(3) NULL;
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'StatusOrigin')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [StatusOrigin] NVARCHAR(24) NOT NULL
        CONSTRAINT [DF_Bill_StatusOrigin] DEFAULT ('user');
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'StatusSourceRef')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [StatusSourceRef] NVARCHAR(64) NULL;
END
GO

-- Backfill. Guarded on "no row has been stamped yet" so a re-apply of this base
-- file (they are re-run routinely) can never overwrite live status transitions.
-- Set-based per feedback_backfill_setbased_under_load — a per-row loop over 20k
-- rows TCP-drops under load.
--
-- NB the batching here is about statement size, NOT commit granularity:
-- scripts/run_sql.py executes every GO-batch on ONE connection and commits once
-- at the end, so the whole file is a single transaction and a failure rolls all
-- of it back. That is the right property for a backfill+constraint pair (no
-- half-stamped state can survive), and resumability comes from the
-- StatusDatetime guard across INVOCATIONS rather than within one.
--
-- The expression is the SAME one shared/lifecycle/resolver.py evaluates at read
-- time (U-443, flag-keyed since U-444), so stored and derived agree by
-- construction rather than by coincidence.
-- Guarded on the JOINED tables existing, and executed through sp_executesql
-- (Codex P1). dbo.[Review] carries FK_Review_Bill so it CANNOT exist before
-- this file has run, and an IF guard alone does NOT help: SQL Server defers
-- name resolution for stored-procedure BODIES, not for ad-hoc batches — this
-- batch compiles as a unit, so a missing table errors at compile time before
-- the IF is ever evaluated. Because run_sql.py commits the whole file as ONE
-- transaction, that would roll back the entire Status schema rather than just
-- the backfill. Same pattern as CountReviewStatusReferencesById (U-444).
--
-- A fresh database has nothing to backfill anyway: every Bill it goes on to
-- create takes its Status from CreateBill.
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.ReviewStatus', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.BillCompletionResult', 'U') IS NOT NULL
   AND EXISTS (SELECT 1 FROM dbo.[Bill])
   AND NOT EXISTS (SELECT 1 FROM dbo.[Bill] WHERE [StatusDatetime] IS NOT NULL)
BEGIN
    EXEC sp_executesql N'
    DECLARE @Batch INT = 5000;
    DECLARE @Done INT = 1;

    WHILE @Done > 0
    BEGIN
        UPDATE TOP (@Batch) b
        SET b.[Status] = CASE
                WHEN b.[IsDraft] = 0            THEN ''completed''
                WHEN cur.[IsDeclined] = 1       THEN ''declined''
                WHEN cur.[IsFinal] = 1          THEN ''approved''
                WHEN cur.[IsInitial] = 1        THEN ''submitted''
                WHEN cur.[BillId] IS NOT NULL   THEN ''in_review''
                ELSE ''draft''
            END,
            b.[StatusDatetime] = SYSUTCDATETIME(),
            b.[StatusOrigin] = CASE
                WHEN b.[IsDraft] = 0 AND EXISTS (
                    SELECT 1 FROM dbo.[BillCompletionResult] r
                    WHERE r.[BillPublicId] = b.[PublicId]
                ) THEN ''completion''
                WHEN b.[QboId] IS NOT NULL THEN ''qbo_pull''
                ELSE ''backfill''
            END,
            b.[StatusSourceRef] = CASE
                WHEN b.[QboId] IS NOT NULL THEN CONCAT(''qbo:'', b.[RealmId], ''/'', b.[QboId])
                ELSE NULL
            END
        FROM dbo.[Bill] b
        OUTER APPLY (
            SELECT TOP 1 r.[BillId], rs.[IsFinal], rs.[IsDeclined], rs.[IsInitial]
            FROM dbo.[Review] r
            INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = r.[ReviewStatusId]
            WHERE r.[BillId] = b.[Id]
            ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC
        ) cur
        WHERE b.[StatusDatetime] IS NULL;

        SET @Done = @@ROWCOUNT;
    END';
END
GO

-- Constraints LAST — see the ordering note above. WITH CHECK (the default for
-- ALTER ... ADD CONSTRAINT, stated explicitly here because it is the point)
-- validates every existing row, so applying this file IS the parity proof.
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Bill_Status')
BEGIN
    ALTER TABLE [dbo].[Bill] WITH CHECK ADD CONSTRAINT [CK_Bill_Status]
        CHECK ([Status] IN ('draft','submitted','in_review','approved','declined','completed'));
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Bill_StatusOrigin')
BEGIN
    ALTER TABLE [dbo].[Bill] WITH CHECK ADD CONSTRAINT [CK_Bill_StatusOrigin]
        CHECK ([StatusOrigin] IN ('user','completion','qbo_pull','fast_path','backfill'));
END
GO

-- THE safety property of this unit. While both columns are really written,
-- this makes them incapable of disagreeing — so an old API image that has
-- never heard of Status still cannot produce an inconsistent row, and the
-- three-step swap the design called for is not needed here.
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Bill_Status_IsDraft')
BEGIN
    ALTER TABLE [dbo].[Bill] WITH CHECK ADD CONSTRAINT [CK_Bill_Status_IsDraft]
        CHECK ((CASE WHEN [Status] = 'completed' THEN 0 ELSE 1 END) = [IsDraft]);
END
GO

-- Filtered: 42 of 20,266 rows are non-completed today, so this index IS the
-- working set for every tab except Billed (which is the paginated scan it
-- already was).
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_Status' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
    CREATE NONCLUSTERED INDEX [IX_Bill_Status] ON [dbo].[Bill] ([Status])
        INCLUDE ([IsDraft], [VendorId], [BillDate])
        WHERE [Status] <> 'completed';
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'SyncToken')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [SyncToken] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Bill_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Bill')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Bill_QboId_RealmId ON [dbo].[Bill] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- Unique filtered index on Vendor + BillNumber + BillDate — prevents duplicates
-- Filtered to non-NULL VendorId and BillNumber so drafts without these fields are not constrained
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_VendorId_BillDate_BillNumber' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
    DROP INDEX [IX_Bill_VendorId_BillDate_BillNumber] ON [dbo].[Bill];
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Bill_VendorId_BillNumber_BillDate' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE UNIQUE INDEX [UQ_Bill_VendorId_BillNumber_BillDate]
    ON [dbo].[Bill] ([VendorId], [BillNumber], [BillDate])
    WHERE [VendorId] IS NOT NULL AND [BillNumber] IS NOT NULL;
END
GO


-- CreateBill is defined canonically in dbo.bill_create_source_email.sql (includes the Gap-2
-- @CreatedByUserId threading + the DueDate=BillDate mirror). The stale duplicate that lived
-- here — missing @CreatedByUserId — was removed 2026-07-12: a base re-run applying it would
-- have regressed the CreatedByUserId threading. Do NOT re-add a CreateBill definition to this
-- file (same single-source-of-truth cleanup as the BillCompletionResult block earlier).




-- Read Bills Stored Procedures
CREATE OR ALTER PROCEDURE ReadBills
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        b.[Id],
        b.[PublicId],
        b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[VendorId],
        b.[PaymentTermId],
        CONVERT(VARCHAR(19), b.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), b.[DueDate], 120) AS [DueDate],
        b.[BillNumber],
        b.[TotalAmount],
        b.[Memo],
        b.[IsDraft],
        b.[Status],
        b.[StatusDatetime],
        b.[StatusOrigin],
        b.[StatusSourceRef],
        b.[IntakeSource],
        b.[IntakeSourceDetail],
        b.[SourceEmailMessageId]
    FROM dbo.[Bill] b
    WHERE dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, b.[Id]) = 1
    ORDER BY b.[BillDate] DESC, b.[BillNumber] ASC;
    COMMIT TRANSACTION;
END;
GO

-- Read Bill By Id Stored Procedures
CREATE OR ALTER PROCEDURE ReadBillById
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
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId],
        [QboId],
        [RealmId]
    FROM dbo.[Bill]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- U-283 (Phase-4): direct dbo-native identity lookup, mirrors dbo.customer.sql's
-- ReadCustomerByQboIdAndRealmId / dbo.project.sql's ReadProjectByQboIdAndRealmId.
-- Lets the Bill connector resolve "does a dbo.Bill already exist for this
-- external QBO id" WITHOUT hopping through the qbo.BillBill mapping table —
-- every Bill synced at least once already carries QboId/RealmId via
-- SetBillQboIdentity, so this is the steady-state fast path; the mapping-table
-- lookup remains as a fallback for rows that predate identity stamping.
-- RBAC-scoped via the existing UserCanAccessBill UDF, like every other Bill read.
CREATE OR ALTER PROCEDURE ReadBillByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50) = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        b.[Id],
        b.[PublicId],
        b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[VendorId],
        b.[PaymentTermId],
        CONVERT(VARCHAR(19), b.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), b.[DueDate], 120) AS [DueDate],
        b.[BillNumber],
        b.[TotalAmount],
        b.[Memo],
        b.[IsDraft],
        b.[Status],
        b.[StatusDatetime],
        b.[StatusOrigin],
        b.[StatusSourceRef],
        b.[IntakeSource],
        b.[IntakeSourceDetail],
        b.[SourceEmailMessageId],
        b.[QboId],
        b.[RealmId]
    FROM dbo.[Bill] b
    WHERE b.[QboId] = @QboId
      AND ((b.[RealmId] = @RealmId) OR (b.[RealmId] IS NULL AND @RealmId IS NULL))
      AND dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, b.[Id]) = 1;

    COMMIT TRANSACTION;
END;
GO





-- Read Bill By Public Id Stored Procedures
-- U-301b: additive [QboId]/[RealmId] columns — outbox/business/worker.py's
-- _refresh_bill reads them off this sproc's caller (BillService.read_by_
-- public_id) to try the dbo-native identity fast path before falling back
-- to the legacy qbo.BillBill -> qbo.Bill two-hop. Bill._from_db (the shared
-- row mapper) already reads these defensively via getattr(row, "QboId",
-- None), so this is safe for every other caller regardless of column
-- presence — mirrors ReadBillById's existing SELECT immediately above.
CREATE OR ALTER PROCEDURE ReadBillByPublicId
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
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId],
        [QboId],
        [RealmId]
    FROM dbo.[Bill]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO



-- Read Bill By Bill Number Stored Procedures
CREATE OR ALTER PROCEDURE ReadBillByBillNumber
(
    @BillNumber NVARCHAR(50)
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
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId]
    FROM dbo.[Bill]
    WHERE [BillNumber] = @BillNumber;

    COMMIT TRANSACTION;
END;
GO








-- Read Bill By Bill Number And Vendor Id Stored Procedures
CREATE OR ALTER PROCEDURE ReadBillByBillNumberAndVendorId
(
    @BillNumber NVARCHAR(50),
    @VendorId BIGINT,
    @BillDate DATETIME2(3) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId]
    FROM dbo.[Bill]
    WHERE [BillNumber] = @BillNumber
      AND [VendorId] = @VendorId
      AND (@BillDate IS NULL OR [BillDate] = @BillDate);

    COMMIT TRANSACTION;
END;
GO





-- Update Bill By Id Stored Procedures

CREATE OR ALTER PROCEDURE UpdateBillById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT,
    @PaymentTermId BIGINT NULL,
    @BillDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @BillNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- IntakeSource / IntakeSourceDetail are set-once at create. The UPDATE
    -- statement deliberately omits them so existing values are preserved.
    UPDATE dbo.[Bill]
    SET
        [ModifiedDatetime] = @Now,
        [VendorId] = @VendorId,
        [PaymentTermId] = @PaymentTermId,
        [BillDate] = @BillDate,
        [DueDate] = @BillDate,
        [BillNumber] = @BillNumber,
        [TotalAmount] = @TotalAmount,
        [Memo] = @Memo,
        -- U-445 compat translation. Callers that predate Status — an old API
        -- image mid-deploy, the QBO pull connectors, a queued iOS PUT — still
        -- send only @IsDraft, and CK_Bill_Status_IsDraft would reject the write
        -- outright if Status were left behind. So:
        --   @IsDraft = 0  ->  also move Status to 'completed'
        --   @IsDraft = 1  ->  NEUTRALISED. Nothing un-completes a bill through
        --                     a field update; that would silently reopen a
        --                     document whose AP already reached QBO/Excel/Box.
        --                     The only legitimate 1 is on a row that is already
        --                     a draft, where it is a no-op anyway.
        [IsDraft] = CASE WHEN @IsDraft = 0 THEN 0 ELSE [IsDraft] END,
        [Status] = CASE
            WHEN @IsDraft = 0 AND [Status] <> 'completed' THEN 'completed'
            ELSE [Status] END,
        [StatusDatetime] = CASE
            WHEN @IsDraft = 0 AND [Status] <> 'completed' THEN @Now
            ELSE [StatusDatetime] END,
        [StatusOrigin] = CASE
            WHEN @IsDraft = 0 AND [Status] <> 'completed' THEN 'completion'
            ELSE [StatusOrigin] END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[PaymentTermId],
        CONVERT(VARCHAR(19), INSERTED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[BillNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft],
        INSERTED.[Status],
        INSERTED.[StatusDatetime],
        INSERTED.[StatusOrigin],
        INSERTED.[StatusSourceRef],
        INSERTED.[IntakeSource],
        INSERTED.[IntakeSourceDetail],
        INSERTED.[SourceEmailMessageId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO





-- Delete Bill By Id Stored Procedures
CREATE OR ALTER PROCEDURE DeleteBillById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Bill]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        DELETED.[PaymentTermId],
        CONVERT(VARCHAR(19), DELETED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), DELETED.[DueDate], 120) AS [DueDate],
        DELETED.[BillNumber],
        DELETED.[TotalAmount],
        DELETED.[Memo],
        DELETED.[IsDraft],
        DELETED.[Status],
        DELETED.[StatusDatetime],
        DELETED.[StatusOrigin],
        DELETED.[StatusSourceRef],
        DELETED.[IntakeSource],
        DELETED.[IntakeSourceDetail],
        DELETED.[SourceEmailMessageId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO






-- Pagination and filtering procedures
CREATE OR ALTER PROCEDURE ReadBillsPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    -- U-445. The canonical filter. `@IsDraft` is kept because every
    -- existing caller still sends it; the two are consistent by
    -- CK_Bill_Status_IsDraft, so passing both is safe rather than
    -- contradictory.
    @Status NVARCHAR(20) = NULL,
    @SortBy NVARCHAR(50) = 'BillDate',
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;
    DECLARE @SortColumn NVARCHAR(50) = CASE @SortBy
        WHEN 'BillNumber' THEN 'BillNumber'
        WHEN 'BillDate' THEN 'BillDate'
        WHEN 'DueDate' THEN 'DueDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'VendorId' THEN 'VendorId'
        ELSE 'BillDate'
    END;
    DECLARE @SortDir NVARCHAR(4) = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

    SELECT
        b.[Id],
        b.[PublicId],
        b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[VendorId],
        b.[PaymentTermId],
        CONVERT(VARCHAR(19), b.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), b.[DueDate], 120) AS [DueDate],
        b.[BillNumber],
        b.[TotalAmount],
        b.[Memo],
        b.[IsDraft],
        b.[Status],
        b.[StatusDatetime],
        b.[StatusOrigin],
        b.[StatusSourceRef],
        b.[IntakeSource],
        b.[IntakeSourceDetail],
        b.[SourceEmailMessageId]
    FROM dbo.[Bill] b
    LEFT JOIN dbo.[Vendor] v ON b.[VendorId] = v.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         b.[BillNumber] LIKE '%' + @SearchTerm + '%' OR
         b.[Memo] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), b.[BillDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), b.[DueDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), b.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR b.[VendorId] = @VendorId)
        AND (@StartDate IS NULL OR b.[BillDate] >= @StartDate)
        AND (@EndDate IS NULL OR b.[BillDate] <= @EndDate)
        AND (@IsDraft IS NULL OR b.[IsDraft] = @IsDraft)
        AND (@Status IS NULL OR b.[Status] = @Status)
        AND dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, b.[Id]) = 1
    ORDER BY
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'BillNumber' THEN b.[BillNumber] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'BillNumber' THEN b.[BillNumber] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'BillDate' THEN b.[BillDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'BillDate' THEN b.[BillDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'DueDate' THEN b.[DueDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'DueDate' THEN b.[DueDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'TotalAmount' THEN b.[TotalAmount] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'TotalAmount' THEN b.[TotalAmount] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'VendorId' THEN b.[VendorId] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'VendorId' THEN b.[VendorId] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE CountBills
(
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    -- U-445. The canonical filter. `@IsDraft` is kept because every
    -- existing caller still sends it; the two are consistent by
    -- CK_Bill_Status_IsDraft, so passing both is safe rather than
    -- contradictory.
    @Status NVARCHAR(20) = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT COUNT(*) AS [TotalCount]
    FROM dbo.[Bill] b
    LEFT JOIN dbo.[Vendor] v ON b.[VendorId] = v.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         b.[BillNumber] LIKE '%' + @SearchTerm + '%' OR
         b.[Memo] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), b.[BillDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), b.[DueDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), b.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR b.[VendorId] = @VendorId)
        AND (@StartDate IS NULL OR b.[BillDate] >= @StartDate)
        AND (@EndDate IS NULL OR b.[BillDate] <= @EndDate)
        AND (@IsDraft IS NULL OR b.[IsDraft] = @IsDraft)
        AND (@Status IS NULL OR b.[Status] = @Status)
        AND dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, b.[Id]) = 1;
    COMMIT TRANSACTION;
END;
GO

-- Get first line item's ProjectId for a batch of bills
CREATE OR ALTER PROCEDURE ReadBillFirstLineItemProjects
(
    @BillIds NVARCHAR(MAX)  -- comma-separated bill IDs
)
AS
BEGIN
    SELECT bli.BillId, bli.ProjectId
    FROM dbo.BillLineItem bli
    INNER JOIN (
        SELECT BillId, MIN(Id) AS FirstId
        FROM dbo.BillLineItem
        WHERE BillId IN (SELECT CAST(value AS BIGINT) FROM STRING_SPLIT(@BillIds, ','))
        GROUP BY BillId
    ) first ON bli.Id = first.FirstId;
END;
GO

-- ============================================================================
-- LinkBillSourceEmailMessage — idempotent backfill of Bill.SourceEmailMessageId.
-- Used by BillService.create() when a duplicate is detected: if the existing
-- Bill came in via a non-email path (e.g. bill_folder) and has no source
-- email linked, this stamps the link so the email-driven dedup audit trail is
-- preserved. Only updates when SourceEmailMessageId IS NULL (won't overwrite
-- an existing link to a different email). Returns the row when it updated,
-- empty when it didn't (already linked, or Bill doesn't exist).
-- ============================================================================

CREATE OR ALTER PROCEDURE LinkBillSourceEmailMessage
(
    @Id BIGINT,
    @SourceEmailMessageId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    UPDATE dbo.[Bill]
    SET [SourceEmailMessageId] = @SourceEmailMessageId,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[SourceEmailMessageId]
    WHERE [Id] = @Id AND [SourceEmailMessageId] IS NULL;

    COMMIT TRANSACTION;
END;
GO

-- ============================================================================
-- FinalizeBillById (U-434) — the ONLY sanctioned IsDraft 1 -> 0 transition.
-- ============================================================================
-- Replaces complete_bill's UpdateBillById round-trip, which carried @RowVersion
-- and therefore made finalization lose a race it has no business losing.
--
-- Finalization is a STATE TRANSITION, not a field edit. complete_bill built a
-- BillUpdate from the row it had just read and wrote every field back
-- unchanged, so the only real change was IsDraft — but the @RowVersion
-- predicate meant BillEdit's 300ms auto-save landing in between matched 0 rows
-- and failed the completion. (The 3-attempt retry loop written to absorb that
-- was unreachable: BillRepository.update_by_id RAISES on a 0-row UPDATE rather
-- than returning None, so its retry branch never ran and time.sleep(0.2) never
-- executed in prod. U-426 finding.)
--
-- No @RowVersion here, deliberately. The transition is guarded by IsDraft = 1
-- instead, which makes it IDEMPOTENT: a concurrent second Complete, or the
-- reclaim watchdog re-driving, flips nothing and is a no-op rather than a
-- conflict. An unrelated field edit racing this can no longer block it.
--
-- Contract, and why the SELECT is unconditional: the UPDATE matches 0 rows both
-- when the bill is ALREADY finalized and when it does not exist, which the
-- caller must distinguish. So the row is re-SELECTed regardless -- a row back
-- means "the bill exists and is now IsDraft=0" (whoever flipped it), no row back
-- means "no such Bill". SET NOCOUNT ON + that guaranteed terminal SELECT is the
-- pyodbc discipline (2026-06-11): without both, fetchone() reads a row-count
-- token as the first result set.
--
-- QboId/RealmId ARE projected here, unlike UpdateBillById, whose OUTPUT omits
-- them and hands callers qbo_id=None for a QBO-linked bill (separate U-426
-- finding, not fixed by this unit -- this sproc simply does not repeat it).
CREATE OR ALTER PROCEDURE FinalizeBillById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    -- U-445: writes BOTH columns. Not optional — CK_Bill_Status_IsDraft rejects
    -- any row where `Status = 'completed'` and `IsDraft = 1` disagree, so an
    -- IsDraft-only write here would now fail outright rather than drift.
    UPDATE dbo.[Bill]
    SET [IsDraft] = 0,
        [Status] = 'completed',
        [StatusDatetime] = SYSUTCDATETIME(),
        [StatusOrigin] = 'completion',
        [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [Id] = @Id AND [IsDraft] = 1;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId],
        [QboId],
        [RealmId]
    FROM dbo.[Bill]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- ReadBillQboLinkInfo — bill-level (one QBO bill per dbo.Bill in practice).
-- =====================================================================
-- U-363: reads dbo.Bill's own QboId/RealmId directly (U-355 — the sole
-- identity store for the header). Pre-U-363 this walked dbo.BillLineItem ->
-- qbo.BillLineItemBillLine -> qbo.BillLine -> qbo.Bill to reach the same
-- (QboId, RealmId) pair via a line item's mapping — a needless hop now that
-- the header carries its own identity. Output column names (QboId,
-- QboRealmId) are unchanged, so the caller (BillRepository.read_qbo_link_info)
-- needed no update. Returns empty if the bill has never been pushed to QBO.

CREATE OR ALTER PROCEDURE dbo.ReadBillQboLinkInfo (@BillId BIGINT)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        b.[QboId]    AS QboId,
        b.[RealmId]  AS QboRealmId
    FROM dbo.[Bill] b
    WHERE b.[Id] = @BillId
      AND b.[QboId] IS NOT NULL;
END;
GO

CREATE OR ALTER PROCEDURE SetBillQboIdentity
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
        UPDATE dbo.[Bill]
        SET [QboId] = NULL, [RealmId] = NULL, [SyncToken] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[Bill]
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

-- ---------------------------------------------------------------------------
-- U-445 — the one sanctioned way to move a Bill between lifecycle states.
--
-- `@FromStatuses` is a CSV of the states this transition is legal FROM, so the
-- guard is expressed by the caller and enforced here atomically rather than
-- read-then-written across a round trip. A row that no longer matches (someone
-- else moved it, or the row version is stale) yields an EMPTY result set —
-- this file's existing conflict contract, which `_from_db(None)` turns into
-- None for the caller. Never ROLLBACK inside a sproc: pyodbc runs
-- autocommit-off and an in-proc rollback raises 266 (CLAUDE.md, 2026-06-11).
--
-- Writes `IsDraft` too, because it is still a real column in this unit and
-- CK_Bill_Status_IsDraft will reject the row otherwise. U-446 removes that
-- line when IsDraft becomes computed.
-- ---------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE TransitionBillStatus
(
    @Id BIGINT,
    @RowVersion BINARY(8) = NULL,
    @FromStatuses NVARCHAR(200),
    @ToStatus NVARCHAR(20),
    @ActorUserId BIGINT = NULL,
    @Origin NVARCHAR(24) = NULL,
    @SourceRef NVARCHAR(64) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Bill]
    SET [Status] = @ToStatus,
        [StatusDatetime] = @Now,
        [StatusOrigin] = COALESCE(@Origin, 'user'),
        [StatusSourceRef] = COALESCE(@SourceRef, [StatusSourceRef]),
        [IsDraft] = CASE WHEN @ToStatus = 'completed' THEN 0 ELSE 1 END,
        [ModifiedDatetime] = @Now
    WHERE [Id] = @Id
      AND (@RowVersion IS NULL OR [RowVersion] = @RowVersion)
      AND [Status] IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(@FromStatuses, ','))
      -- Idempotent by construction: a transition to the state the row is
      -- already in matches nothing and returns the row unchanged below.
      AND [Status] <> @ToStatus;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId],
        [QboId],
        [RealmId]
    FROM dbo.[Bill]
    -- `= @ToStatus`, not just `= @Id` (Codex P1). An unconditional re-SELECT
    -- returns the row even when the UPDATE matched nothing — a stale
    -- @RowVersion, a status outside @FromStatuses — so the caller could not
    -- tell a refused transition from a successful one.
    --
    -- Keying the projection on the DESTINATION makes the contract "the bill is
    -- now in @ToStatus": a real move returns the row, a repeat transition
    -- returns it too (idempotent success), and a guarded miss or a deleted bill
    -- returns nothing. NB this is deliberately the OPPOSITE choice from
    -- FinalizeBillById, which re-SELECTs unconditionally so that "no row" means
    -- "bill gone" rather than "already finalized" (U-434) — there, the guard is
    -- the state itself; here it is the caller's @FromStatuses.
    WHERE [Id] = @Id AND [Status] = @ToStatus;

    COMMIT TRANSACTION;
END;


GO
