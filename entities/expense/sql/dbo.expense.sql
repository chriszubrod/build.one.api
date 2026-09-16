-- ===========================================================================
-- ⛔ APPLY THIS FILE AND entities/review/sql/dbo.review.sql IN **ONE**
--    TRANSACTION. NEITHER ORDER IS SAFE ON ITS OWN (U-467).
--
--    this file first  -> Expense stores Status, but the still-old CreateReview
--                        does not mirror it: a review created in that window
--                        is stamped 'draft' forever (the backfill's
--                        StatusDatetime guard is already satisfied and no
--                        re-apply repairs it).
--    review first     -> the new CreateReview Expense mirror names a column
--                        that does not exist yet: SQL error 207.
--
--    CreateExpense in this file omits [IsDraft] from its INSERT (a computed
--    column — naming one is SQL error 271). An old CreateExpense against the
--    swapped schema fails every POST /create/expense until this file is
--    applied.
--
--    scripts/run_sql.py commits per invocation — so applying them separately
--    means a live window with one of the two failures above. Apply both on
--    ONE connection with a single commit.
-- ===========================================================================
-- The CREATE TABLE block below omits IsCredit / SourceEmailMessageId /
-- CompanyId / CreatedByUserId (IsCredit and SourceEmailMessageId added later
-- by add_is_credit_column.sql / source_email_message_fk.sql; CompanyId and
-- CreatedByUserId are live and must never be touched here — CreatedByUserId
-- has an idempotent ALTER-ADD guard below, U-345). A from-scratch run would
-- otherwise build a table the CreateExpense INSERT then fails against.

IF OBJECT_ID('dbo.Expense', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Expense]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NOT NULL,
    [ExpenseDate] DATETIME2(3) NOT NULL,
    [ReferenceNumber] NVARCHAR(50) NOT NULL,
    [TotalAmount] DECIMAL(18,2) NULL,
    [Memo] NVARCHAR(MAX) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_Expense_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id])
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_Expense_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Expense_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD CONSTRAINT [FK_Expense_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Expense_VendorId' AND object_id = OBJECT_ID('dbo.Expense'))
BEGIN
CREATE INDEX IX_Expense_VendorId ON [dbo].[Expense] ([VendorId]);
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Expense_ExpenseDate' AND object_id = OBJECT_ID('dbo.Expense'))
BEGIN
CREATE INDEX IX_Expense_ExpenseDate ON [dbo].[Expense] ([ExpenseDate]);
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Expense_ReferenceNumber' AND object_id = OBJECT_ID('dbo.Expense'))
BEGIN
CREATE INDEX IX_Expense_ReferenceNumber ON [dbo].[Expense] ([ReferenceNumber]);
END
GO

-- Additive: QboId/RealmId/SyncToken (U-238a dbo-native identity) were added
-- out-of-band before this base file was made canonical — the base CREATE TABLE
-- above never declared them, which would abort a from-scratch build at
-- SetExpenseQboIdentity's (and now ReadExpenseByQboIdAndRealmId's) CREATE
-- PROCEDURE time (SQL error 207). Idempotent, no-op-safe against live —
-- columns + the unique index already exist there. Same gap/fix as U-277's
-- dbo.company.sql and this unit's dbo.bill.sql.
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'SyncToken')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [SyncToken] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Expense_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Expense')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Expense_QboId_RealmId ON [dbo].[Expense] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- ===========================================================================
-- U-467 (U-357 Phase 3, LS-03c) — the canonical `Status` column, shipped as
-- ONE slice straight to the end state (U-445 + U-446 together).
--
-- Expense had no status of its own. U-457 DERIVED one per request from
-- `IsDraft x the latest Review row`, which was correct but unfilterable: you
-- cannot post-filter a paginated page without making `count` lie. This is the
-- stored column the `?status=` filter — and the Expenses page tabs — need.
--
-- ORDER BELOW IS LOAD-BEARING. `Status` defaults to 'draft', so the moment the
-- column exists all 11,743 completed expenses read 'draft' and
-- CK_Expense_Status_IsDraft would be violated. Columns, THEN backfill, THEN
-- constraints — and the CHECKs go on WITH CHECK so SQL Server validates all
-- 11,753 existing rows, which is itself the proof that the backfill was right.
--
-- `IsDraft` is then retired in the same file: drop + re-add as PERSISTED
-- computed. Dual-writing is what removes the old-image deploy window: the
-- in-file CK_Expense_Status_IsDraft makes drift between the two impossible at
-- the database level for the duration of the apply, then is dropped because
-- it is tautological once IsDraft derives from Status.
-- ===========================================================================
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'Status')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [Status] NVARCHAR(20) NOT NULL
        CONSTRAINT [DF_Expense_Status] DEFAULT ('draft');
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'StatusDatetime')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [StatusDatetime] DATETIME2(3) NULL;
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'StatusOrigin')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [StatusOrigin] NVARCHAR(24) NOT NULL
        CONSTRAINT [DF_Expense_StatusOrigin] DEFAULT ('user');
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'StatusSourceRef')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [StatusSourceRef] NVARCHAR(64) NULL;
END
GO

