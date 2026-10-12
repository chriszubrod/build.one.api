-- U-652: migrate dbo.ExpenseCodingItem rows to dbo.CodingSuggestion (purchase grain).
--
-- Required apply order (stop the API before running):
--   (1) entities/coding_suggestion/sql/dbo.coding_suggestion.sql
--   (2) integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql
--   (3) entities/expense/sql/dbo.expense.sql (at least ReadUncodedCompletedExpenseCandidates)
--   (4) this migration
--
-- Run: ./.venv/bin/python scripts/run_sql.py scripts/migrations/u652_coding_suggestion.sql

SET XACT_ABORT ON;
GO

IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NULL
BEGIN
    PRINT N'U-652: dbo.ExpenseCodingItem already migrated — nothing to do.';
END
ELSE
BEGIN
    IF OBJECT_ID('dbo.CodingSuggestion', 'U') IS NULL
    BEGIN
        THROW 65201, N'U-652: dbo.CodingSuggestion must exist before running this migration.', 1;
    END;

    -- Any module (procedure, function, view, TRIGGER) that DEPENDS on dbo.ExpenseCodingItem would
    -- break once it is renamed. sys.sql_expression_dependencies records real references (not
    -- comments) for every module type. Only the 14 dbo procedures this migration drops are exempt,
    -- matched by object id, so a same-named procedure in another schema still blocks.
    IF EXISTS (
        SELECT 1
        FROM sys.sql_expression_dependencies d
        WHERE d.referencing_class = 1
          AND d.referenced_entity_name = N'ExpenseCodingItem'
          AND (d.referenced_schema_name IS NULL OR d.referenced_schema_name = N'dbo')
          AND d.referencing_id NOT IN (
              SELECT x.id
              FROM (VALUES
                  (OBJECT_ID(N'dbo.UpsertExpenseCodingItem', N'P')),
                  (OBJECT_ID(N'dbo.ReadExpenseCodingItemByPublicId', N'P')),
                  (OBJECT_ID(N'dbo.RecordExpenseCodingSuggestion', N'P')),
                  (OBJECT_ID(N'dbo.RecordExpenseCodingFlag', N'P')),
                  (OBJECT_ID(N'dbo.ClaimExpenseCodingItem', N'P')),
                  (OBJECT_ID(N'dbo.ReleaseExpenseCodingItem', N'P')),
                  (OBJECT_ID(N'dbo.RecordExpenseCodingConfirmation', N'P')),
                  (OBJECT_ID(N'dbo.MarkExpenseCodingEnqueued', N'P')),
                  (OBJECT_ID(N'dbo.MarkExpenseCodingWritten', N'P')),
                  (OBJECT_ID(N'dbo.MarkExpenseCodingChangedInQbo', N'P')),
                  (OBJECT_ID(N'dbo.MarkExpenseCodingError', N'P')),
                  (OBJECT_ID(N'dbo.ReadExpenseCodingStateByExpenseIds', N'P')),
                  (OBJECT_ID(N'dbo.ReadExternallyResolvedCodingItemCandidates', N'P')),
                  (OBJECT_ID(N'dbo.MarkExpenseCodingResolvedExternally', N'P'))
              ) AS x(id)
              WHERE x.id IS NOT NULL
          )
    )
    BEGIN
        THROW 65206, N'U-652: a module still depends on dbo.ExpenseCodingItem — apply steps (1)-(3) before this migration.', 1;
    END;

    BEGIN TRANSACTION;

    IF EXISTS (
        SELECT 1
        FROM dbo.[ExpenseCodingItem] eci
        GROUP BY eci.[RealmId], eci.[QboPurchaseQboId]
        HAVING COUNT(*) > 1
    )
    BEGIN
        THROW 65202, N'U-652: duplicate ExpenseCodingItem rows for the same (RealmId, QboPurchaseQboId).', 1;
    END;

    INSERT INTO dbo.[CodingSuggestion] (
        [PublicId],
        [CompanyId],
        [CreatedByUserId],
        [CreatedDatetime],
        [ModifiedDatetime],
        [TargetKind],
        [ExpenseId],
        [VendorId],
        [QboPurchaseId],
        [QboPurchaseQboId],
        [RealmId],
        [SyncTokenAtSuggest],
        [Status],
        [SuggestedProjectId],
        [SuggestedSubCostCodeId],
        [SuggestedDescription],
        [SuggestionSource],
        [SuggestionReason],
        [SuggestionConfidence],
        [SuggestedAt],
        [ConfirmedProjectId],
        [ConfirmedSubCostCodeId],
        [ConfirmedDescription],
        [WasOverridden],
        [ConfirmedByUserId],
        [ConfirmedAt],
        [FlagReason],
        [FlaggedAt],
        [WrittenAt],
        [WriteError],
        [ClaimedByUserId],
        [ClaimedAt]
    )
    SELECT
        eci.[PublicId],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        eci.[CreatedDatetime],
        eci.[ModifiedDatetime],
        N'expense',
        e.[Id],
        eci.[VendorId],
        eci.[QboPurchaseId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt]
    FROM dbo.[ExpenseCodingItem] eci
    LEFT JOIN qbo.[Purchase] p
        ON p.[Id] = eci.[QboPurchaseId]
    LEFT JOIN dbo.[Expense] e
        ON e.[QboId] = COALESCE(p.[QboId], eci.[QboPurchaseQboId])
       AND e.[RealmId] = eci.[RealmId]
    WHERE NOT EXISTS (
        SELECT 1
        FROM dbo.[CodingSuggestion] cs
        WHERE cs.[PublicId] = eci.[PublicId]
    );

    DECLARE @OldCount INT = (SELECT COUNT(*) FROM dbo.[ExpenseCodingItem]);
    DECLARE @NewCount INT = (
        SELECT COUNT(*)
        FROM dbo.[CodingSuggestion] cs
        WHERE EXISTS (
            SELECT 1 FROM dbo.[ExpenseCodingItem] eci WHERE eci.[PublicId] = cs.[PublicId]
        )
    );

    IF @OldCount <> @NewCount
    BEGIN
        THROW 65203, N'U-652: CodingSuggestion row count does not match ExpenseCodingItem.', 1;
    END;

    DECLARE @Status TABLE ([Status] NVARCHAR(30), Cnt INT);
    INSERT INTO @Status ([Status], Cnt)
    SELECT [Status], COUNT(*) FROM dbo.[ExpenseCodingItem] GROUP BY [Status];

    DECLARE @Mismatch NVARCHAR(30);
    SELECT TOP (1) @Mismatch = o.[Status]
    FROM @Status o
    WHERE o.Cnt <> (
        SELECT COUNT(*)
        FROM dbo.[CodingSuggestion] cs
        INNER JOIN dbo.[ExpenseCodingItem] eci ON eci.[PublicId] = cs.[PublicId]
        WHERE cs.[Status] = o.[Status]
    );

    IF @Mismatch IS NOT NULL
    BEGIN
        THROW 65204, N'U-652: per-Status counts mismatch after copy.', 1;
    END;

    IF EXISTS (
        SELECT 1
        FROM dbo.[ExpenseCodingItem] eci
        WHERE NOT EXISTS (
            SELECT 1 FROM dbo.[CodingSuggestion] cs WHERE cs.[PublicId] = eci.[PublicId]
        )
    )
    BEGIN
        THROW 65205, N'U-652: not every ExpenseCodingItem PublicId was copied.', 1;
    END;

    EXEC sp_rename 'dbo.ExpenseCodingItem', 'ExpenseCodingItem_u652_backup';

    IF OBJECT_ID('dbo.UpsertExpenseCodingItem', 'P') IS NOT NULL DROP PROCEDURE dbo.UpsertExpenseCodingItem;
    IF OBJECT_ID('dbo.ReadExpenseCodingItemByPublicId', 'P') IS NOT NULL DROP PROCEDURE dbo.ReadExpenseCodingItemByPublicId;
    IF OBJECT_ID('dbo.RecordExpenseCodingSuggestion', 'P') IS NOT NULL DROP PROCEDURE dbo.RecordExpenseCodingSuggestion;
    IF OBJECT_ID('dbo.RecordExpenseCodingFlag', 'P') IS NOT NULL DROP PROCEDURE dbo.RecordExpenseCodingFlag;
    IF OBJECT_ID('dbo.ClaimExpenseCodingItem', 'P') IS NOT NULL DROP PROCEDURE dbo.ClaimExpenseCodingItem;
    IF OBJECT_ID('dbo.ReleaseExpenseCodingItem', 'P') IS NOT NULL DROP PROCEDURE dbo.ReleaseExpenseCodingItem;
    IF OBJECT_ID('dbo.RecordExpenseCodingConfirmation', 'P') IS NOT NULL DROP PROCEDURE dbo.RecordExpenseCodingConfirmation;
    IF OBJECT_ID('dbo.MarkExpenseCodingEnqueued', 'P') IS NOT NULL DROP PROCEDURE dbo.MarkExpenseCodingEnqueued;
    IF OBJECT_ID('dbo.MarkExpenseCodingWritten', 'P') IS NOT NULL DROP PROCEDURE dbo.MarkExpenseCodingWritten;
    IF OBJECT_ID('dbo.MarkExpenseCodingChangedInQbo', 'P') IS NOT NULL DROP PROCEDURE dbo.MarkExpenseCodingChangedInQbo;
    IF OBJECT_ID('dbo.MarkExpenseCodingError', 'P') IS NOT NULL DROP PROCEDURE dbo.MarkExpenseCodingError;
    IF OBJECT_ID('dbo.ReadExpenseCodingStateByExpenseIds', 'P') IS NOT NULL DROP PROCEDURE dbo.ReadExpenseCodingStateByExpenseIds;
    IF OBJECT_ID('dbo.ReadExternallyResolvedCodingItemCandidates', 'P') IS NOT NULL DROP PROCEDURE dbo.ReadExternallyResolvedCodingItemCandidates;
    IF OBJECT_ID('dbo.MarkExpenseCodingResolvedExternally', 'P') IS NOT NULL DROP PROCEDURE dbo.MarkExpenseCodingResolvedExternally;

    COMMIT TRANSACTION;

    PRINT N'U-652: migrated ' + CAST(@OldCount AS NVARCHAR(20)) + N' ExpenseCodingItem row(s) to CodingSuggestion.';
END;
GO
