-- ⚠️ DRIFT WARNING — DO NOT re-run this whole file against an existing DB.
-- This canonical file is STALE relative to the live schema:
--   • The CREATE TABLE block below omits IsCredit / SourceEmailMessageId
--     (added later by add_is_credit_column.sql, source_email_message_fk.sql).
--     A from-scratch run would build a table the CreateExpense INSERT then
--     fails against. (CreatedByUserId is no longer part of this gap — U-345
--     added an idempotent ALTER-ADD guard for it below, mirroring every
--     other entity in the campaign; see TODO.md.)
--   • ReadExpenses / ReadExpensesPaginated / CountExpenses here LACK the live
--     @ActorUserId / @ActorIsSystemAdmin RBAC params (from the Gap 1 list-scoping
--     migrations) that the Python repo passes — re-running them would REGRESS
--     per-user UserProject scoping (silent data-leak across users).
-- Apply targeted migrations under entities/expense/sql/migrations/ instead.
-- (Full canonical reconcile is tracked in TODO.md.)
GO

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
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Expense]
        ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ExpenseDate],
         [ReferenceNumber], [TotalAmount], [Memo], [IsDraft], [IsCredit],
         [SourceEmailMessageId], [CreatedByUserId])
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
        INSERTED.[IsCredit],
        INSERTED.[SourceEmailMessageId]
    VALUES (@Now, @Now, @VendorId, @ExpenseDate, @ReferenceNumber, @TotalAmount,
            @Memo, @IsDraft, @IsCredit, @SourceEmailMessageId,
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
        [IsCredit]
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
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END,
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
        INSERTED.[IsCredit]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

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
    @SortBy NVARCHAR(50) = 'ExpenseDate',
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
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
        e.[IsCredit]
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
        AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1
    ORDER BY
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'ReferenceNumber' THEN e.[ReferenceNumber] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ReferenceNumber' THEN e.[ReferenceNumber] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'ExpenseDate' THEN e.[ExpenseDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ExpenseDate' THEN e.[ExpenseDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'TotalAmount' THEN e.[TotalAmount] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'TotalAmount' THEN e.[TotalAmount] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'VendorId' THEN e.[VendorId] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'VendorId' THEN e.[VendorId] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;
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
