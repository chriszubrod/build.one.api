GO

IF OBJECT_ID('dbo.ReviewStatus', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ReviewStatus]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(100) NOT NULL,
    [Description] NVARCHAR(500) NULL,
    [SortOrder] INT NOT NULL DEFAULT 0,
    [IsFinal] BIT NOT NULL DEFAULT 0,
    [IsDeclined] BIT NOT NULL DEFAULT 0,
    [IsActive] BIT NOT NULL DEFAULT 1,
    [Color] NVARCHAR(7) NULL
);
END
GO

-- ---------------------------------------------------------------------------
-- Idempotent column adds. Both columns were introduced by a migration and were
-- never ported back into this CREATE TABLE, so a from-scratch build produced a
-- table its own sprocs could not INSERT into.
--
--   CreatedByUserId  scripts/migrations/gap2_adjacent_threading.sql (2026-05-07)
--   IsInitial        U-444 (2026-09-11)
-- ---------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.ReviewStatus') AND name = 'CreatedByUserId'
)
BEGIN
    ALTER TABLE dbo.[ReviewStatus] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_ReviewStatus_CreatedByUserId] DEFAULT (17);
END
GO

-- Guarded on dbo.[User] EXISTING, not just on the FK's absence: unlike a
-- procedure body (whose table names resolve lazily), ALTER TABLE ... REFERENCES
-- resolves immediately, so an unguarded add aborts the whole file on a
-- from-scratch build where dbo.[User] has not been created yet.
IF OBJECT_ID('dbo.[User]', 'U') IS NOT NULL
   AND NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ReviewStatus_CreatedByUser'
)
BEGIN
    ALTER TABLE dbo.[ReviewStatus] ADD CONSTRAINT [FK_ReviewStatus_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES dbo.[User]([Id]);
END
GO

-- U-444. `review_status_kind = submitted` used to be POSITION-derived: the row
-- with MIN(SortOrder) among active non-declined rows. That made SortOrder load
-- bearing in a way nothing guarded — inserting a new lowest row silently
-- re-derived every historical `submitted` Review as `in_review`, retroactively,
-- because the kind is computed at read time. `declined` and `approved` already
-- keyed on flags; only this one keyed on position.
--
-- IsInitial makes all three key on flags. SortOrder still drives what comes
-- NEXT (ReadNextReviewStatus), so reordering changes the pipeline going forward
-- without relabelling history.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.ReviewStatus') AND name = 'IsInitial'
)
BEGIN
    ALTER TABLE dbo.[ReviewStatus] ADD [IsInitial] BIT NOT NULL
        CONSTRAINT [DF_ReviewStatus_IsInitial] DEFAULT (0);
END
GO

-- Backfill, guarded so a re-apply can never move an admin's chosen initial row
-- back. Selects by the derivation ReadFirstReviewStatus used BEFORE this unit,
-- so the cutover is a provable no-op rather than a hardcoded id. (The `[Id]`
-- tiebreak is belt-and-braces; prod SortOrders are 10/20/30/100, no ties.)
IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [IsInitial] = 1)
BEGIN
    UPDATE dbo.[ReviewStatus]
    SET [IsInitial] = 1
    WHERE [Id] = (
        SELECT TOP 1 [Id]
        FROM dbo.[ReviewStatus]
        -- `[IsFinal] = 0` matters: the NEW ReadFirstReviewStatus excludes final
        -- rows (the position version did not). Without it, a config whose
        -- lowest active non-declined row happened to be final would be marked
        -- initial here and then REJECTED by the new predicate — the sproc would
        -- return no row and auto-submit would silently stop creating Reviews.
        --
        -- HONESTY NOTE (Codex P1, 2026-09-11): this makes the backfill agree
        -- with the new predicate, but it does NOT make the cutover a no-op for
        -- every possible input, and the earlier comment here wrongly claimed it
        -- did. Two inputs change behaviour ON PURPOSE:
        --   * lowest active non-declined row is FINAL — old code returned it
        --     (and /submit auto-approved, the bug); U-444 skips to the next.
        --   * tied SortOrders — the old `TOP 1 ORDER BY [SortOrder]` was
        --     unspecified among ties; this is deterministic on [Id].
        -- For THIS database it IS a no-op, verified against prod 2026-09-11:
        -- both this query and the live sproc return id 1 'Submitted'.
        WHERE [IsDeclined] = 0 AND [IsActive] = 1 AND [IsFinal] = 0
        ORDER BY [SortOrder] ASC, [Id] ASC
    );
