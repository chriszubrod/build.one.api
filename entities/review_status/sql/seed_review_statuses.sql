-- Default review workflow statuses
-- SortOrder gaps (10, 20, 30, 100) allow inserting intermediate statuses later
--
-- U-444 (Codex P1): 'Submitted' MUST seed with IsInitial = 1. On a fresh build
-- this file runs AFTER dbo.review_status.sql, whose backfill therefore found an
-- empty table and set nothing. Seeding all four with the column's DEFAULT of 0
-- left zero initial rows, so ReadFirstReviewStatus returned nothing and
-- BillService's auto-Submit silently skipped creating any Review — and the new
-- shape rails then refused every API repair, because a set with zero
-- initial/final/declined rows is illegal by their own definition. A fresh
-- database would have come up permanently unable to submit anything for review.
--
-- The tail block re-asserts it for databases seeded BEFORE U-444 whose backfill
-- also missed (belt and braces — the base file's backfill covers prod).

IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [Name] = 'Submitted')
BEGIN
    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [IsInitial], [Color])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Submitted', 'Submitted for review', 10, 0, 0, 1, 1, '#2196F3');
END

IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [Name] = 'In Review')
BEGIN
    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [Color])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'In Review', 'Currently being reviewed', 20, 0, 0, 1, '#FF9800');
END

IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [Name] = 'Approved')
BEGIN
    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [Color])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Approved', 'Review approved', 30, 1, 0, 1, '#4CAF50');
END

IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [Name] = 'Declined')
BEGIN
    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [Color])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Declined', 'Review declined', 100, 0, 1, 1, '#F44336');
END

-- Guaranteed non-empty: if nothing carries IsInitial (a database seeded before
-- U-444, or one where the base file's backfill ran against an empty table),
-- promote the lowest active, non-declined, non-final row — the same derivation
-- ReadFirstReviewStatus used before U-444, and the same one the base file's
-- backfill uses.
IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [IsInitial] = 1)
BEGIN
    UPDATE dbo.[ReviewStatus]
    SET [IsInitial] = 1, [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [Id] = (
        SELECT TOP 1 [Id]
        FROM dbo.[ReviewStatus]
        WHERE [IsDeclined] = 0 AND [IsActive] = 1 AND [IsFinal] = 0
        ORDER BY [SortOrder] ASC, [Id] ASC
    );
END
GO
