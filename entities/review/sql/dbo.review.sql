GO

IF OBJECT_ID('dbo.Review', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Review]
(
    [Id]               BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId]         UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion]       ROWVERSION NOT NULL,
    [CreatedDatetime]  DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ReviewStatusId]   BIGINT NOT NULL,
    [UserId]           BIGINT NOT NULL,
    [Comments]         NVARCHAR(MAX) NULL,
    [BillId]           BIGINT NULL,
    [ExpenseId]        BIGINT NULL,
    [BillCreditId]     BIGINT NULL,
    [InvoiceId]        BIGINT NULL,
    [ContractLaborId]  BIGINT NULL,
    CONSTRAINT [FK_Review_ReviewStatus] FOREIGN KEY ([ReviewStatusId]) REFERENCES dbo.[ReviewStatus]([Id]),
    CONSTRAINT [FK_Review_User]         FOREIGN KEY ([UserId])         REFERENCES dbo.[User]([Id]),
    CONSTRAINT [FK_Review_Bill]         FOREIGN KEY ([BillId])         REFERENCES dbo.[Bill]([Id]),
    CONSTRAINT [FK_Review_Expense]      FOREIGN KEY ([ExpenseId])      REFERENCES dbo.[Expense]([Id]),
    CONSTRAINT [FK_Review_BillCredit]   FOREIGN KEY ([BillCreditId])   REFERENCES dbo.[BillCredit]([Id]),
    CONSTRAINT [FK_Review_Invoice]      FOREIGN KEY ([InvoiceId])      REFERENCES dbo.[Invoice]([Id]),
    CONSTRAINT [FK_Review_ContractLabor] FOREIGN KEY ([ContractLaborId]) REFERENCES dbo.[ContractLabor]([Id]),
    CONSTRAINT [CK_Review_OneParent] CHECK (
        (CASE WHEN [BillId]       IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [ExpenseId]    IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [BillCreditId] IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [InvoiceId]    IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [ContractLaborId] IS NOT NULL THEN 1 ELSE 0 END) = 1
    )
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.Review') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[Review] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_Review_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Review_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[Review] ADD CONSTRAINT [FK_Review_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


-- U-357b: ContractLabor as the 5th Review parent — live in prod since
-- migrations/003_add_contract_labor_parent.sql (2026-05-28); folded into the base so a
-- from-scratch build and the live schema agree (the base was 4-parent while the
-- ContractLabor read/delete sprocs below already filter on [ContractLaborId]).
-- No-op against the live schema (column / FK / 5-way CK already exist there).
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.Review', 'ContractLaborId') IS NULL
BEGIN
    ALTER TABLE [dbo].[Review] ADD [ContractLaborId] BIGINT NULL;
END
GO
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.ContractLabor', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Review_ContractLabor')
BEGIN
    ALTER TABLE [dbo].[Review] ADD CONSTRAINT [FK_Review_ContractLabor]
        FOREIGN KEY ([ContractLaborId]) REFERENCES [dbo].[ContractLabor]([Id]);
END
GO
-- CK_Review_OneParent must count all five parents. Recreate ONLY when the live
-- definition predates ContractLabor (4-parent); a correct 5-way CK is left alone.
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'CK_Review_OneParent'
                 AND parent_object_id = OBJECT_ID('dbo.Review')
                 AND definition NOT LIKE '%ContractLaborId%')
BEGIN
    ALTER TABLE [dbo].[Review] DROP CONSTRAINT [CK_Review_OneParent];
END
GO
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.check_constraints
                   WHERE name = 'CK_Review_OneParent' AND parent_object_id = OBJECT_ID('dbo.Review'))
BEGIN
    ALTER TABLE [dbo].[Review] ADD CONSTRAINT [CK_Review_OneParent] CHECK (
        (CASE WHEN [BillId]          IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [ExpenseId]       IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [BillCreditId]    IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [InvoiceId]       IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [ContractLaborId] IS NOT NULL THEN 1 ELSE 0 END) = 1
    );
END
GO

-- =========================================================================
-- U-455: [ReviewKind] -- the review's kind, FROZEN at insert
-- =========================================================================
--
-- The kind was derived at READ time from the ReviewStatus flags
-- (`shared/lifecycle/resolver.py::review_kind_from_flags`), which makes stored
-- history a function of current configuration. `UpdateReviewStatus` clears
-- [IsInitial] from every other status when the initial role is transferred
-- (`dbo.review_status.sql`: `UPDATE ... SET [IsInitial] = 0 WHERE [IsInitial] = 1
-- AND [Id] <> @Id`), so moving that flag silently relabels all 764 stored
-- `submitted` rows as `in_review` on the wire, and orphans every historical
-- submission from the inbox's submitter resolution (U-453's residual).
--
-- U-444 fixed the same class once already, moving the boundary from POSITION
-- (SortOrder) to a FLAG. That removed one failure mode and left the other: a
-- flag is still current config, and history must not be.
--
-- ⏳ THE BACKFILL BELOW IS ONLY CORRECT WHILE THE FLAGS ARE UNMOVED. It derives
-- each row's kind from the status's CURRENT flags, which is exactly what the
-- read path does today -- so it reproduces today's answers precisely. Once an
-- admin moves a flag the true history is unrecoverable, which is why this
-- column is being added now rather than when it is next convenient.
--
-- Precedence is IsDeclined -> IsFinal -> IsInitial -> in_review, matching
-- `review_kind_from_flags` exactly. Kept in lockstep by
-- tests/test_u455_review_kind_frozen.py, which proves the SQL and the Python
-- agree across all 16 flag combinations.

