-- U-596 (2026-10-02): EditedSinceAggregation — a durable record that something OTHER than the
-- time-entry aggregator wrote this row. The aggregator (dbo.AggregateTimeEntryOnSubmit) creates
-- and rewrites labor rows straight from a day's time logs and never sets this bit; the generic
-- update sprocs — every other writer: a PM's bill edit, a reviewer's decision, the billing run —
-- set it to 1. dbo.IsTimeEntryLaborUntouched reads it: a row anyone else has written is never
-- rebuilt from the logs again (a changing resubmit is refused), so an office correction cannot be
-- silently undone. Idempotent; additive; safe to re-run.
--
-- BACKFILL: every row that exists when the column is added is set to 1. Corrections made
-- before this marker existed left no trace the predicate can read, so pre-existing labor is
-- never auto-rebuilt — a late log on such a day is refused and handled by the office, as
-- before. Only labor the aggregator creates AFTER this runs starts at 0. (Dynamic SQL: the
-- column cannot be referenced in the batch that adds it.)
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.ContractLabor') AND name = 'EditedSinceAggregation'
)
BEGIN
    ALTER TABLE dbo.[ContractLabor]
    ADD [EditedSinceAggregation] BIT NOT NULL CONSTRAINT [DF_ContractLabor_EditedSinceAggregation] DEFAULT (0);
    EXEC(N'UPDATE dbo.[ContractLabor] SET [EditedSinceAggregation] = 1;');
END
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.ContractLaborLineItem') AND name = 'EditedSinceAggregation'
)
BEGIN
    ALTER TABLE dbo.[ContractLaborLineItem]
    ADD [EditedSinceAggregation] BIT NOT NULL CONSTRAINT [DF_ContractLaborLineItem_EditedSinceAggregation] DEFAULT (0);
    EXEC(N'UPDATE dbo.[ContractLaborLineItem] SET [EditedSinceAggregation] = 1;');
END
GO