-- Backfill. Guarded on "no row has been stamped yet" so a re-apply of this base
-- file (they are re-run routinely) can never overwrite live status transitions.
-- Set-based per feedback_backfill_setbased_under_load — a per-row loop over 11k
-- rows TCP-drops under load.
--
-- NB the batching here is about statement size, NOT commit granularity:
-- scripts/run_sql.py executes every GO-batch on ONE connection and commits once
-- at the end, so the whole file is a single transaction and a failure rolls all
-- of it back. That is the right property for a backfill+constraint pair (no
-- half-stamped state can survive), and resumability comes from the
-- StatusDatetime guard across INVOCATIONS rather than within one.
--
-- Prefers the frozen [ReviewKind] exactly as the resolver does, falling back
-- to flags only when the column is NULL.
-- Guarded on the JOINED tables existing, and executed through sp_executesql
-- (Codex P1). dbo.[Review] carries FK_Review_Expense so it CANNOT exist before
-- this file has run, and an IF guard alone does NOT help: SQL Server defers
-- name resolution for stored-procedure BODIES, not for ad-hoc batches — this
-- batch compiles as a unit, so a missing table errors at compile time before
-- the IF is ever evaluated. Because run_sql.py commits the whole file as ONE
-- transaction, that would roll back the entire Status schema rather than just
-- the backfill. Same pattern as CountReviewStatusReferencesById (U-444).
--
-- A fresh database has nothing to backfill anyway: every Expense it goes on to
-- create takes its Status from CreateExpense.
-- Census 2026-09-16 (read-only): 11,753 rows; 11,743 IsDraft=0 (all have QboId
-- and RealmId) → completed/qbo_pull; 10 IsDraft=1 (no QboId) → draft/backfill.
-- dbo.Review has 0 Expense rows; dbo.CompletionJob has 0 Expense rows. The
-- completion origin branch is still written because CompletionJob will
-- accumulate Expense rows going forward.
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.ReviewStatus', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.CompletionJob', 'U') IS NOT NULL
   AND EXISTS (SELECT 1 FROM dbo.[Expense])
   AND NOT EXISTS (SELECT 1 FROM dbo.[Expense] WHERE [StatusDatetime] IS NOT NULL)
BEGIN
    EXEC sp_executesql N'
    DECLARE @Batch INT = 5000;
    DECLARE @Done INT = 1;

    WHILE @Done > 0
    BEGIN
        UPDATE TOP (@Batch) e
        SET e.[Status] = CASE
                WHEN e.[IsDraft] = 0            THEN ''completed''
                WHEN cur.[Kind] IS NOT NULL THEN cur.[Kind]
                ELSE ''draft''
            END,
            e.[StatusDatetime] = SYSUTCDATETIME(),
            e.[StatusOrigin] = CASE
                WHEN e.[IsDraft] = 0 AND EXISTS (
                    SELECT 1 FROM dbo.[CompletionJob] cj
                    WHERE cj.[EntityType] = ''Expense''
                      AND cj.[EntityPublicId] = e.[PublicId]
                      AND cj.[Status] = ''completed''
                ) THEN ''completion''
                WHEN e.[QboId] IS NOT NULL THEN ''qbo_pull''
                ELSE ''backfill''
            END,
            e.[StatusSourceRef] = CASE
                WHEN e.[QboId] IS NOT NULL AND e.[RealmId] IS NOT NULL
                    THEN CONCAT(''qbo:'', e.[RealmId], ''/'', e.[QboId])
                ELSE NULL
            END
        FROM dbo.[Expense] e
        OUTER APPLY (
            SELECT TOP 1 r.[ExpenseId],
                   COALESCE(r.[ReviewKind],
                            CASE WHEN rs.[IsDeclined] = 1 THEN N''declined''
                                 WHEN rs.[IsFinal]    = 1 THEN N''approved''
                                 WHEN rs.[IsInitial]  = 1 THEN N''submitted''
                                 ELSE N''in_review'' END) AS [Kind]
            FROM dbo.[Review] r
            INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = r.[ReviewStatusId]
            WHERE r.[ExpenseId] = e.[Id]
            ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC
        ) cur
        WHERE e.[StatusDatetime] IS NULL;

        SET @Done = @@ROWCOUNT;
    END';
