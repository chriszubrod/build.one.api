GO

-- ─────────────────────────────────────────────────────────────────────
-- Budget — customer-facing contract value per Project.
-- One non-archived Budget per Project (filtered unique index below);
-- 'archived' is reserved for future re-baselining.
--
-- Status workflow: draft → active (via ActivateBudgetById ONLY) →
-- archived (future). Status NEVER changes via UpdateBudgetById.
--
-- Children: dbo.BudgetRevision (Rev 0 'original' + change-order deltas)
-- → dbo.BudgetLineItem. No FK CASCADE per project convention —
-- application code deletes children explicitly.
-- ─────────────────────────────────────────────────────────────────────

IF OBJECT_ID('dbo.Budget', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Budget]
(
    [Id]                 BIGINT             IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]           UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
    [RowVersion]         ROWVERSION         NOT NULL,
    [CreatedDatetime]    DATETIME2(3)       NOT NULL,
    [ModifiedDatetime]   DATETIME2(3)       NULL,
    [CompanyId]          BIGINT             NOT NULL CONSTRAINT DF_Budget_CompanyId       DEFAULT (1),
    [CreatedByUserId]    BIGINT             NOT NULL CONSTRAINT DF_Budget_CreatedByUserId DEFAULT (17),

    [ProjectId]          BIGINT             NOT NULL,

    -- draft → active → archived.
    [Status]             NVARCHAR(20)       NOT NULL DEFAULT 'draft',

    [Notes]              NVARCHAR(MAX)      NULL
);
END
GO

-- One live (non-archived) Budget per Project. A plain UNIQUE on ProjectId
-- would permanently block re-baselining — the filter exempts archived rows.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Budget_ProjectId_Active' AND object_id = OBJECT_ID('dbo.Budget'))
BEGIN
    CREATE UNIQUE INDEX [UQ_Budget_ProjectId_Active]
        ON [dbo].[Budget] ([ProjectId])
        WHERE [Status] <> 'archived';
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Budget_PublicId' AND object_id = OBJECT_ID('dbo.Budget'))
BEGIN
    CREATE INDEX [IX_Budget_PublicId] ON [dbo].[Budget] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Budget_ProjectId' AND object_id = OBJECT_ID('dbo.Budget'))