-- ⚠ APPLY THIS AT A QUIET MOMENT. Holding TABLOCKX on dbo.ReviewStatus while
--    `ALTER TABLE dbo.Review` runs can DEADLOCK against a concurrent review
--    creation: that insert holds dbo.Review's lock and then waits on FK
--    validation against dbo.ReviewStatus, while this migration holds
--    ReviewStatus and waits for Review. (`UpdateReviewStatusById` orders
--    ReviewStatus -> Review, so a role transfer is NOT the reverse-order case;
--    a plain `CreateReview` is.)
--
--    Not a correctness problem: the whole file runs in one transaction, so if
--    this side is chosen as the deadlock victim EVERYTHING unwinds -- column,
--    backfills, constraint -- and it can simply be re-run. The window is ~5s.
--
-- 0. FENCE dbo.ReviewStatus for the WHOLE migration, before the column exists.
--
--    Taking the lock at the first backfill was not early enough (Codex): the
--    window opens the moment the column is added, and a role transfer
--    committing between the ADD and the backfill's snapshot stamps every
--    pre-existing row from the NEW flags -- permanently, with no NULL left for
--    the re-sweep or the RAISERROR to catch.
--
--    And the dependency on the runner is now CHECKED rather than assumed. An
--    earlier version of this file claimed taking the lock in both backfill
--    blocks made it "runner-independent". That was WRONG: HOLDLOCK lasts only
--    for the enclosing transaction, so under an autocommitting runner the
--    locking SELECT and the UPDATE that follows it are separate transactions
--    and the fence is released between them. It works because
--    `scripts/run_sql.py` uses ONE connection with autocommit off
--    (@@TRANCOUNT = 1 for the whole file, verified) -- so this asserts exactly
--    that, and refuses to proceed otherwise rather than silently backfilling
--    unfenced.
IF OBJECT_ID('dbo.ReviewStatus', 'U') IS NOT NULL
BEGIN
    IF @@TRANCOUNT = 0
        RAISERROR('U-455: dbo.review.sql must be applied inside a transaction (scripts/run_sql.py does this). Without one the ReviewStatus fence is released between statements and the backfill can stamp rows from flags that changed mid-migration.', 16, 1);

    DECLARE @statuses_fenced INT;
    SELECT @statuses_fenced = COUNT(*)
    FROM dbo.[ReviewStatus] WITH (TABLOCKX, HOLDLOCK);
END
GO

-- 1. add it NULLable (no DEFAULT: there is no safe default -- 'in_review' would
--    silently mislabel every submission the backfill has not reached yet)
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.Review', 'ReviewKind') IS NULL
BEGIN
    ALTER TABLE [dbo].[Review] ADD [ReviewKind] NVARCHAR(20) NULL;
END
GO

-- 2. backfill from the status flags as they stand
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.Review', 'ReviewKind') IS NOT NULL
BEGIN
    -- ⛔ LOCK dbo.ReviewStatus FIRST, and hold it (U-455, Codex P1).
    --
    -- The backfill derives each row's historical kind from the status's CURRENT
    -- flags. `UpdateReviewStatusById` can transfer IsInitial / IsFinal /
    -- IsDeclined at any moment, and if such a transfer commits between the
    -- column-add and this UPDATE's snapshot, every pre-existing row is stamped
    -- from the NEW flags -- permanently, and with NO NULL left behind for the
    -- re-sweep or the RAISERROR to notice. The migration would report success
    -- having written exactly the corruption it exists to prevent.
    --
    -- TABLOCKX + HOLDLOCK: an exclusive table lock held to the end of the
    -- transaction, so a concurrent role transfer either committed BEFORE this
    -- migration began (already-lost history, and not something a migration can
    -- fix) or waits until it is done. dbo.ReviewStatus is four rows and the
    -- backfill touches 1,700, so the block is momentary.
    --
    -- Re-asserted here as well as at step 0. Redundant under the real runner
    -- (one transaction, so step 0's fence is still held) and deliberately kept:
    -- it costs nothing on a 4-row table and makes each block legible on its own.
    -- It does NOT make the file runner-independent -- HOLDLOCK lives only as
    -- long as the enclosing transaction, which is why step 0 asserts
    -- @@TRANCOUNT rather than hoping.
    DECLARE @statuses_locked INT;
    SELECT @statuses_locked = COUNT(*)
    FROM dbo.[ReviewStatus] WITH (TABLOCKX, HOLDLOCK);

    UPDATE r
    SET r.[ReviewKind] =
        CASE
            WHEN rs.[IsDeclined] = 1 THEN N'declined'
            WHEN rs.[IsFinal]    = 1 THEN N'approved'
            WHEN rs.[IsInitial]  = 1 THEN N'submitted'
            ELSE N'in_review'
        END
    FROM dbo.[Review] r
    INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = r.[ReviewStatusId]
    WHERE r.[ReviewKind] IS NULL;
END
GO

-- 3. re-backfill, then make it NOT NULL -- or FAIL LOUDLY.
--
--    The window this closes (Codex P1): the column is added NULLable here, but
--    the writer that stamps it -- `CreateReview` -- is not replaced until much
--    later in this same file. A review created in between is inserted by the
--    OLD sproc and lands NULL. The first version of this step simply SKIPPED
--    when it saw a NULL, so the deploy reported success and left rows that
--    silently fall back to live-flag derivation; a later re-apply would then
--    "backfill" them from whatever the flags say THEN, which is precisely the
--    historical corruption this unit exists to prevent.
--
--    So: sweep again (catching anything inserted since step 2), and if a NULL
--    still survives that, RAISERROR. A migration that cannot establish its
--    invariant must fail, not shrug.
--
--    Failing is SAFE here, not half-destructive: `scripts/run_sql.py` runs every
--    batch of this file on ONE connection and re-raises on the first error, and
--    `shared/database.get_connection` rolls back on any exception. SQL Server's
--    DDL is transactional, so the column-add, both backfills and the ALTER all
--    unwind together -- the database is left exactly as it was, and the operator
--    sees why.
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('dbo.Review')
                 AND name = 'ReviewKind' AND is_nullable = 1)
