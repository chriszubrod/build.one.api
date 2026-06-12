GO

-- ─────────────────────────────────────────────────────────────────────
-- BudgetLineItem — child line items of BudgetRevision.
--
-- BillLineItem-style lines (SubCostCode, Qty, Rate, Amount, Markup,
-- Price) carrying a budget revision's schedule-of-values / change-order
-- deltas. ALL business fields are NULLable — the auto-save grid needs
-- partial rows; completeness is enforced at activation/approval in the
-- service layer, not at insert. Negative values are legal (CO deltas).
--
-- Semantics: Amount = pre-markup cost basis; Price = Amount×(1+Markup)
-- = contract value (like-for-like with BillLineItem).
--
-- Lock rule (enforced in BudgetLineItemService, NOT here): line items
-- of an APPROVED revision are immutable. Sprocs stay generic.
--
-- FK to BudgetRevision is plain (NO cascade) — DeleteBudgetRevisionById
-- deletes children explicitly inside its transaction.
-- ─────────────────────────────────────────────────────────────────────

IF OBJECT_ID('dbo.BudgetLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BudgetLineItem]
(
    [Id]                 BIGINT             IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]           UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
    [RowVersion]         ROWVERSION         NOT NULL,
    [CreatedDatetime]    DATETIME2(3)       NOT NULL,
    [ModifiedDatetime]   DATETIME2(3)       NULL,
    [CompanyId]          BIGINT             NOT NULL CONSTRAINT DF_BudgetLineItem_CompanyId       DEFAULT (1),
    [CreatedByUserId]    BIGINT             NOT NULL CONSTRAINT DF_BudgetLineItem_CreatedByUserId DEFAULT (17),

    [BudgetRevisionId]   BIGINT             NOT NULL,
    [SubCostCodeId]      BIGINT             NULL,
    [Description]        NVARCHAR(500)      NULL,

    [Quantity]           DECIMAL(18,4)      NULL,
    [Rate]               DECIMAL(18,4)      NULL,
    [Amount]             DECIMAL(18,2)      NULL,
    [Markup]             DECIMAL(18,4)      NULL,
    [Price]              DECIMAL(18,2)      NULL
);
END
GO

IF OBJECT_ID('dbo.BudgetLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BudgetLineItem_PublicId' AND object_id = OBJECT_ID('dbo.BudgetLineItem'))
BEGIN
    CREATE INDEX [IX_BudgetLineItem_PublicId] ON [dbo].[BudgetLineItem] ([PublicId]);
END
GO

IF OBJECT_ID('dbo.BudgetLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BudgetLineItem_BudgetRevisionId' AND object_id = OBJECT_ID('dbo.BudgetLineItem'))
BEGIN
    CREATE INDEX [IX_BudgetLineItem_BudgetRevisionId] ON [dbo].[BudgetLineItem] ([BudgetRevisionId]);
END
GO

IF OBJECT_ID('dbo.BudgetLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BudgetLineItem_SubCostCodeId' AND object_id = OBJECT_ID('dbo.BudgetLineItem'))
BEGIN
    CREATE INDEX [IX_BudgetLineItem_SubCostCodeId] ON [dbo].[BudgetLineItem] ([SubCostCodeId]);
END
GO