END
GO

-- Constraints LAST — see the ordering note above. WITH CHECK (the default for
-- ALTER ... ADD CONSTRAINT, stated explicitly here because it is the point)
-- validates every existing row, so applying this file IS the parity proof.
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Expense_Status')
BEGIN
    ALTER TABLE [dbo].[Expense] WITH CHECK ADD CONSTRAINT [CK_Expense_Status]
        CHECK ([Status] IN ('draft','submitted','in_review','approved','declined','completed'));
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Expense_StatusOrigin')
BEGIN
    ALTER TABLE [dbo].[Expense] WITH CHECK ADD CONSTRAINT [CK_Expense_StatusOrigin]
        CHECK ([StatusOrigin] IN ('user','completion','qbo_pull','fast_path','backfill'));
END
GO

-- THE safety property of the apply. While both columns are really written,
-- this makes them incapable of disagreeing — so the apply cannot leave a
-- drifted row. Guarded on IsDraft still being a REAL column (Codex P2). The
-- swap below drops this constraint for good — it is tautological once IsDraft
-- derives from Status — but this block runs EARLIER in the file, so on a
-- second apply it would see the constraint absent and cheerfully recreate it:
-- legal on a persisted computed column, silently contradicting "dropped for
-- good", and re-validating 11k rows every time the file is re-run.
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'IsDraft'
                 AND is_computed = 0)
   AND NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Expense_Status_IsDraft')
BEGIN
    ALTER TABLE [dbo].[Expense] WITH CHECK ADD CONSTRAINT [CK_Expense_Status_IsDraft]
        CHECK ((CASE WHEN [Status] = 'completed' THEN 0 ELSE 1 END) = [IsDraft]);
END
GO

-- Filtered: 10 of 11,753 rows are non-completed today, so this index IS the
-- working set for every tab except Billed (which is the paginated scan it
-- already was).
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Expense_Status' AND object_id = OBJECT_ID('dbo.Expense'))
BEGIN
    -- Do not INCLUDE [IsDraft]: it becomes computed FROM [Status], which is
    -- this index's key, so covering it would store a column the index could
    -- already derive — and an INCLUDE naming it blocks the column swap.
    CREATE NONCLUSTERED INDEX [IX_Expense_Status] ON [dbo].[Expense] ([Status])
        INCLUDE ([VendorId], [ExpenseDate])
        WHERE [Status] <> 'completed';
END
GO