BEGIN
    -- ⛔ LOCK dbo.ReviewStatus FIRST, and hold it (U-455, Codex P1).
    --
    -- The backfill derives each row's historical kind from the status's CURRENT
    -- flags. `UpdateReviewStatusById` can transfer IsInitial / IsFinal /
    -- IsDeclined at any moment, and if such a transfer commits between the
    -- column-add and this UPDATE's snapshot, every pre-existing row is stamped
    -- from the NEW flags -- permanently, and with NO NULL left behind for the
    -- re-sweep or the RAISERROR to notice. The migration would report success
    -- having written exactly the corruption it exists to prevent.
    --
    -- TABLOCKX + HOLDLOCK: an exclusive table lock held to the end of the
    -- transaction, so a concurrent role transfer either committed BEFORE this
    -- migration began (already-lost history, and not something a migration can
    -- fix) or waits until it is done. dbo.ReviewStatus is four rows and the
    -- backfill touches 1,700, so the block is momentary.
    --
    -- Re-asserted here as well as at step 0. Redundant under the real runner
    -- (one transaction, so step 0's fence is still held) and deliberately kept:
    -- it costs nothing on a 4-row table and makes each block legible on its own.
    -- It does NOT make the file runner-independent -- HOLDLOCK lives only as
    -- long as the enclosing transaction, which is why step 0 asserts
    -- @@TRANCOUNT rather than hoping.
    DECLARE @statuses_locked_resweep INT;
    SELECT @statuses_locked_resweep = COUNT(*)
    FROM dbo.[ReviewStatus] WITH (TABLOCKX, HOLDLOCK);

    UPDATE r
    SET r.[ReviewKind] =
        CASE
            WHEN rs.[IsDeclined] = 1 THEN N'declined'
            WHEN rs.[IsFinal]    = 1 THEN N'approved'
            WHEN rs.[IsInitial]  = 1 THEN N'submitted'
            ELSE N'in_review'
        END
    FROM dbo.[Review] r
    INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = r.[ReviewStatusId]
    WHERE r.[ReviewKind] IS NULL;

    IF EXISTS (SELECT 1 FROM dbo.[Review] WHERE [ReviewKind] IS NULL)
        RAISERROR('U-455: [Review].[ReviewKind] still has NULL rows after two backfill passes; refusing to continue rather than leaving history derived from live config.', 16, 1);
    ELSE
        ALTER TABLE [dbo].[Review] ALTER COLUMN [ReviewKind] NVARCHAR(20) NOT NULL;
END
GO

-- 4. and constrain it to the row-level vocabulary. `none` is deliberately
--    absent: it is REVIEW_STATUS_KINDS' answer for a document with NO review
--    row, which by construction cannot be a row in this table.
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.Review', 'ReviewKind') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Review_ReviewKind')
BEGIN
    -- `IS NOT NULL` is not redundant with the column's NOT NULL: a CHECK
    -- evaluates `NULL IN (...)` as UNKNOWN, which PASSES. Stating it means the
    -- constraint alone rejects a NULL even if the column constraint were ever
    -- relaxed.
    ALTER TABLE [dbo].[Review] ADD CONSTRAINT [CK_Review_ReviewKind]
        CHECK ([ReviewKind] IS NOT NULL
               AND [ReviewKind] IN (N'submitted', N'in_review', N'approved', N'declined'));
END
GO


-- =========================================================================
-- Indexes (filtered, one per parent FK)
-- =========================================================================

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_BillId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_BillId] ON [dbo].[Review]([BillId]) WHERE [BillId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_ExpenseId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_ExpenseId] ON [dbo].[Review]([ExpenseId]) WHERE [ExpenseId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_BillCreditId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_BillCreditId] ON [dbo].[Review]([BillCreditId]) WHERE [BillCreditId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_InvoiceId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_InvoiceId] ON [dbo].[Review]([InvoiceId]) WHERE [InvoiceId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_ContractLaborId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_ContractLaborId] ON [dbo].[Review]([ContractLaborId]) WHERE [ContractLaborId] IS NOT NULL;
END
GO