-- Plain FK, no cascade — DeleteBudgetRevisionById removes children
-- explicitly. Guarded on dbo.BudgetRevision existing so this file stays
-- idempotent if run before dbo.budget_revision.sql; re-run after to add.
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BudgetLineItem_BudgetRevision')
   AND OBJECT_ID('dbo.BudgetRevision', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[BudgetLineItem]
    ADD CONSTRAINT [FK_BudgetLineItem_BudgetRevision]
        FOREIGN KEY ([BudgetRevisionId]) REFERENCES [dbo].[BudgetRevision]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BudgetLineItem_SubCostCode')
BEGIN
    ALTER TABLE [dbo].[BudgetLineItem]
    ADD CONSTRAINT [FK_BudgetLineItem_SubCostCode]
        FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateBudgetLineItem
(
    @BudgetRevisionId BIGINT,
    @SubCostCodeId    BIGINT        = NULL,
    @Description      NVARCHAR(500) = NULL,
    @Quantity         DECIMAL(18,4) = NULL,
    @Rate             DECIMAL(18,4) = NULL,
    @Amount           DECIMAL(18,2) = NULL,
    @Markup           DECIMAL(18,4) = NULL,
    @Price            DECIMAL(18,2) = NULL,
    @CreatedByUserId  BIGINT        = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- Parent-status guard inside the transaction: approved revisions are
    -- immutable. The service pre-checks, but only this UPDLOCK on the
    -- revision row closes the insert-racing-approval window.
    IF NOT EXISTS (SELECT 1 FROM dbo.[BudgetRevision] WITH (UPDLOCK)
                   WHERE [Id] = @BudgetRevisionId AND [Status] = 'draft')
    BEGIN
        -- Nothing modified yet — COMMIT the empty txn (an in-proc ROLLBACK
        -- would zero pyodbc's implicit outer transaction, error 266).
        COMMIT TRANSACTION;
        RAISERROR('BudgetRevision is missing or approved — line items are immutable once a revision is approved.', 16, 1);
        RETURN;
    END

    INSERT INTO dbo.[BudgetLineItem]
        ([CreatedDatetime], [ModifiedDatetime], [BudgetRevisionId], [SubCostCodeId],
         [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BudgetRevisionId], INSERTED.[SubCostCodeId], INSERTED.[Description],
        INSERTED.[Quantity], INSERTED.[Rate], INSERTED.[Amount],
        INSERTED.[Markup], INSERTED.[Price]
    VALUES (@Now, @Now, @BudgetRevisionId, @SubCostCodeId,
            @Description, @Quantity, @Rate, @Amount, @Markup, @Price,
            COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


-- Reads JOIN up to BudgetRevision + Budget so the service gets the
-- parent-lock context (RevisionStatus) and access-control context
-- (ProjectId) in one round trip.
CREATE OR ALTER PROCEDURE ReadBudgetLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        bli.[Id], bli.[PublicId], bli.[RowVersion],
        CONVERT(VARCHAR(19), bli.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), bli.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        bli.[BudgetRevisionId], bli.[SubCostCodeId], bli.[Description],
        bli.[Quantity], bli.[Rate], bli.[Amount], bli.[Markup], bli.[Price],
        br.[Status]   AS [RevisionStatus],
        br.[Type]     AS [RevisionType],
        br.[BudgetId] AS [BudgetId],
        b.[ProjectId] AS [ProjectId]
    FROM dbo.[BudgetLineItem] bli
    INNER JOIN dbo.[BudgetRevision] br ON br.[Id] = bli.[BudgetRevisionId]
    INNER JOIN dbo.[Budget]         b  ON b.[Id]  = br.[BudgetId]
    WHERE bli.[Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadBudgetLineItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        bli.[Id], bli.[PublicId], bli.[RowVersion],
        CONVERT(VARCHAR(19), bli.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), bli.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        bli.[BudgetRevisionId], bli.[SubCostCodeId], bli.[Description],
        bli.[Quantity], bli.[Rate], bli.[Amount], bli.[Markup], bli.[Price],
        br.[Status]   AS [RevisionStatus],
        br.[Type]     AS [RevisionType],
        br.[BudgetId] AS [BudgetId],
        b.[ProjectId] AS [ProjectId]
    FROM dbo.[BudgetLineItem] bli
    INNER JOIN dbo.[BudgetRevision] br ON br.[Id] = bli.[BudgetRevisionId]
    INNER JOIN dbo.[Budget]         b  ON b.[Id]  = br.[BudgetId]
    WHERE bli.[PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadBudgetLineItemsByBudgetRevisionId
(
    @BudgetRevisionId BIGINT
)
AS
BEGIN
    SELECT
        bli.[Id], bli.[PublicId], bli.[RowVersion],
        CONVERT(VARCHAR(19), bli.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), bli.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        bli.[BudgetRevisionId], bli.[SubCostCodeId], bli.[Description],
        bli.[Quantity], bli.[Rate], bli.[Amount], bli.[Markup], bli.[Price],
        br.[Status]   AS [RevisionStatus],
        br.[Type]     AS [RevisionType],
        br.[BudgetId] AS [BudgetId],
        b.[ProjectId] AS [ProjectId]
    FROM dbo.[BudgetLineItem] bli
    INNER JOIN dbo.[BudgetRevision] br ON br.[Id] = bli.[BudgetRevisionId]
    INNER JOIN dbo.[Budget]         b  ON b.[Id]  = br.[BudgetId]
    WHERE bli.[BudgetRevisionId] = @BudgetRevisionId
    ORDER BY bli.[Id];
END;
GO


-- Business fields are SET UNCONDITIONALLY — the auto-save grid sends
-- the full row state on every save, and NULL means "cleared". Do NOT
-- add CASE WHEN preserve-on-NULL guards here (would make clearing a
-- cell impossible). Rowversion check via WHERE + empty result =
-- concurrency conflict, surfaced in the repository layer.
CREATE OR ALTER PROCEDURE UpdateBudgetLineItemById
(
    @Id            BIGINT,
    @RowVersion    BINARY(8),
    @SubCostCodeId BIGINT        = NULL,
    @Description   NVARCHAR(500) = NULL,
    @Quantity      DECIMAL(18,4) = NULL,
    @Rate          DECIMAL(18,4) = NULL,
    @Amount        DECIMAL(18,2) = NULL,
    @Markup        DECIMAL(18,4) = NULL,
    @Price         DECIMAL(18,2) = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the SELECT.
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- Parent-status guard inside the transaction: approved revisions are
    -- immutable (line RowVersions don't change on approval, so the
    -- rowversion WHERE alone cannot catch an update racing the approval).
    -- Unknown @Id falls through to the rowversion-checked UPDATE's empty
    -- result set so missing rows still surface as the normal conflict path.
    IF EXISTS (SELECT 1 FROM dbo.[BudgetLineItem] WHERE [Id] = @Id)
       AND NOT EXISTS (SELECT 1
                       FROM dbo.[BudgetRevision] br WITH (UPDLOCK)
                       INNER JOIN dbo.[BudgetLineItem] li ON li.[BudgetRevisionId] = br.[Id]
                       WHERE li.[Id] = @Id AND br.[Status] = 'draft')
    BEGIN
        -- Nothing modified yet — COMMIT the empty txn (an in-proc ROLLBACK
        -- would zero pyodbc's implicit outer transaction, error 266).
        COMMIT TRANSACTION;
        RAISERROR('BudgetRevision is approved — line items are immutable once a revision is approved.', 16, 1);
        RETURN;
    END

    UPDATE dbo.[BudgetLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [SubCostCodeId]    = @SubCostCodeId,
        [Description]      = @Description,
        [Quantity]         = @Quantity,
        [Rate]             = @Rate,
        [Amount]           = @Amount,
        [Markup]           = @Markup,
        [Price]            = @Price
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BudgetRevisionId], INSERTED.[SubCostCodeId], INSERTED.[Description],
        INSERTED.[Quantity], INSERTED.[Rate], INSERTED.[Amount],
        INSERTED.[Markup], INSERTED.[Price]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- Rowversion-checked delete. Empty result (no DELETED row) = concurrency
-- conflict or already gone — surfaced in the repository layer.
CREATE OR ALTER PROCEDURE DeleteBudgetLineItemById
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

    -- Parent-status guard: approved revisions are immutable (see
    -- UpdateBudgetLineItemById for the race this closes). Unknown @Id falls
    -- through to the DELETE's empty result set (normal conflict path).
    IF EXISTS (SELECT 1 FROM dbo.[BudgetLineItem] WHERE [Id] = @Id)
       AND NOT EXISTS (SELECT 1
                       FROM dbo.[BudgetRevision] br WITH (UPDLOCK)
                       INNER JOIN dbo.[BudgetLineItem] li ON li.[BudgetRevisionId] = br.[Id]
                       WHERE li.[Id] = @Id AND br.[Status] = 'draft')
    BEGIN
        -- Nothing modified yet — COMMIT the empty txn (an in-proc ROLLBACK
        -- would zero pyodbc's implicit outer transaction, error 266).
        COMMIT TRANSACTION;
        RAISERROR('BudgetRevision is approved — line items are immutable once a revision is approved.', 16, 1);
        RETURN;
    END

    DELETE FROM dbo.[BudgetLineItem]
    OUTPUT DELETED.[Id]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
