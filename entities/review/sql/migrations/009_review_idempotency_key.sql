-- =============================================================================
-- U-541 — Client idempotency key on dbo.Review for transactional review decisions.
--
-- Nullable column + filtered unique index so legacy NULL rows and every existing
-- writer (email path, web /advance) stay untouched.
--
-- Idempotent. Safe to re-run. Prod apply is operator-owned.
-- Canonical CreateReview param lives in entities/review/sql/dbo.review.sql.
-- =============================================================================

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.Review', 'IdempotencyKey') IS NULL
BEGIN
    ALTER TABLE [dbo].[Review] ADD [IdempotencyKey] UNIQUEIDENTIFIER NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE [name] = N'UX_Review_IdempotencyKey'
         AND [object_id] = OBJECT_ID(N'dbo.Review')
   )
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX [UX_Review_IdempotencyKey]
        ON [dbo].[Review] ([IdempotencyKey])
        WHERE [IdempotencyKey] IS NOT NULL;
END
GO

PRINT 'migrations/009_review_idempotency_key applied (column + filtered unique index).';
GO