-- EmailMessageId: optional FK back to the EmailMessage that triggered
-- this Review state transition. Used by the Web UI's "final review"
-- surface to navigate from a state row to the source email (vendor
-- invoice / forwarded notification / PM reply).
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='EmailMessageId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    ALTER TABLE [dbo].[Review] ADD [EmailMessageId] BIGINT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Review_EmailMessage')
BEGIN
    ALTER TABLE [dbo].[Review]
    ADD CONSTRAINT [FK_Review_EmailMessage] FOREIGN KEY ([EmailMessageId]) REFERENCES [dbo].[EmailMessage]([Id]);
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Review_EmailMessageId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_EmailMessageId] ON [dbo].[Review]([EmailMessageId]) WHERE [EmailMessageId] IS NOT NULL;
END
GO


-- =========================================================================
-- View — denormalized JOIN to ReviewStatus + User
-- Every read sproc selects from this view.
-- =========================================================================

CREATE OR ALTER VIEW [dbo].[vw_Review]
AS
    SELECT
        r.[Id],
        r.[PublicId],
        r.[RowVersion],
        CONVERT(VARCHAR(19), r.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), r.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        r.[ReviewStatusId],
        r.[UserId],
        r.[Comments],
        r.[BillId],
        r.[ExpenseId],
        r.[BillCreditId],
        r.[InvoiceId],
        r.[ContractLaborId],
        r.[EmailMessageId],
        rs.[Name]       AS [StatusName],
        rs.[SortOrder]  AS [StatusSortOrder],
        rs.[IsFinal]    AS [StatusIsFinal],
        rs.[IsDeclined] AS [StatusIsDeclined],
        -- U-444. `review_status_kind = submitted` keys on THIS FLAG. It used to
        -- key on position (StatusSortOrder == the MIN active non-declined one),
        -- which meant two rows sharing a SortOrder both derived `submitted`,
        -- and a new lowest row retroactively relabelled every stored one.
        r.[ReviewKind],
        -- U-455: [ReviewKind] above is the FROZEN answer, stamped at insert.
        -- The Status* flags below are the status's CURRENT configuration and
        -- are kept for callers that legitimately want today's shape (the
        -- inbox's non-final / non-declined Pending filter, the status admin
        -- UI). Do NOT re-derive a row's kind from them -- that is the bug
        -- U-455 fixed, and it reads identically until the day someone moves a
        -- flag.
        rs.[IsInitial]  AS [StatusIsInitial],
        rs.[Color]      AS [StatusColor],
        u.[Firstname]   AS [UserFirstname],
        u.[Lastname]    AS [UserLastname]
    FROM dbo.[Review] r
    INNER JOIN dbo.[ReviewStatus] rs ON r.[ReviewStatusId] = rs.[Id]
    INNER JOIN dbo.[User] u          ON r.[UserId]         = u.[Id];
GO


-- =========================================================================
-- CreateReview
-- =========================================================================