END
GO


GO

CREATE OR ALTER PROCEDURE CreateReviewStatus
(
    @Name NVARCHAR(100),
    @Description NVARCHAR(500) = NULL,
    @SortOrder INT = 0,
    @IsFinal BIT = 0,
    @IsDeclined BIT = 0,
    @IsActive BIT = 1,
    @Color NVARCHAR(7) = NULL,
    -- Reconciled FROM LIVE (U-444). This param was added by
    -- scripts/migrations/gap2_adjacent_threading.sql on 2026-05-07 and never
    -- ported here, so this base file was STALE against prod. Re-applying it
    -- without this line would have DROPPED the param from the live sproc and
    -- broken every review-status create — ReviewStatusRepository.create()
    -- passes it. Verified against sys.parameters before the port.
    @CreatedByUserId BIGINT = NULL,
    @IsInitial BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- Same transfer semantics as the update path: a new status created AS the
    -- initial one demotes the incumbent rather than producing an illegal set.
    IF @IsInitial = 1
        UPDATE dbo.[ReviewStatus] SET [IsInitial] = 0, [ModifiedDatetime] = @Now
        WHERE [IsInitial] = 1;

    IF @IsFinal = 1
        UPDATE dbo.[ReviewStatus] SET [IsFinal] = 0, [ModifiedDatetime] = @Now
        WHERE [IsFinal] = 1 AND [IsActive] = 1;

    IF @IsDeclined = 1
        UPDATE dbo.[ReviewStatus] SET [IsDeclined] = 0, [ModifiedDatetime] = @Now
        WHERE [IsDeclined] = 1 AND [IsActive] = 1;

    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [IsInitial], [Color], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[SortOrder],
        INSERTED.[IsFinal],
        INSERTED.[IsDeclined],
        INSERTED.[IsActive],
        INSERTED.[IsInitial],
        INSERTED.[Color]
    VALUES (@Now, @Now, @Name, @Description, @SortOrder, @IsFinal, @IsDeclined, @IsActive, @IsInitial, @Color, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewStatuses
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [IsInitial],
        [Color]
    FROM dbo.[ReviewStatus]
    ORDER BY [SortOrder] ASC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewStatusById
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
        [Name],
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [IsInitial],
        [Color]
    FROM dbo.[ReviewStatus]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewStatusByPublicId
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
        [Name],
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [IsInitial],
        [Color]
    FROM dbo.[ReviewStatus]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadNextReviewStatus
(
    @CurrentSortOrder INT
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
        [Name],
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [IsInitial],
        [Color]
    FROM dbo.[ReviewStatus]
    WHERE [SortOrder] > @CurrentSortOrder
      AND [IsDeclined] = 0
      AND [IsActive] = 1
    ORDER BY [SortOrder] ASC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadFirstReviewStatus
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [IsInitial],
        [Color]
    FROM dbo.[ReviewStatus]
    -- U-444: keys on the FLAG, not on MIN(SortOrder). Deliberately NO fallback
    -- to the old position rule — a config with no active initial row must fail
    -- loudly here rather than silently pick whatever sorts lowest. This sproc
    -- decides the status every auto-Submit Review is CREATED at
    -- (bill/business/service.py) and whether reviewers get emailed
    -- (review/business/service.py), so a quietly-wrong answer is expensive.
    --
    -- Note it also still excludes IsFinal rows, which the position version did
    -- NOT: the old MIN row being final would have made /submit auto-approve.
    WHERE [IsInitial] = 1
      AND [IsActive] = 1
      AND [IsDeclined] = 0
      AND [IsFinal] = 0
    ORDER BY [SortOrder] ASC, [Id] ASC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE UpdateReviewStatusById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(100),
    @Description NVARCHAR(500) = NULL,
    @SortOrder INT = 0,
    @IsFinal BIT = 0,
    @IsDeclined BIT = 0,
    @IsActive BIT = 1,
    @Color NVARCHAR(7) = NULL,
    -- NULL-preserving, unlike its siblings above: this sproc SETs every column
    -- unconditionally, so a caller that predates the param (an old container
    -- mid-deploy) would drive a NOT NULL column to NULL. The CASE WHEN guard is
    -- the house convention for exactly this (CLAUDE.md, "Stored procedure NULL
    -- handling").
    @IsInitial BIT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @WasActive BIT;

    -- ====================================================================
    -- STEP 1 — take the target under lock and confirm the row version FIRST.
    --
    -- ORDER IS LOAD-BEARING (Codex P0, 2026-09-11). The role demotions below
    -- touch OTHER rows, and the final UPDATE is row-version-qualified. In the
    -- first cut the demotions ran first, so a STALE update — admin A moving the
    -- initial flag to a status admin B had just edited — cleared the incumbent,
    -- matched zero target rows, and COMMITTED: zero initial statuses, and
    -- `ReadFirstReviewStatus` returns nothing. BillService's auto-Submit
    -- handles "no first status" by logging a warning and SKIPPING the Review
    -- entirely, so every subsequent bill would quietly stop entering review.
    --
    -- Bailing here preserves this sproc's existing conflict contract: an empty
    -- result set, which `_from_db(None)` turns into None for the caller.
    -- ====================================================================
    SELECT @WasActive = [IsActive]
    FROM dbo.[ReviewStatus] WITH (UPDLOCK, HOLDLOCK)
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    IF @WasActive IS NULL
    BEGIN
        COMMIT TRANSACTION;
        RETURN;
    END

    -- STEP 2 — the one set-level invariant worth enforcing in SQL.
    --
    -- The service checks the full shape, but the deploy is SQL-first, so for a
    -- couple of minutes an OLD container writes through these sprocs with no
    -- rails at all (Codex P1). Of everything it could do, exactly one edit is
    -- silently unrecoverable: marking the INITIAL row final or declined. The
    -- new ReadFirstReviewStatus filters those out, so the set ends up with no
    -- first status and submissions stop with nothing in the logs. Cheap to
    -- refuse here because it is a single-row test.
    IF (CASE WHEN @IsInitial IS NULL THEN
            (SELECT [IsInitial] FROM dbo.[ReviewStatus] WHERE [Id] = @Id)
        ELSE @IsInitial END) = 1
       AND (@IsFinal = 1 OR @IsDeclined = 1)
    BEGIN
        COMMIT TRANSACTION;
        RAISERROR (
            'Review status configuration is invalid: the initial status cannot also be final or declined.',
            16, 1
        );
        RETURN;
    END

    -- STEP 3 — the strand check, re-run INSIDE this transaction.
    --
    -- ReviewStatusService checks it too, but on a separate connection, and a
    -- Review inserted in between does not change THIS row's RowVersion — so
    -- optimistic concurrency cannot see it and the service's rail passes on
    -- stale evidence.
    --
    -- The lock hints on the CHILD predicates are the point (Codex P1): locking
    -- only dbo.[ReviewStatus] leaves the Review insert free to land between the
    -- EXISTS and the UPDATE. UPDLOCK+HOLDLOCK on the child ranges blocks it.
    --
    -- Signalled by COMMIT-then-RAISERROR, never ROLLBACK: pyodbc runs
    -- autocommit-off, so an in-proc rollback zeroes the outer transaction and
    -- raises 266 (CLAUDE.md, 2026-06-11).
    IF @IsActive = 0 AND @WasActive = 1
    AND (
        EXISTS (SELECT 1 FROM dbo.[Review] WITH (UPDLOCK, HOLDLOCK)
                WHERE [ReviewStatusId] = @Id)
     OR (OBJECT_ID('dbo.ReviewEntry', 'U') IS NOT NULL
         AND EXISTS (SELECT 1 FROM dbo.[ReviewEntry] WITH (UPDLOCK, HOLDLOCK)
                     WHERE [ReviewStatusId] = @Id))
    )
    BEGIN
        COMMIT TRANSACTION;
        RAISERROR (
            'Review status configuration is invalid: cannot deactivate a status that reviews still reference.',
            16, 1
        );
        RETURN;
    END

    -- STEP 4 — singleton roles TRANSFER rather than being refused.
    --
    -- "Exactly one active X" is unsatisfiable one row at a time: setting the new
    -- holder reads as two, clearing the old reads as zero, so both halves get
    -- refused and the role can never move. All THREE roles get the same
    -- treatment (Codex P1 — the first cut gave it only to IsInitial, which left
    -- 'Approved' and 'Declined' permanently unreplaceable). Safe to run now:
    -- step 1 proved the target exists at this row version, so the UPDATE below
    -- cannot match zero rows and strand the demotion.
    IF @IsInitial = 1
        UPDATE dbo.[ReviewStatus] SET [IsInitial] = 0, [ModifiedDatetime] = @Now
        WHERE [IsInitial] = 1 AND [Id] <> @Id;

    IF @IsFinal = 1
        UPDATE dbo.[ReviewStatus] SET [IsFinal] = 0, [ModifiedDatetime] = @Now
        WHERE [IsFinal] = 1 AND [IsActive] = 1 AND [Id] <> @Id;

    IF @IsDeclined = 1
        UPDATE dbo.[ReviewStatus] SET [IsDeclined] = 0, [ModifiedDatetime] = @Now
        WHERE [IsDeclined] = 1 AND [IsActive] = 1 AND [Id] <> @Id;

    UPDATE dbo.[ReviewStatus]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Description] = @Description,
        [SortOrder] = @SortOrder,
        [IsFinal] = @IsFinal,
        [IsDeclined] = @IsDeclined,
        [IsActive] = @IsActive,
        [IsInitial] = CASE WHEN @IsInitial IS NULL THEN [IsInitial] ELSE @IsInitial END,
        [Color] = @Color
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[SortOrder],
        INSERTED.[IsFinal],
        INSERTED.[IsDeclined],
        INSERTED.[IsActive],
        INSERTED.[IsInitial],
        INSERTED.[Color]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE DeleteReviewStatusById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ReviewStatus]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Description],
        DELETED.[SortOrder],
        DELETED.[IsFinal],
        DELETED.[IsDeclined],
        DELETED.[IsActive],
        DELETED.[IsInitial],
        DELETED.[Color]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


GO

-- ---------------------------------------------------------------------------
-- U-444. "Is this status still in use?" — the DEACTIVATE rail.
--
-- DELETE is already blocked by FK_Review_ReviewStatus / FK_ReviewEntry_ReviewStatus.
-- Deactivating is not: IsActive=0 on a referenced row drops it out of
-- ReadFirstReviewStatus / ReadNextReviewStatus while leaving every historical
-- Review pointing at it, which strands documents mid-pipeline with no way
-- forward.
--
-- Homed here rather than in dbo.review.sql because it answers a ReviewStatus
-- question. It reads two other entities' tables, which is safe at build time:
-- T-SQL defers name resolution for tables, so this creates fine even when
-- dbo.Review does not exist yet (review_status builds first — Review FKs to it).
--
-- ReviewEntry is decommissioned (5 rows, 2026-09-11) but still carries a live
-- FK, so it counts until the table drops.
-- ---------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE CountReviewStatusReferencesById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    -- ReviewEntry is decommissioned. Deferred name resolution lets this sproc
    -- CREATE on a database that never had the table, but it would fail at
    -- EXECUTION with "Invalid object name" (Codex P2) — so the count is built
    -- dynamically and simply omits the leg when the table is gone.
    DECLARE @Count BIGINT =
        (SELECT COUNT_BIG(*) FROM dbo.[Review] WHERE [ReviewStatusId] = @Id);

    IF OBJECT_ID('dbo.ReviewEntry', 'U') IS NOT NULL
    BEGIN
        DECLARE @EntryCount BIGINT;
        EXEC sp_executesql
            N'SELECT @c = COUNT_BIG(*) FROM dbo.[ReviewEntry] WHERE [ReviewStatusId] = @Id',
            N'@Id BIGINT, @c BIGINT OUTPUT', @Id = @Id, @c = @EntryCount OUTPUT;
        SET @Count = @Count + ISNULL(@EntryCount, 0);
    END

    SELECT @Count AS [ReferenceCount];
END;


GO