-- ===========================================================================
-- U-467 — retire [IsDraft]. It becomes a PERSISTED COMPUTED column derived
-- from [Status], so it can be READ exactly as before and can no longer be
-- written at all. Shipped in the same unit as the column add (Bill's U-445 +
-- U-446 as one slice).
--
-- ORDER IS FORCED. Three objects reference the column and each blocks the
-- drop:
--   IX_Expense_Status          (just created above) -> dropped, recreated after
--   CK_Expense_Status_IsDraft  compared it          -> dropped FOR GOOD, it is
--                                                      tautological once the
--                                                      column derives from
--                                                      Status
--   DF__Expense__IsDraft__*    SQL-Server-named default -> found via
--                                                       sys.default_constraints
--                                                       (live name
--                                                       DF__Expense__IsDraft__367CE370
--                                                       — never hardcode it)
--
-- Verified before writing this: no WITH SCHEMABINDING module binds IsDraft
-- (dbo.UserCanAccessExpense is schemabound but does not reference it), so
-- nothing else stands in the way. No index, expression dependency, CHECK, or
-- user statistic on IsDraft (census 2026-09-16).
--
-- NOTE the swap rewrites the table under a schema-modification lock, so live
-- callers BLOCK for its duration. Bill's 20k rows stalled reads 3.5-10s;
-- Expense has 11.7k. Apply it when a similar stall on Expense reads is
-- acceptable. It also moves IsDraft to the END of the column order; every
-- reader here binds pyodbc rows by NAME, so that is inert.
-- ===========================================================================
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'IsDraft'
                 AND is_computed = 0)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_Expense_Status' AND object_id = OBJECT_ID('dbo.Expense'))
        DROP INDEX [IX_Expense_Status] ON [dbo].[Expense];

    IF EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'CK_Expense_Status_IsDraft'
                 AND parent_object_id = OBJECT_ID('dbo.Expense'))
        ALTER TABLE [dbo].[Expense] DROP CONSTRAINT [CK_Expense_Status_IsDraft];

    DECLARE @IsDraftDefault SYSNAME = (
        SELECT dc.name
        FROM sys.default_constraints dc
        JOIN sys.columns c
          ON c.object_id = dc.parent_object_id AND c.column_id = dc.parent_column_id
        WHERE dc.parent_object_id = OBJECT_ID('dbo.Expense') AND c.name = 'IsDraft'
    );
    -- Built into a variable, not concatenated inside EXEC(): SQL Server does
    -- not allow a function call in EXEC()'s argument, which a PARSEONLY check
    -- caught before this ever reached prod.
    IF @IsDraftDefault IS NOT NULL
    BEGIN
        DECLARE @DropDefaultSql NVARCHAR(400) =
            N'ALTER TABLE [dbo].[Expense] DROP CONSTRAINT ' + QUOTENAME(@IsDraftDefault);
        EXEC sp_executesql @DropDefaultSql;
    END

    ALTER TABLE [dbo].[Expense] DROP COLUMN [IsDraft];

    -- PERSISTED so it can be indexed and is not recomputed per read.
    --
    -- `NOT NULL` is explicit and load-bearing. Without it SQL Server marks a
    -- computed column NULLABLE — it will not prove a CASE expression total —
    -- which would have quietly widened `BIT NOT NULL` to `BIT NULL` for every
    -- reader. Measured on Bill against prod: with the clause, `is_nullable = 0`,
    -- matching the column being replaced exactly.
    ALTER TABLE [dbo].[Expense] ADD [IsDraft] AS (
        CASE WHEN [Status] = 'completed' THEN CAST(0 AS BIT) ELSE CAST(1 AS BIT) END
    ) PERSISTED NOT NULL;

    CREATE NONCLUSTERED INDEX [IX_Expense_Status] ON [dbo].[Expense] ([Status])
        INCLUDE ([VendorId], [ExpenseDate])
        WHERE [Status] <> 'completed';
END
GO

-- Unique constraint to prevent duplicate ReferenceNumber for the same VendorId
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_Expense_VendorId_ReferenceNumber' AND parent_object_id = OBJECT_ID('dbo.Expense'))
BEGIN
ALTER TABLE [dbo].[Expense]
ADD CONSTRAINT UQ_Expense_VendorId_ReferenceNumber UNIQUE ([VendorId], [ReferenceNumber]);
END
GO

GO