CREATE OR ALTER PROCEDURE CreateReview
(
    @ReviewStatusId  BIGINT,
    @UserId          BIGINT,
    @Comments        NVARCHAR(MAX) = NULL,
    @BillId          BIGINT = NULL,
    @ExpenseId       BIGINT = NULL,
    @BillCreditId    BIGINT = NULL,
    @InvoiceId       BIGINT = NULL,
    @ContractLaborId BIGINT = NULL,
    @EmailMessageId  BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL,
    -- U-454. Defaults to 0 = REFUSE, per U-446c: `@AllowTerminalParent BIT = 1`
    -- meant every caller that had not been taught about the lock silently
    -- skipped it, so the guard protected only the paths that already knew.
    @AllowTerminalParent BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- U-454: refuse a review transition on a document that is already finished,
    -- INSIDE the writing transaction.
    --
    -- The service layer checks this too, and that check is not enough on its
    -- own: RCSI means a plain SELECT reads a snapshot, so a completion
    -- committing between the service's read and this INSERT still lands a new
    -- review row on a completed parent -- and U-454's own inbox filter then
    -- HIDES the resulting task. Same two-layer shape U-446b established for the
    -- Bill mutation sprocs.
    --
    -- LOCK UNCONDITIONALLY, REFUSE CONDITIONALLY (U-446c). The UPDLOCK is taken
    -- whether or not this caller is exempt: gating the lock itself on the
    -- exemption makes an exempt writer invisible to concurrent transactions,
    -- which is the subtler half of the same bug.
    --
    -- ContractLabor has no [IsDraft] column and speaks its own vocabulary, so
    -- it gets its own predicate rather than being skipped. Its terminal state
    -- is 'billed' today; `2026_07_02_unify_labor_status_vocab.sql` maps that to
    -- 'completed' at the LS-04 cutover, so BOTH spellings are accepted and this
    -- guard survives the migration without a second edit. (A "different
    -- vocabulary" is a reason to write a different predicate, not a reason to
    -- leave the entity unguarded -- Codex P1.)
    DECLARE @LockedFinished INT = 0;

    IF @BillId IS NOT NULL
        SELECT @LockedFinished = COUNT(*) FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @BillId AND [IsDraft] = 0;
    ELSE IF @ExpenseId IS NOT NULL
        SELECT @LockedFinished = COUNT(*) FROM dbo.[Expense] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @ExpenseId AND [IsDraft] = 0;
    ELSE IF @BillCreditId IS NOT NULL
        SELECT @LockedFinished = COUNT(*) FROM dbo.[BillCredit] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @BillCreditId AND [IsDraft] = 0;
    ELSE IF @InvoiceId IS NOT NULL
        SELECT @LockedFinished = COUNT(*) FROM dbo.[Invoice] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @InvoiceId AND [IsDraft] = 0;
    ELSE IF @ContractLaborId IS NOT NULL
        SELECT @LockedFinished = COUNT(*) FROM dbo.[ContractLabor] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @ContractLaborId AND [Status] IN ('billed', 'completed');

    IF @AllowTerminalParent = 0 AND @LockedFinished > 0
    BEGIN
        -- COMMIT then RAISERROR, never ROLLBACK inside a sproc: pyodbc runs
        -- autocommit-off, so an in-proc rollback zeroes the implicit outer
        -- transaction and SQL Server raises error 266 instead of this message.
        COMMIT TRANSACTION;
        RAISERROR('STATUS_LOCKED: its review cannot be changed once the document is completed.', 16, 1);
        RETURN;
    END

    -- U-455: freeze the kind at insert. Resolved HERE rather than in the
    -- service so every caller mirrors -- the UI, the review-reply email agent,
    -- the CL crew flow and scripted backfills alike -- exactly as the Bill
    -- Status mirror below is.
    --
    -- Precedence matches shared/lifecycle/resolver.py::review_kind_from_flags
    -- exactly: IsDeclined -> IsFinal -> IsInitial -> in_review.
    DECLARE @ReviewKind NVARCHAR(20);

    SELECT @ReviewKind =
        CASE
            WHEN rs.[IsDeclined] = 1 THEN N'declined'
            WHEN rs.[IsFinal]    = 1 THEN N'approved'
            WHEN rs.[IsInitial]  = 1 THEN N'submitted'
            ELSE N'in_review'
        END
    FROM dbo.[ReviewStatus] rs
    WHERE rs.[Id] = @ReviewStatusId;

    IF @ReviewKind IS NULL
    BEGIN
        -- FK_Review_ReviewStatus would reject this anyway; failing here names
        -- the cause instead of surfacing a constraint violation.
        COMMIT TRANSACTION;
        RAISERROR('CreateReview: ReviewStatusId %I64d does not exist.', 16, 1, @ReviewStatusId);
        RETURN;
    END

    INSERT INTO dbo.[Review] (
        [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId], [ContractLaborId],
        [EmailMessageId],
        [CreatedByUserId],
        [ReviewKind]
    )
    VALUES (
        @Now, @Now,
        @ReviewStatusId, @UserId, @Comments,
        @BillId, @ExpenseId, @BillCreditId, @InvoiceId, @ContractLaborId,
        @EmailMessageId,
        COALESCE(@CreatedByUserId, 17),
        @ReviewKind
    );

    -- U-445: mirror the new review state onto the parent Bill's Status column.
    --
    -- Without this the unit is broken for every NEW submission. `Status` used to
    -- be DERIVED at read time (U-443), so creating a Review automatically moved
    -- the bill; now the read model emits the STORED column, and nothing else
    -- writes it. A bill submitted for review would sit at 'draft' forever while
    -- `review_status_kind` said 'submitted' — invisible under `?status=submitted`
    -- and mislabelled under `?status=draft`.
    --
    -- Kept in the SAME transaction as the INSERT, and in the sproc rather than
    -- the service, so every caller mirrors: the UI, the review-reply email
    -- agent, the CL crew flow, and scripted backfills alike.
    --
    -- The `[IsDraft] = 1` guard is NOT optional: reviews legitimately exist on
    -- completed bills (39 completed-yet-'submitted' in prod on 2026-09-11), and
    -- dragging such a bill's Status back to 'submitted' would violate
    -- CK_Bill_Status_IsDraft and turn every such review write into a 500.
    -- `completed` outranks any review state (U-443) — we do not reopen a
    -- document whose AP already reached QBO/SharePoint/Excel/Box.
    --
    -- The mirror reuses @ReviewKind rather than re-reading the flags (U-455,
    -- Codex P1). Two separate reads of dbo.ReviewStatus in one sproc are two
    -- separate RCSI snapshots: a role transfer landing between them could stamp
    -- the Review `submitted` while setting the Bill to `in_review`. One read,
    -- one answer, and the two can no longer disagree -- which also makes the
    -- precedence impossible to duplicate wrongly, since it now exists once.
    --
    -- U-467: Expense Status mirror. Couples dbo.review.sql to dbo.expense.sql
    -- for a one-transaction apply (schema first leaves a window where Expense
    -- stores Status but this sproc does not stamp it; review first fails with
    -- error 207). Same shape as the Bill mirror below: assign the frozen
    -- @ReviewKind onto an OPEN expense only.
    IF @ExpenseId IS NOT NULL
    BEGIN
        UPDATE e
        SET e.[Status] = @ReviewKind,
            e.[StatusDatetime] = @Now,
            e.[StatusOrigin] = 'user',
            e.[ModifiedDatetime] = @Now
        FROM dbo.[Expense] e
        WHERE e.[Id] = @ExpenseId
          AND e.[IsDraft] = 1;
    END

    IF @BillId IS NOT NULL
    BEGIN
        UPDATE b
        SET b.[Status] = @ReviewKind,
            b.[StatusDatetime] = @Now,
            b.[StatusOrigin] = 'user',
            b.[ModifiedDatetime] = @Now
        FROM dbo.[Bill] b
        WHERE b.[Id] = @BillId
          AND b.[IsDraft] = 1;
    END

    SELECT * FROM dbo.[vw_Review] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


-- =========================================================================
-- ReadReviewByPublicId
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadReviewByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review] WHERE [PublicId] = @PublicId;
END;
GO


