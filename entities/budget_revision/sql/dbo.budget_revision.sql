GO

-- ─────────────────────────────────────────────────────────────────────
-- BudgetRevision — revision (delta) rows under a Budget.
--
-- Rev 0 (Type='original') is the original schedule of values; each
-- change order (Type='change_order') adds delta lines (negatives
-- allowed). Current contract value per SubCostCode = SUM over APPROVED
-- revisions' BudgetLineItems.
--
-- Status: 'draft' → 'approved'. Approved revisions are IMMUTABLE — the
-- service layer enforces immutability on update/delete; this file's
-- ApproveBudgetRevisionById enforces the draft→approved gate in SQL
-- (WHERE [Status] = 'draft'). Originals are approved only via budget
-- activation (ActivateBudgetById, in the Budget entity's SQL file).
--
-- RevisionNumber is computed ATOMICALLY inside CreateBudgetRevision
-- (UPDLOCK + HOLDLOCK on the BudgetId range within the txn) — never
-- compute MAX+1 in Python; that is a dup-key race.
--
-- No FK CASCADE per project convention — DeleteBudgetRevisionById
-- deletes child BudgetLineItems explicitly inside the same txn.
-- ─────────────────────────────────────────────────────────────────────

IF OBJECT_ID('dbo.BudgetRevision', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BudgetRevision]
(
    [Id]                 BIGINT             IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]           UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
    [RowVersion]         ROWVERSION         NOT NULL,
    [CreatedDatetime]    DATETIME2(3)       NOT NULL,
    [ModifiedDatetime]   DATETIME2(3)       NULL,
    [CompanyId]          BIGINT             NOT NULL CONSTRAINT DF_BudgetRevision_CompanyId       DEFAULT (1),
    [CreatedByUserId]    BIGINT             NOT NULL CONSTRAINT DF_BudgetRevision_CreatedByUserId DEFAULT (17),

    [BudgetId]           BIGINT             NOT NULL,
    [RevisionNumber]     INT                NOT NULL,

    -- 'original' (Rev 0) | 'change_order'.
    [Type]               NVARCHAR(20)       NOT NULL,

    -- 'draft' → 'approved'. Approved rows are immutable.
    [Status]             NVARCHAR(20)       NOT NULL DEFAULT 'draft',

    [Title]              NVARCHAR(255)      NULL,
    [Description]        NVARCHAR(MAX)      NULL,

    [ApprovedByUserId]   BIGINT             NULL,
    [ApprovedDatetime]   DATETIME2(3)       NULL,
    [EffectiveDate]      DATE               NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BudgetRevision_PublicId' AND object_id = OBJECT_ID('dbo.BudgetRevision'))
BEGIN
    CREATE INDEX [IX_BudgetRevision_PublicId]
        ON [dbo].[BudgetRevision] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BudgetRevision_BudgetId' AND object_id = OBJECT_ID('dbo.BudgetRevision'))
BEGIN
    CREATE INDEX [IX_BudgetRevision_BudgetId]
        ON [dbo].[BudgetRevision] ([BudgetId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BudgetRevision_BudgetId_RevisionNumber' AND object_id = OBJECT_ID('dbo.BudgetRevision'))
BEGIN
    CREATE UNIQUE INDEX [UQ_BudgetRevision_BudgetId_RevisionNumber]
        ON [dbo].[BudgetRevision] ([BudgetId], [RevisionNumber]);
END
GO

-- Plain FK, NO cascade — explicit child delete per project convention.
-- Guarded on dbo.Budget existing so this file is order-tolerant; a re-run
-- after the Budget table lands adds the constraint.
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BudgetRevision_Budget')
   AND OBJECT_ID('dbo.Budget', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[BudgetRevision]
    ADD CONSTRAINT [FK_BudgetRevision_Budget] FOREIGN KEY ([BudgetId]) REFERENCES [dbo].[Budget]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BudgetRevision_ApprovedByUser')
BEGIN
    ALTER TABLE [dbo].[BudgetRevision]
    ADD CONSTRAINT [FK_BudgetRevision_ApprovedByUser] FOREIGN KEY ([ApprovedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateBudgetRevision
(
    @BudgetId        BIGINT,
    @Type            NVARCHAR(20),
    @Title           NVARCHAR(255) = NULL,
    @Description     NVARCHAR(MAX) = NULL,
    @EffectiveDate   DATE          = NULL,
    @CreatedByUserId BIGINT        = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- RevisionNumber computed atomically inside the txn. UPDLOCK + HOLDLOCK
    -- serializes concurrent creates on the same BudgetId so two callers can
    -- never both read MAX and insert the same number (dup-key race).
    DECLARE @RevisionNumber INT;
    SELECT @RevisionNumber = ISNULL(MAX([RevisionNumber]) + 1, 0)
    FROM dbo.[BudgetRevision] WITH (UPDLOCK, HOLDLOCK)
    WHERE [BudgetId] = @BudgetId;

    INSERT INTO dbo.[BudgetRevision]
        ([CreatedDatetime], [ModifiedDatetime], [BudgetId], [RevisionNumber],
         [Type], [Status], [Title], [Description], [EffectiveDate], [CreatedByUserId])
    VALUES (@Now, @Now, @BudgetId, @RevisionNumber,
            @Type, 'draft', @Title, @Description, @EffectiveDate,
            COALESCE(@CreatedByUserId, 17));

    DECLARE @NewId BIGINT = SCOPE_IDENTITY();

    COMMIT TRANSACTION;

    SELECT
        br.[Id], br.[PublicId], br.[RowVersion],
        CONVERT(VARCHAR(19), br.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), br.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        br.[BudgetId], br.[RevisionNumber], br.[Type], br.[Status],
        br.[Title], br.[Description], br.[ApprovedByUserId],
        CONVERT(VARCHAR(19), br.[ApprovedDatetime], 120) AS [ApprovedDatetime],
        CONVERT(VARCHAR(10), br.[EffectiveDate], 120)    AS [EffectiveDate],
        b.[PublicId] AS [BudgetPublicId],
        b.[ProjectId],
        b.[Status]   AS [BudgetStatus]
    FROM dbo.[BudgetRevision] br
    INNER JOIN dbo.[Budget] b ON b.[Id] = br.[BudgetId]
    WHERE br.[Id] = @NewId;
END;
GO


CREATE OR ALTER PROCEDURE ReadBudgetRevisionById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        br.[Id], br.[PublicId], br.[RowVersion],
        CONVERT(VARCHAR(19), br.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), br.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        br.[BudgetId], br.[RevisionNumber], br.[Type], br.[Status],
        br.[Title], br.[Description], br.[ApprovedByUserId],
        CONVERT(VARCHAR(19), br.[ApprovedDatetime], 120) AS [ApprovedDatetime],
        CONVERT(VARCHAR(10), br.[EffectiveDate], 120)    AS [EffectiveDate],
        b.[PublicId] AS [BudgetPublicId],
        b.[ProjectId],
        b.[Status]   AS [BudgetStatus]
    FROM dbo.[BudgetRevision] br
    INNER JOIN dbo.[Budget] b ON b.[Id] = br.[BudgetId]
    WHERE br.[Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadBudgetRevisionByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        br.[Id], br.[PublicId], br.[RowVersion],
        CONVERT(VARCHAR(19), br.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), br.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        br.[BudgetId], br.[RevisionNumber], br.[Type], br.[Status],
        br.[Title], br.[Description], br.[ApprovedByUserId],
        CONVERT(VARCHAR(19), br.[ApprovedDatetime], 120) AS [ApprovedDatetime],
        CONVERT(VARCHAR(10), br.[EffectiveDate], 120)    AS [EffectiveDate],
        b.[PublicId] AS [BudgetPublicId],
        b.[ProjectId],
        b.[Status]   AS [BudgetStatus]
    FROM dbo.[BudgetRevision] br
    INNER JOIN dbo.[Budget] b ON b.[Id] = br.[BudgetId]
    WHERE br.[PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadBudgetRevisionsByBudgetId
(
    @BudgetId BIGINT
)
AS
BEGIN
    SELECT
        br.[Id], br.[PublicId], br.[RowVersion],
        CONVERT(VARCHAR(19), br.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), br.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        br.[BudgetId], br.[RevisionNumber], br.[Type], br.[Status],
        br.[Title], br.[Description], br.[ApprovedByUserId],
        CONVERT(VARCHAR(19), br.[ApprovedDatetime], 120) AS [ApprovedDatetime],
        CONVERT(VARCHAR(10), br.[EffectiveDate], 120)    AS [EffectiveDate],
        b.[PublicId] AS [BudgetPublicId],
        b.[ProjectId],
        b.[Status]   AS [BudgetStatus]
    FROM dbo.[BudgetRevision] br
    INNER JOIN dbo.[Budget] b ON b.[Id] = br.[BudgetId]
    WHERE br.[BudgetId] = @BudgetId
    ORDER BY br.[RevisionNumber] ASC;
END;
GO


CREATE OR ALTER PROCEDURE UpdateBudgetRevisionById
(
    @Id            BIGINT,
    @RowVersion    BINARY(8),
    @Title         NVARCHAR(255) = NULL,
    @Description   NVARCHAR(MAX) = NULL,
    @EffectiveDate DATE          = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Updated INT = 0;

    -- Unconditional SET on Title / Description / EffectiveDate — these are
    -- clearable from the edit UI (callers always send the full row).
    -- Status NEVER changes via update: approval has its own sproc
    -- (ApproveBudgetRevisionById / ActivateBudgetById).
    -- RowVersion checked in the WHERE; zero rows = empty result set, which
    -- the service raises as a concurrency conflict.
    UPDATE dbo.[BudgetRevision]
    SET
        [ModifiedDatetime] = @Now,
        [Title]            = @Title,
        [Description]      = @Description,
        [EffectiveDate]    = @EffectiveDate
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    SET @Updated = @@ROWCOUNT;

    -- Unconditional COMMIT: when @Updated = 0 nothing was modified, and an
    -- in-proc ROLLBACK would zero pyodbc's implicit outer txn (error 266).
    COMMIT TRANSACTION;

    SELECT
        br.[Id], br.[PublicId], br.[RowVersion],
        CONVERT(VARCHAR(19), br.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), br.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        br.[BudgetId], br.[RevisionNumber], br.[Type], br.[Status],
        br.[Title], br.[Description], br.[ApprovedByUserId],
        CONVERT(VARCHAR(19), br.[ApprovedDatetime], 120) AS [ApprovedDatetime],
        CONVERT(VARCHAR(10), br.[EffectiveDate], 120)    AS [EffectiveDate],
        b.[PublicId] AS [BudgetPublicId],
        b.[ProjectId],
        b.[Status]   AS [BudgetStatus]
    FROM dbo.[BudgetRevision] br
    INNER JOIN dbo.[Budget] b ON b.[Id] = br.[BudgetId]
    WHERE br.[Id] = @Id AND @Updated = 1;
END;
GO


CREATE OR ALTER PROCEDURE DeleteBudgetRevisionById
(
    @Id         BIGINT,
    @RowVersion BINARY(8)
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Deleted INT = 0;
    DECLARE @LockedId BIGINT;

    -- Lock-first under UPDLOCK with the rowversion check, so the child
    -- deletes below can never need undoing. No in-proc ROLLBACK anywhere —
    -- it would zero pyodbc's implicit outer transaction (error 266).
    SELECT @LockedId = [Id]
    FROM dbo.[BudgetRevision] WITH (UPDLOCK, HOLDLOCK)
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    IF @LockedId IS NOT NULL
    BEGIN
        -- Explicit child delete inside the same txn — no FK CASCADE per
        -- project convention. Children go first or the revision delete
        -- violates FK_BudgetLineItem_BudgetRevision.
        DELETE FROM dbo.[BudgetLineItem] WHERE [BudgetRevisionId] = @LockedId;

        DELETE FROM dbo.[BudgetRevision] WHERE [Id] = @LockedId;
        SET @Deleted = @@ROWCOUNT;
    END

    COMMIT TRANSACTION;

    -- Empty result set on rowversion mismatch / missing row — the service
    -- raises it as a concurrency conflict.
    SELECT @Id AS [Id] WHERE @Deleted = 1;
END;
GO


CREATE OR ALTER PROCEDURE ApproveBudgetRevisionById
(
    @Id               BIGINT,
    @RowVersion       BINARY(8),
    @ApprovedByUserId BIGINT = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Approved INT = 0;

    -- Lock the revision row first: the UPDLOCK serializes against the
    -- line-item mutation sprocs so a line cannot be blanked between the
    -- completeness check below and the approval commit.
    DECLARE @LockedId BIGINT;
    SELECT @LockedId = [Id]
    FROM dbo.[BudgetRevision] WITH (UPDLOCK, HOLDLOCK)
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [Status] = 'draft';

    -- Completeness re-check inside the transaction (service validates first;
    -- this closes the read-then-commit race): ≥1 line, every line carrying
    -- SubCostCodeId + Amount + Price.
    IF @LockedId IS NOT NULL
       AND EXISTS (SELECT 1 FROM dbo.[BudgetLineItem] WHERE [BudgetRevisionId] = @LockedId)
       AND NOT EXISTS (SELECT 1 FROM dbo.[BudgetLineItem]
                       WHERE [BudgetRevisionId] = @LockedId
                         AND ([SubCostCodeId] IS NULL OR [Amount] IS NULL OR [Price] IS NULL))
    BEGIN
        -- The draft→approved gate stays in SQL: WHERE [Status] = 'draft'. A
        -- stale RowVersion or an already-approved row both fall through to
        -- the empty result set → service raises a concurrency/state error.
        UPDATE dbo.[BudgetRevision]
        SET
            [ModifiedDatetime] = @Now,
            [Status]           = 'approved',
            [ApprovedByUserId] = @ApprovedByUserId,
            [ApprovedDatetime] = @Now
        WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [Status] = 'draft';

        SET @Approved = @@ROWCOUNT;
    END

    -- Unconditional COMMIT: when @Approved = 0 nothing was modified, and an
    -- in-proc ROLLBACK would zero pyodbc's implicit outer txn (error 266).
    COMMIT TRANSACTION;

    SELECT
        br.[Id], br.[PublicId], br.[RowVersion],
        CONVERT(VARCHAR(19), br.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), br.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        br.[BudgetId], br.[RevisionNumber], br.[Type], br.[Status],
        br.[Title], br.[Description], br.[ApprovedByUserId],
        CONVERT(VARCHAR(19), br.[ApprovedDatetime], 120) AS [ApprovedDatetime],
        CONVERT(VARCHAR(10), br.[EffectiveDate], 120)    AS [EffectiveDate],
        b.[PublicId] AS [BudgetPublicId],
        b.[ProjectId],
        b.[Status]   AS [BudgetStatus]
    FROM dbo.[BudgetRevision] br
    INNER JOIN dbo.[Budget] b ON b.[Id] = br.[BudgetId]
    WHERE br.[Id] = @Id AND @Approved = 1;
END;
GO