-- NOTE: kept in sync with entities/expense/sql/migrations/001_expense_source_email.sql
-- and scripts/migrations/gap2_core_threading.sql. Carries @CreatedByUserId
-- (Gap 2 attribution) + @SourceEmailMessageId (receipt-intake source trail).
CREATE OR ALTER PROCEDURE CreateExpense
(
    @VendorId BIGINT,
    @ExpenseDate DATETIME2(3),
    @ReferenceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1,
    @IsCredit BIT = 0,
    @SourceEmailMessageId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL,
    -- U-467. Defaulted so every existing caller keeps working unchanged.
    -- NULL @Status means "derive it from @IsDraft", which is what the ~11k
    -- rows created before this unit effectively did.
    @Status NVARCHAR(20) = NULL,
    @StatusOrigin NVARCHAR(24) = NULL,
    @StatusSourceRef NVARCHAR(64) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Expense]
        ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ExpenseDate],
         [ReferenceNumber], [TotalAmount], [Memo],
         -- U-467: [IsDraft] is gone from this list. An INSERT that names a
         -- computed column is SQL Server error 271; `@IsDraft` is KEPT as a
         -- no-op parameter so older callers still bind successfully.
         [Status], [StatusDatetime], [StatusOrigin], [StatusSourceRef],
         [IsCredit], [SourceEmailMessageId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        CONVERT(VARCHAR(19), INSERTED.[ExpenseDate], 120) AS [ExpenseDate],
        INSERTED.[ReferenceNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft],
        INSERTED.[Status],
        INSERTED.[StatusDatetime],
        INSERTED.[StatusOrigin],
        INSERTED.[StatusSourceRef],
        INSERTED.[IsCredit],
        INSERTED.[SourceEmailMessageId]
    VALUES (@Now, @Now, @VendorId, @ExpenseDate, @ReferenceNumber, @TotalAmount,
            @Memo,
            -- @IsDraft survives only as the fallback for a caller that has
            -- not learned @Status yet. Nothing writes IsDraft itself.
            COALESCE(@Status, CASE WHEN @IsDraft = 0 THEN 'completed' ELSE 'draft' END),
            @Now,
            COALESCE(@StatusOrigin, 'user'),
            @StatusSourceRef,
            @IsCredit, @SourceEmailMessageId,
            COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenses
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        e.[Id],
        e.[PublicId],
        e.[RowVersion],
        CONVERT(VARCHAR(19), e.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), e.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        e.[VendorId],
        CONVERT(VARCHAR(19), e.[ExpenseDate], 120) AS [ExpenseDate],
        e.[ReferenceNumber],
        e.[TotalAmount],
        e.[Memo],
        e.[IsDraft],
        e.[Status],
        e.[StatusDatetime],
        e.[StatusOrigin],
        e.[StatusSourceRef],
        e.[IsCredit]
    FROM dbo.[Expense] e
    WHERE dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1
    ORDER BY e.[ExpenseDate] DESC, e.[ReferenceNumber] ASC;
    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseById
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
        CONVERT(VARCHAR(19), [ExpenseDate], 120) AS [ExpenseDate],
        [ReferenceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IsCredit],
        [QboId],
        [RealmId]
    FROM dbo.[Expense]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- U-283b (Phase-4): direct dbo-native identity lookup, mirrors dbo.bill.sql's
-- ReadBillByQboIdAndRealmId (U-283) / dbo.customer.sql's / dbo.project.sql's.
-- Lets the Purchase connector resolve "does a dbo.Expense already exist for
-- this external QBO id" WITHOUT hopping through the qbo.PurchaseExpense
-- mapping table — every Expense synced at least once already carries
-- QboId/RealmId via SetExpenseQboIdentity, so this is the steady-state fast
-- path; the mapping-table lookup remains as a fallback for rows that predate
-- identity stamping. RBAC-scoped via the existing UserCanAccessExpense UDF,
-- like every other Expense read.
CREATE OR ALTER PROCEDURE ReadExpenseByQboIdAndRealmId
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
        e.[Id],
        e.[PublicId],
        e.[RowVersion],
        CONVERT(VARCHAR(19), e.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), e.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        e.[VendorId],
        CONVERT(VARCHAR(19), e.[ExpenseDate], 120) AS [ExpenseDate],
        e.[ReferenceNumber],
        e.[TotalAmount],
        e.[Memo],
        e.[IsDraft],
        e.[Status],
        e.[StatusDatetime],
        e.[StatusOrigin],
        e.[StatusSourceRef],
        e.[IsCredit],
        e.[QboId],
        e.[RealmId]
    FROM dbo.[Expense] e
    WHERE e.[QboId] = @QboId
      AND ((e.[RealmId] = @RealmId) OR (e.[RealmId] IS NULL AND @RealmId IS NULL))
      AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1;

    COMMIT TRANSACTION;
END;
GO

-- U-298 (Wave-1): bulk sibling of ReadExpenseByQboIdAndRealmId above — the set
-- of QboIds already stamped on dbo.Expense for a realm. Lets
-- scripts/sync_qbo_purchase.py's dry-run preview classify create-vs-update
-- against the SAME identity the connector actually resolves by (one query),
-- instead of qbo.Purchase staging-row existence, which can diverge from it
-- (e.g. a staging row surviving an Expense create that failed/rolled back on
-- a prior tick). RBAC-scoped via the existing UserCanAccessExpense UDF, like
-- every other Expense read.
-- U-301a: additive [Id] column — the reconciliation void detector needs the
-- dbo.Expense.Id alongside QboId (its issue-detail message references the
-- local row directly, and detect_void_absent_candidates's local_rows contract
-- needs a real object per row, not a bare string). Existing callers reading
-- by column name (ExpenseRepository.read_qbo_ids_by_realm_id) are unaffected.
CREATE OR ALTER PROCEDURE ReadExpenseQboIdsByRealmId
(
    @RealmId NVARCHAR(50),
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT e.[Id], e.[QboId]
    FROM dbo.[Expense] e
    WHERE e.[RealmId] = @RealmId
      AND e.[QboId] IS NOT NULL
      AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseByPublicId
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
        CONVERT(VARCHAR(19), [ExpenseDate], 120) AS [ExpenseDate],
        [ReferenceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IsCredit],
        [QboId],
        [RealmId]
    FROM dbo.[Expense]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseByReferenceNumberAndVendorId
(
    @ReferenceNumber NVARCHAR(50),
    @VendorId BIGINT
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
        CONVERT(VARCHAR(19), [ExpenseDate], 120) AS [ExpenseDate],
        [ReferenceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IsCredit]
    FROM dbo.[Expense]
    WHERE [ReferenceNumber] = @ReferenceNumber AND [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE UpdateExpenseById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT,
    @ExpenseDate DATETIME2(3),
    @ReferenceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = NULL,
    @IsCredit BIT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Expense]
    SET
        [ModifiedDatetime] = @Now,
        [VendorId] = @VendorId,
        [ExpenseDate] = @ExpenseDate,
        [ReferenceNumber] = @ReferenceNumber,
        [TotalAmount] = @TotalAmount,
        [Memo] = @Memo,
        -- U-467 compat translation. Callers that predate Status — an old API
        -- image mid-deploy, the QBO pull connectors, a queued iOS PUT — still
        -- send only @IsDraft. So:
        --   @IsDraft = 0  ->  also move Status to 'completed'
        --   @IsDraft = 1  ->  NEUTRALISED. Nothing un-completes an expense
        --                     through a field update. The only legitimate 1
        --                     is on a row that is already a draft, where it
        --                     is a no-op anyway.
        -- No [IsDraft] assignment. It is a PERSISTED COMPUTED column now, and
        -- naming one on the left of a SET is an error — so the compat
        -- translation writes only Status and IsDraft follows automatically.
        -- The `@IsDraft = 1` neutralisation is implicit: the CASE below fires
        -- only on 0, so 1 cannot un-complete anything.
        [Status] = CASE
            WHEN @IsDraft = 0 AND [Status] <> 'completed' THEN 'completed'
            ELSE [Status] END,
        [StatusDatetime] = CASE
            WHEN @IsDraft = 0 AND [Status] <> 'completed' THEN @Now
            ELSE [StatusDatetime] END,
        [StatusOrigin] = CASE
            WHEN @IsDraft = 0 AND [Status] <> 'completed' THEN 'completion'
            ELSE [StatusOrigin] END,
        [IsCredit] = CASE WHEN @IsCredit IS NULL THEN [IsCredit] ELSE @IsCredit END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        CONVERT(VARCHAR(19), INSERTED.[ExpenseDate], 120) AS [ExpenseDate],
        INSERTED.[ReferenceNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft],
        INSERTED.[Status],
        INSERTED.[StatusDatetime],
        INSERTED.[StatusOrigin],
        INSERTED.[StatusSourceRef],
        INSERTED.[IsCredit]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

-- U-467. Idempotent finalize: write Status='completed' (origin completion)
-- where the row is not already there. No @RowVersion — an unrelated concurrent
-- field edit must not be able to fail completion. Presence-not-rowcount
-- contract: the UPDATE matches 0 rows both when the expense is ALREADY
-- finalized and when it does not exist, so the row is re-SELECTed regardless.
-- SET NOCOUNT ON + that guaranteed terminal SELECT is the pyodbc discipline
-- (2026-06-11). Never assign [IsDraft] — it is computed.
-- QboId/RealmId ARE projected here, unlike UpdateExpenseById.
CREATE OR ALTER PROCEDURE FinalizeExpenseById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    UPDATE dbo.[Expense]
    SET [Status] = 'completed',
        [StatusDatetime] = SYSUTCDATETIME(),
        [StatusOrigin] = 'completion',
        [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [Id] = @Id AND [Status] <> 'completed';

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        CONVERT(VARCHAR(19), [ExpenseDate], 120) AS [ExpenseDate],
        [ReferenceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IsCredit],
        [QboId],
        [RealmId]
    FROM dbo.[Expense]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE DeleteExpenseById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Expense]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        CONVERT(VARCHAR(19), DELETED.[ExpenseDate], 120) AS [ExpenseDate],
        DELETED.[ReferenceNumber],
        DELETED.[TotalAmount],
        DELETED.[Memo],
        DELETED.[IsDraft],
        DELETED.[Status],
        DELETED.[StatusDatetime],
        DELETED.[StatusOrigin],
        DELETED.[StatusSourceRef],
        DELETED.[IsCredit]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- Pagination and filtering procedures
GO

CREATE OR ALTER PROCEDURE ReadExpensesPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @IsCredit BIT = NULL,
    -- U-467. The canonical filter. `@IsDraft` is kept because every
    -- existing caller still sends it; IsDraft is computed from Status, so
    -- passing both is safe rather than contradictory.
    @Status NVARCHAR(20) = NULL,
    @SortBy NVARCHAR(50) = 'ExpenseDate',
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    -- U-447 (ported): MANDATORY now. This sproc used to be a single bare
    -- SELECT, which survived without it; the INSERT below emits a row-count
    -- token that would arrive as the first "result" and make cursor.fetchall()
    -- return the wrong thing (CLAUDE.md, pyodbc result-set discipline,
    -- 2026-06-11).
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;
    DECLARE @SortColumn NVARCHAR(50) = CASE @SortBy
        WHEN 'ReferenceNumber' THEN 'ReferenceNumber'
        WHEN 'ExpenseDate' THEN 'ExpenseDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'VendorId' THEN 'VendorId'
        ELSE 'ExpenseDate'
    END;
    DECLARE @SortDir NVARCHAR(4) = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

    -- ======================================================================
    -- U-447 (ported U-467) — the page and its total now come from ONE
    -- materialized set.
    --
    -- They used to be two sprocs on two round trips, so an expense finalized
    -- between them put `data` and `count` in different snapshots. Merging the
    -- two SELECTs into one sproc would NOT have fixed that — this database
    -- runs READ_COMMITTED_SNAPSHOT, where every STATEMENT takes its own
    -- snapshot even inside one explicit transaction. The fix has to be a set
    -- both reads share, not a shared transaction.
    --
    -- Materializes the ROWS, not just their ids (Codex P1). Materializing ids
    -- alone did not work: the page then re-read live dbo.[Expense] through a
    -- JOIN, which is a NEW statement and therefore a NEW RCSI snapshot.
    --
    -- Nothing below this statement touches dbo.[Expense] again. That is the
    -- property, and tests assert it.
    SELECT
        e.[Id],
        e.[PublicId],
        e.[RowVersion],
        CONVERT(VARCHAR(19), e.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), e.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        e.[VendorId],
        CONVERT(VARCHAR(19), e.[ExpenseDate], 120) AS [ExpenseDate],
        e.[ReferenceNumber],
        e.[TotalAmount],
        e.[Memo],
        e.[IsDraft],
        e.[Status],
        e.[StatusDatetime],
        e.[StatusOrigin],
        e.[StatusSourceRef],
        e.[IsCredit],
        -- kept raw for ORDER BY; the CONVERTed copy above is what ships
        e.[ExpenseDate] AS [SortExpenseDate]
    INTO #FilteredExpenses
    FROM dbo.[Expense] e
    LEFT JOIN dbo.[Vendor] v ON e.[VendorId] = v.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         e.[ReferenceNumber] LIKE '%' + @SearchTerm + '%' OR
         e.[Memo] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), e.[ExpenseDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), e.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR e.[VendorId] = @VendorId)
        AND (@StartDate IS NULL OR e.[ExpenseDate] >= @StartDate)
        AND (@EndDate IS NULL OR e.[ExpenseDate] <= @EndDate)
        AND (@IsDraft IS NULL OR e.[IsDraft] = @IsDraft)
        AND (@IsCredit IS NULL OR e.[IsCredit] = @IsCredit)
        AND (@Status IS NULL OR e.[Status] = @Status)
        AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1;


    SELECT
        [Id], [PublicId], [RowVersion], [CreatedDatetime], [ModifiedDatetime],
        [VendorId], [ExpenseDate], [ReferenceNumber],
        [TotalAmount], [Memo], [IsDraft], [Status], [StatusDatetime],
        [StatusOrigin], [StatusSourceRef], [IsCredit]
    FROM #FilteredExpenses
    ORDER BY
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'ReferenceNumber' THEN [ReferenceNumber] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ReferenceNumber' THEN [ReferenceNumber] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'ExpenseDate' THEN [SortExpenseDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ExpenseDate' THEN [SortExpenseDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'TotalAmount' THEN [TotalAmount] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'TotalAmount' THEN [TotalAmount] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'VendorId' THEN [VendorId] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'VendorId' THEN [VendorId] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;

    -- Same materialized set. Cannot disagree with the page above.
    SELECT COUNT(*) AS [TotalCount] FROM #FilteredExpenses;

    DROP TABLE #FilteredExpenses;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE CountExpenses
(
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @IsCredit BIT = NULL,
    -- U-467. The canonical filter. `@IsDraft` is kept because every
    -- existing caller still sends it; IsDraft is computed from Status, so
    -- passing both is safe rather than contradictory.
    @Status NVARCHAR(20) = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT COUNT(*) AS [TotalCount]
    FROM dbo.[Expense] e
    LEFT JOIN dbo.[Vendor] v ON e.[VendorId] = v.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         e.[ReferenceNumber] LIKE '%' + @SearchTerm + '%' OR
         e.[Memo] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), e.[ExpenseDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), e.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR e.[VendorId] = @VendorId)
        AND (@StartDate IS NULL OR e.[ExpenseDate] >= @StartDate)
        AND (@EndDate IS NULL OR e.[ExpenseDate] <= @EndDate)
        AND (@IsDraft IS NULL OR e.[IsDraft] = @IsDraft)
        AND (@IsCredit IS NULL OR e.[IsCredit] = @IsCredit)
        AND (@Status IS NULL OR e.[Status] = @Status)
        AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE SetExpenseQboIdentity
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
        UPDATE dbo.[Expense]
        SET [QboId] = NULL, [RealmId] = NULL, [SyncToken] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[Expense]
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
-- U-467 — the one sanctioned way to move an Expense between lifecycle states.
--
-- `@FromStatuses` is a CSV of the states this transition is legal FROM, so the
-- guard is expressed by the caller and enforced here atomically rather than
-- read-then-written across a round trip. A row that no longer matches (someone
-- else moved it, or the row version is stale) yields an EMPTY result set —
-- this file's existing conflict contract. Never ROLLBACK inside a sproc:
-- pyodbc runs autocommit-off and an in-proc rollback raises 266 (CLAUDE.md,
-- 2026-06-11).
--
-- Sproc only, no Python caller, matching Bill.
-- ---------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE TransitionExpenseStatus
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

    UPDATE dbo.[Expense]
    SET [Status] = @ToStatus,
        [StatusDatetime] = @Now,
        [StatusOrigin] = COALESCE(@Origin, 'user'),
        [StatusSourceRef] = COALESCE(@SourceRef, [StatusSourceRef]),
        [ModifiedDatetime] = @Now
    WHERE [Id] = @Id
      AND (@RowVersion IS NULL OR [RowVersion] = @RowVersion)
      AND [Status] IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(@FromStatuses, ','))
      -- Idempotent by construction: a transition to the state the row is
      -- already in matches nothing and returns the row unchanged below.
      AND [Status] <> @ToStatus
      -- U-446c (Codex P1), ported. `completed` is TERMINAL: this sproc takes
      -- its allowed source states from the CALLER, so passing
      -- @FromStatuses = 'completed' would reopen a finalised Expense. No
      -- repository calls it that way today; the fence makes it uncallable that
      -- way at all. Completion itself goes through FinalizeExpenseById, which
      -- does not use this path.
      AND [Status] <> 'completed';

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        CONVERT(VARCHAR(19), [ExpenseDate], 120) AS [ExpenseDate],
        [ReferenceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [Status],
        [StatusDatetime],
        [StatusOrigin],
        [StatusSourceRef],
        [IsCredit],
        [QboId],
        [RealmId]
    FROM dbo.[Expense]
    -- `= @ToStatus`, not just `= @Id` (Codex P1). An unconditional re-SELECT
    -- returns the row even when the UPDATE matched nothing — a stale
    -- @RowVersion, a status outside @FromStatuses — so the caller could not
    -- tell a refused transition from a successful one.
    --
    -- Keying the projection on the DESTINATION makes the contract "the expense
    -- is now in @ToStatus": a real move returns the row, a repeat transition
    -- returns it too (idempotent success), and a guarded miss or a deleted
    -- expense returns nothing. NB this is deliberately the OPPOSITE choice
    -- from FinalizeExpenseById, which re-SELECTs unconditionally so that "no
    -- row" means "expense gone" rather than "already finalized" — there, the
    -- guard is the state itself; here it is the caller's @FromStatuses.
    WHERE [Id] = @Id AND [Status] = @ToStatus;

    COMMIT TRANSACTION;
END;
GO