-- =========================================================================
-- ReadReviewsByXId — full history, ascending
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadReviewsByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadReviewsByExpenseId
(
    @ExpenseId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [ExpenseId] = @ExpenseId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadReviewsByBillCreditId
(
    @BillCreditId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [BillCreditId] = @BillCreditId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadReviewsByInvoiceId
(
    @InvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [InvoiceId] = @InvoiceId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

-- U-126 (2026-07-23): homed from migration 005; body is the LIVE prod definition captured via sys.sql_modules.
CREATE OR ALTER PROCEDURE ReadReviewsByContractLaborId
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [ContractLaborId] = @ContractLaborId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO


-- =========================================================================
-- ReadCurrentReviewByXId — TOP 1 latest, descending. Tiebreak by Id DESC.
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadCurrentReviewByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentReviewByExpenseId
(
    @ExpenseId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [ExpenseId] = @ExpenseId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentReviewByBillCreditId
(
    @BillCreditId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [BillCreditId] = @BillCreditId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentReviewByInvoiceId
(
    @InvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [InvoiceId] = @InvoiceId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

-- U-126 (2026-07-23): homed from migration 005; body is the LIVE prod definition captured via sys.sql_modules.
CREATE OR ALTER PROCEDURE ReadCurrentReviewByContractLaborId
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [ContractLaborId] = @ContractLaborId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

-- Batch lookup: latest Review per Bill in one call. Used by the Bill
-- list endpoint (Wave 3 Phase D) to surface ReviewStatus alongside
-- Draft state without a per-row N+1 query. Returns at most one row
-- per BillId — the most recently created Review for that bill.
CREATE OR ALTER PROCEDURE ReadCurrentReviewsByBillIds
(
    @BillIds NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH ranked AS (
        SELECT
            r.*,
            ROW_NUMBER() OVER (
                PARTITION BY r.[BillId]
                ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC
            ) AS rn
        FROM dbo.[vw_Review] r
        INNER JOIN STRING_SPLIT(ISNULL(@BillIds, ''), ',') s
            ON s.value <> '' AND r.[BillId] = TRY_CAST(LTRIM(RTRIM(s.value)) AS BIGINT)
        WHERE r.[BillId] IS NOT NULL
    )
    SELECT
        [Id], [PublicId], [RowVersion], [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId],
        [StatusName], [StatusSortOrder], [StatusIsFinal], [StatusIsDeclined], [StatusIsInitial], [StatusColor],
        -- U-455/U-457. The four batch readers are the only vw_Review readers
        -- with explicit column lists -- every other one is `SELECT *` and picks
        -- this column up for free. Omitting it made the Bill LIST re-derive the
        -- kind from live flags while every single GET used the frozen value:
        -- the unit silently inert on its busiest consumer (Codex P1). The
        -- generic scan in tests/test_u455_review_kind_frozen.py now fails any
        -- explicit projection that drops it, which is what keeps the three
        -- siblings below honest.
        [ReviewKind],
        [UserFirstname], [UserLastname]
    FROM ranked
    WHERE rn = 1;
END;
GO

-- U-457: the batch current-review reader for Expense, mirroring
-- ReadCurrentReviewsByBillIds exactly. Without it the Expense LIST would
-- resolve one review per row -- the N+1 that Bill's slice avoided and pinned.
CREATE OR ALTER PROCEDURE ReadCurrentReviewsByExpenseIds
(
    @ExpenseIds NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH ranked AS (
        SELECT
            r.*,
            ROW_NUMBER() OVER (
                PARTITION BY r.[ExpenseId]
                ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC
            ) AS rn
        FROM dbo.[vw_Review] r
        INNER JOIN STRING_SPLIT(ISNULL(@ExpenseIds, ''), ',') s
            ON s.value <> '' AND r.[ExpenseId] = TRY_CAST(LTRIM(RTRIM(s.value)) AS BIGINT)
        WHERE r.[ExpenseId] IS NOT NULL
    )
    SELECT
        [Id], [PublicId], [RowVersion], [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId],
        [StatusName], [StatusSortOrder], [StatusIsFinal], [StatusIsDeclined], [StatusIsInitial], [StatusColor],
        [ReviewKind],
        [UserFirstname], [UserLastname]
    FROM ranked
    WHERE rn = 1;
END;
GO

-- U-457: the batch current-review reader for BillCredit, mirroring
-- ReadCurrentReviewsByBillIds exactly. Without it the BillCredit LIST would
-- resolve one review per row -- the N+1 that Bill's slice avoided and pinned.
CREATE OR ALTER PROCEDURE ReadCurrentReviewsByBillCreditIds
(
    @BillCreditIds NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH ranked AS (
        SELECT
            r.*,
            ROW_NUMBER() OVER (
                PARTITION BY r.[BillCreditId]
                ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC
            ) AS rn
        FROM dbo.[vw_Review] r
        INNER JOIN STRING_SPLIT(ISNULL(@BillCreditIds, ''), ',') s
            ON s.value <> '' AND r.[BillCreditId] = TRY_CAST(LTRIM(RTRIM(s.value)) AS BIGINT)
        WHERE r.[BillCreditId] IS NOT NULL
    )
    SELECT
        [Id], [PublicId], [RowVersion], [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId],
        [StatusName], [StatusSortOrder], [StatusIsFinal], [StatusIsDeclined], [StatusIsInitial], [StatusColor],
        [ReviewKind],
        [UserFirstname], [UserLastname]
    FROM ranked
    WHERE rn = 1;
END;
GO

-- U-457: the batch current-review reader for Invoice, mirroring
-- ReadCurrentReviewsByBillIds exactly. Without it the Invoice LIST would
-- resolve one review per row -- the N+1 that Bill's slice avoided and pinned.
CREATE OR ALTER PROCEDURE ReadCurrentReviewsByInvoiceIds
(
    @InvoiceIds NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH ranked AS (
        SELECT
            r.*,
            ROW_NUMBER() OVER (
                PARTITION BY r.[InvoiceId]
                ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC
            ) AS rn
        FROM dbo.[vw_Review] r
        INNER JOIN STRING_SPLIT(ISNULL(@InvoiceIds, ''), ',') s
            ON s.value <> '' AND r.[InvoiceId] = TRY_CAST(LTRIM(RTRIM(s.value)) AS BIGINT)
        WHERE r.[InvoiceId] IS NOT NULL
    )
    SELECT
        [Id], [PublicId], [RowVersion], [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId],
        [StatusName], [StatusSortOrder], [StatusIsFinal], [StatusIsDeclined], [StatusIsInitial], [StatusColor],
        [ReviewKind],
        [UserFirstname], [UserLastname]
    FROM ranked
    WHERE rn = 1;
END;
GO

-- Delete all Review rows for a Bill. Called by BillService.delete_by_public_id
-- so a bill can be hard-deleted without tripping FK_Review_Bill. Reviews are
-- otherwise insert-only (audit history); this delete path exists ONLY for the
-- cascade when the parent Bill is itself being deleted. Also clears legacy
-- dbo.ReviewEntry rows (decommissioned table that still carries an FK to Bill);
-- guarded by an OBJECT_ID check so it's safe once that table is dropped.
CREATE OR ALTER PROCEDURE DeleteReviewsByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Review] WHERE [BillId] = @BillId;

    IF OBJECT_ID('dbo.ReviewEntry', 'U') IS NOT NULL
        DELETE FROM dbo.[ReviewEntry] WHERE [BillId] = @BillId;

    COMMIT TRANSACTION;
END;
GO

-- U-126 (2026-07-23): homed from migration 005; body is the LIVE prod definition captured via sys.sql_modules.
-- =========================================================================
-- DeleteReviewsByContractLaborId — for parent cascades
-- Mirrors DeleteReviewsByBillId. Required only if ContractLabor ever
-- hard-deletes parents (current ContractLaborService.delete is hard-delete).
-- =========================================================================

CREATE OR ALTER PROCEDURE DeleteReviewsByContractLaborId
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Review]
    WHERE [ContractLaborId] = @ContractLaborId;

    COMMIT TRANSACTION;
END;
GO


-- Review-notification recipient resolvers (canonical home; single-sourced U-062).
-- Human-only: excludes agent accounts (User.IsAgent=1) + persona test accounts
-- (Auth.Username LIKE 'persona_%'). Migrations 001/004/006/007/008 are superseded.

GO

-- -----------------------------------------------------------------------------
-- Shared human-only predicate (U-087): single source for the filter that was
-- copy-pasted verbatim in all three resolvers below. Excludes LLM agent accounts
-- (User.IsAgent = 1) and persona test accounts (Auth.Username starting with
-- 'persona_', whitespace-tolerant). Returns 1 for a real human reviewer, else 0.
-- Recipient sets are tiny, so correctness/DRY > perf; on compat-150+ FROID
-- inlines it into the resolver plans anyway.
-- -----------------------------------------------------------------------------
CREATE OR ALTER FUNCTION dbo.IsHumanReviewUser (@UserId BIGINT)
RETURNS BIT
AS
BEGIN
    RETURN CASE WHEN
        NOT EXISTS (
            SELECT 1 FROM dbo.[User] u
            WHERE u.[Id] = @UserId
              AND u.[IsAgent] = 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM dbo.[Auth] a
            WHERE a.[UserId] = @UserId
              AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
        )
    THEN 1 ELSE 0 END;
END;
GO

-- -----------------------------------------------------------------------------
-- 1. Bill resolver — filter personas in UserProjectRoles
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ResolveReviewRecipientsByBillId
(
    @BillId BIGINT,
    @ExcludeUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    WITH BillProjects AS (
        SELECT DISTINCT bli.[ProjectId]
        FROM dbo.[BillLineItem] bli
        WHERE bli.[BillId] = @BillId
          AND bli.[ProjectId] IS NOT NULL
    ),
    UserProjectRoles AS (
        SELECT
            up.[UserId],
            up.[ProjectId],
            r.[Name] AS [RoleName],
            CASE r.[Name]
                WHEN 'Project Manager' THEN 1
                WHEN 'Owner'           THEN 2
                ELSE 99
            END AS [RolePrecedence]
        FROM dbo.[UserProject] up
        INNER JOIN BillProjects bp ON bp.[ProjectId] = up.[ProjectId]
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] IN ('Project Manager', 'Owner')
          AND (@ExcludeUserId IS NULL OR up.[UserId] <> @ExcludeUserId)
          -- Restrict recipients to real human users via the U-087 shared
          -- predicate: excludes LLM agent accounts + persona test accounts.
          AND dbo.IsHumanReviewUser(up.[UserId]) = 1
    ),
    DedupedRoles AS (
        SELECT
            [UserId],
            [RoleName],
            [ProjectId],
            ROW_NUMBER() OVER (
                PARTITION BY [UserId]
                ORDER BY [RolePrecedence] ASC, [ProjectId] ASC
            ) AS rn
        FROM UserProjectRoles
    ),
    UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        u.[Id]        AS [UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        dr.[RoleName],
        dr.[ProjectId]
    FROM DedupedRoles dr
    INNER JOIN dbo.[User] u ON u.[Id] = dr.[UserId]
    LEFT JOIN UserEmails ue
        ON ue.[UserId] = dr.[UserId]
       AND ue.rn = 1
    WHERE dr.rn = 1
    ORDER BY dr.[RoleName], u.[Lastname], u.[Firstname];

    COMMIT TRANSACTION;
END;
GO


-- -----------------------------------------------------------------------------
-- 2. ContractLabor resolver — filter personas in UserProjectRoles
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ResolveReviewRecipientsByContractLaborId
(
    @ContractLaborId BIGINT,
    @ExcludeUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    WITH ContractLaborProjects AS (
        SELECT DISTINCT cli.[ProjectId]
        FROM dbo.[ContractLaborLineItem] cli
        WHERE cli.[ContractLaborId] = @ContractLaborId
          AND cli.[ProjectId] IS NOT NULL
    ),
    UserProjectRoles AS (
        SELECT
            up.[UserId],
            up.[ProjectId],
            r.[Name] AS [RoleName],
            CASE r.[Name]
                WHEN 'Project Manager' THEN 1
                WHEN 'Owner'           THEN 2
                ELSE 99
            END AS [RolePrecedence]
        FROM dbo.[UserProject] up
        INNER JOIN ContractLaborProjects clp ON clp.[ProjectId] = up.[ProjectId]
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] IN ('Project Manager', 'Owner')
          AND (@ExcludeUserId IS NULL OR up.[UserId] <> @ExcludeUserId)
          -- Restrict recipients to real human users via the U-087 shared
          -- predicate: excludes LLM agent accounts + persona test accounts.
          AND dbo.IsHumanReviewUser(up.[UserId]) = 1
    ),
    DedupedRoles AS (
        SELECT
            [UserId],
            [RoleName],
            [ProjectId],
            ROW_NUMBER() OVER (
                PARTITION BY [UserId]
                ORDER BY [RolePrecedence] ASC, [ProjectId] ASC
            ) AS rn
        FROM UserProjectRoles
    ),
    UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        u.[Id]        AS [UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        dr.[RoleName],
        dr.[ProjectId]
    FROM DedupedRoles dr
    INNER JOIN dbo.[User] u ON u.[Id] = dr.[UserId]
    LEFT JOIN UserEmails ue
        ON ue.[UserId] = dr.[UserId]
       AND ue.rn = 1
    WHERE dr.rn = 1
    ORDER BY dr.[RoleName], u.[Lastname], u.[Firstname];

    COMMIT TRANSACTION;
END;
GO


-- -----------------------------------------------------------------------------
-- 3. Per-project ContractLabor resolver (v2 envelope, includes Owners)
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ResolveContractLaborReviewRecipientsPerProject
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    WITH ContractLaborProjects AS (
        SELECT DISTINCT cli.[ProjectId]
        FROM dbo.[ContractLaborLineItem] cli
        WHERE cli.[ContractLaborId] = @ContractLaborId
          AND cli.[ProjectId] IS NOT NULL
    ),
    UserProjectRoles AS (
        SELECT
            up.[ProjectId],
            up.[UserId],
            r.[Name] AS [RoleName],
            CASE r.[Name]
                WHEN N'Project Manager' THEN 1
                WHEN N'Owner'           THEN 2
                ELSE 99
            END AS [RolePrecedence]
        FROM dbo.[UserProject] up
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] IN (N'Project Manager', N'Owner')
          -- Restrict recipients to real human users via the U-087 shared
          -- predicate: excludes LLM agent accounts + persona test accounts.
          AND dbo.IsHumanReviewUser(up.[UserId]) = 1
    ),
    -- PM wins when a user holds both roles on the same project.
    DedupedUserProjectRoles AS (
        SELECT
            [ProjectId],
            [UserId],
            [RoleName],
            ROW_NUMBER() OVER (
                PARTITION BY [ProjectId], [UserId]
                ORDER BY [RolePrecedence] ASC
            ) AS rn
        FROM UserProjectRoles
    ),
    UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        clp.[ProjectId],
        p.[Name]         AS [ProjectName],
        p.[Abbreviation] AS [ProjectAbbreviation],
        dpr.[UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        dpr.[RoleName]
    FROM ContractLaborProjects clp
    INNER JOIN dbo.[Project] p ON p.[Id] = clp.[ProjectId]
    LEFT JOIN DedupedUserProjectRoles dpr
        ON dpr.[ProjectId] = clp.[ProjectId]
       AND dpr.rn = 1
    LEFT JOIN dbo.[User] u      ON u.[Id] = dpr.[UserId]
    LEFT JOIN UserEmails ue     ON ue.[UserId] = dpr.[UserId] AND ue.rn = 1
    ORDER BY clp.[ProjectId], dpr.[RoleName], u.[Lastname], u.[Firstname];
END;
GO