BEGIN
    CREATE INDEX [IX_Budget_ProjectId] ON [dbo].[Budget] ([ProjectId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Budget_Project')
BEGIN
    ALTER TABLE [dbo].[Budget]
    ADD CONSTRAINT [FK_Budget_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateBudget
(
    @ProjectId       BIGINT,
    @Notes           NVARCHAR(MAX) = NULL,
    @CreatedByUserId BIGINT        = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- Status defaults to 'draft' via the column DEFAULT.
    INSERT INTO dbo.[Budget]
        ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [Notes], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId], INSERTED.[Status], INSERTED.[Notes]
    VALUES (@Now, @Now, @ProjectId, @Notes, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


-- Actor-scoped list. Inline dbo.UserCanAccessProject (Invoice/ContractLabor
-- shape from gap1_list_sprocs_scoped.sql). FAILS CLOSED on NULL actor —
-- the UDF returns 0 for a NULL @ActorUserId; deliberately NO
-- 'OR @ActorUserId IS NULL' legacy bypass branch.
-- Archived budgets are excluded — deliberately matched with
-- ReadBudgetListRollups' filter so every listed budget has a real rollup row
-- (a mismatch would render archived budgets with fabricated $0 rollups).
CREATE OR ALTER PROCEDURE ReadBudgets
(
    @ActorUserId        BIGINT = NULL,
    @ActorIsSystemAdmin BIT    = NULL
)
AS
BEGIN
    SELECT
        b.[Id], b.[PublicId], b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[ProjectId], b.[Status], b.[Notes],
        p.[Name]     AS [ProjectName],
        p.[PublicId] AS [ProjectPublicId]
    FROM dbo.[Budget] b
    LEFT JOIN dbo.[Project] p ON p.[Id] = b.[ProjectId]
    WHERE b.[Status] <> 'archived'
      AND dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, b.[ProjectId]) = 1
    ORDER BY p.[Name], b.[CreatedDatetime] DESC;
END;
GO


CREATE OR ALTER PROCEDURE ReadBudgetById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        b.[Id], b.[PublicId], b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[ProjectId], b.[Status], b.[Notes],
        p.[Name]     AS [ProjectName],
        p.[PublicId] AS [ProjectPublicId]
    FROM dbo.[Budget] b
    LEFT JOIN dbo.[Project] p ON p.[Id] = b.[ProjectId]
    WHERE b.[Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadBudgetByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        b.[Id], b.[PublicId], b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[ProjectId], b.[Status], b.[Notes],
        p.[Name]     AS [ProjectName],
        p.[PublicId] AS [ProjectPublicId]
    FROM dbo.[Budget] b
    LEFT JOIN dbo.[Project] p ON p.[Id] = b.[ProjectId]
    WHERE b.[PublicId] = @PublicId;
END;
GO


-- The live (non-archived) Budget for a Project — at most one exists per the
-- UQ_Budget_ProjectId_Active filtered unique index. Archived budgets are
-- deliberately excluded; this powers both the create pre-check and the
-- /get/budget/by-project route.
CREATE OR ALTER PROCEDURE ReadBudgetByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    SELECT TOP 1
        b.[Id], b.[PublicId], b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[ProjectId], b.[Status], b.[Notes],
        p.[Name]     AS [ProjectName],
        p.[PublicId] AS [ProjectPublicId]
    FROM dbo.[Budget] b
    LEFT JOIN dbo.[Project] p ON p.[Id] = b.[ProjectId]
    WHERE b.[ProjectId] = @ProjectId
      AND b.[Status] <> 'archived';
END;
GO


-- Notes only — Status NEVER changes via update (ActivateBudgetById owns the
-- draft→active transition). @Notes is an unconditional SET (clearable free
-- text per house convention); the service resolves preserve-vs-clear before
-- calling. Rowversion check via WHERE — an empty result set means a
-- concurrency conflict, raised in the service layer.
CREATE OR ALTER PROCEDURE UpdateBudgetById
(
    @Id         BIGINT,
    @RowVersion BINARY(8),
    @Notes      NVARCHAR(MAX) = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Budget]
    SET
        [ModifiedDatetime] = @Now,
        [Notes]            = @Notes
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId], INSERTED.[Status], INSERTED.[Notes]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- Rowversion-guarded delete. Children (BudgetRevision → BudgetLineItem) are
-- deleted by the service BEFORE this is called — explicit child deletes, no
-- FK cascade. DeletedCount = 0 means concurrency conflict (or already gone),
-- raised in the service layer.
CREATE OR ALTER PROCEDURE DeleteBudgetById
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

    DELETE FROM dbo.[Budget]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    SELECT @@ROWCOUNT AS [DeletedCount];

    COMMIT TRANSACTION;
END;
GO


-- Single-transaction activation: Budget draft→active + the 'original'
-- revision (Rev 0) draft→approved with approver stamps. Validate-first /
-- always-COMMIT shape: an EMPTY result set means a concurrency conflict,
-- wrong state, or incomplete Rev 0 lines; the service raises a friendly
-- 409-style error. Line-item completeness (≥1 line, SCC + Amount + Price
-- non-null) is validated by the service first AND re-checked here in-txn.
CREATE OR ALTER PROCEDURE ActivateBudgetById
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

    DECLARE @Activated BIT = 0;

    -- Validate-first, mutate-after: no path needs an in-proc ROLLBACK
    -- (which would zero pyodbc's implicit outer transaction — error 266).
    -- Lock Rev 0 up front; the UPDLOCK serializes against the line-item
    -- mutation sprocs, closing the blank-a-line-during-activation race.
    DECLARE @Rev0Id BIGINT;
    SELECT @Rev0Id = [Id]
    FROM dbo.[BudgetRevision] WITH (UPDLOCK, HOLDLOCK)
    WHERE [BudgetId] = @Id
      AND [Type] = 'original'
      AND [Status] = 'draft';

    -- Completeness re-checked inside the transaction (the service validates
    -- first, but a line can be blanked between that read and this commit):
    -- Rev 0 exists, has at least one line, every line carries SCC+Amount+Price.
    IF @Rev0Id IS NOT NULL
       AND EXISTS (SELECT 1 FROM dbo.[BudgetLineItem] WHERE [BudgetRevisionId] = @Rev0Id)
       AND NOT EXISTS (SELECT 1 FROM dbo.[BudgetLineItem]
                       WHERE [BudgetRevisionId] = @Rev0Id
                         AND ([SubCostCodeId] IS NULL OR [Amount] IS NULL OR [Price] IS NULL))
    BEGIN
        UPDATE dbo.[Budget]
        SET
            [ModifiedDatetime] = @Now,
            [Status]           = 'active'
        WHERE [Id] = @Id
          AND [Status] = 'draft'
          AND [RowVersion] = @RowVersion;

        IF @@ROWCOUNT = 1
        BEGIN
            -- Rev 0 is locked + known draft, so this UPDATE cannot miss.
            -- No COALESCE-to-17: approval attribution must be a real actor;
            -- the service refuses activation when no user is in context.
            UPDATE dbo.[BudgetRevision]
            SET
                [ModifiedDatetime] = @Now,
                [Status]           = 'approved',
                [ApprovedByUserId] = @ApprovedByUserId,
                [ApprovedDatetime] = @Now
            WHERE [Id] = @Rev0Id;

            SET @Activated = 1;
        END
    END

    COMMIT TRANSACTION;

    -- Empty result set on any validation/concurrency failure — the service
    -- maps it to a conflict. Budget + Rev 0 only ever flip together.
    SELECT
        b.[Id], b.[PublicId], b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[ProjectId], b.[Status], b.[Notes],
        p.[Name]     AS [ProjectName],
        p.[PublicId] AS [ProjectPublicId]
    FROM dbo.[Budget] b
    LEFT JOIN dbo.[Project] p ON p.[Id] = b.[ProjectId]
    WHERE b.[Id] = @Id AND @Activated = 1;
END;
GO
