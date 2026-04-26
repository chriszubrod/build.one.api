-- Add SourceEmailMessageId BIGINT NULL FK + index to dbo.Bill,
-- dbo.Expense, dbo.BillCredit. Each FK points back to dbo.EmailMessage(Id).
--
-- Many-to-one — one EmailMessage can spawn many Bills/Expenses/BillCredits
-- (e.g. an attachment-packet email with multiple PDFs). The reverse —
-- "find every email in the thread that produced this bill" — works
-- without a join table by joining EmailMessage on ConversationId from the
-- source email's row.
--
-- Sprocs are intentionally NOT touched in this migration; CreateBill /
-- CreateExpense / CreateBillCredit will be extended when the Phase 2
-- email agent actually starts populating these. NULL is the default
-- (manually-created entities) and existing sprocs ignore the new column.
--
-- Idempotent: ADD COLUMN guards on sys.columns; FK + index guards on
-- sys.foreign_keys / sys.indexes.

GO

-- dbo.Bill ------------------------------------------------------------------

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'SourceEmailMessageId' AND object_id = OBJECT_ID('dbo.Bill')
)
BEGIN
    ALTER TABLE dbo.[Bill] ADD [SourceEmailMessageId] BIGINT NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_Bill_SourceEmailMessage' AND parent_object_id = OBJECT_ID('dbo.Bill')
)
BEGIN
    ALTER TABLE dbo.[Bill]
    ADD CONSTRAINT FK_Bill_SourceEmailMessage
        FOREIGN KEY ([SourceEmailMessageId])
        REFERENCES dbo.[EmailMessage]([Id]);
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Bill_SourceEmailMessageId' AND object_id = OBJECT_ID('dbo.Bill')
)
BEGIN
    CREATE INDEX IX_Bill_SourceEmailMessageId ON dbo.[Bill] ([SourceEmailMessageId])
        WHERE [SourceEmailMessageId] IS NOT NULL;
END
GO

-- dbo.Expense ---------------------------------------------------------------

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'SourceEmailMessageId' AND object_id = OBJECT_ID('dbo.Expense')
)
BEGIN
    ALTER TABLE dbo.[Expense] ADD [SourceEmailMessageId] BIGINT NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_Expense_SourceEmailMessage' AND parent_object_id = OBJECT_ID('dbo.Expense')
)
BEGIN
    ALTER TABLE dbo.[Expense]
    ADD CONSTRAINT FK_Expense_SourceEmailMessage
        FOREIGN KEY ([SourceEmailMessageId])
        REFERENCES dbo.[EmailMessage]([Id]);
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Expense_SourceEmailMessageId' AND object_id = OBJECT_ID('dbo.Expense')
)
BEGIN
    CREATE INDEX IX_Expense_SourceEmailMessageId ON dbo.[Expense] ([SourceEmailMessageId])
        WHERE [SourceEmailMessageId] IS NOT NULL;
END
GO

-- dbo.BillCredit ------------------------------------------------------------

IF OBJECT_ID('dbo.BillCredit', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'SourceEmailMessageId' AND object_id = OBJECT_ID('dbo.BillCredit')
)
BEGIN
    ALTER TABLE dbo.[BillCredit] ADD [SourceEmailMessageId] BIGINT NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_BillCredit_SourceEmailMessage' AND parent_object_id = OBJECT_ID('dbo.BillCredit')
)
BEGIN
    ALTER TABLE dbo.[BillCredit]
    ADD CONSTRAINT FK_BillCredit_SourceEmailMessage
        FOREIGN KEY ([SourceEmailMessageId])
        REFERENCES dbo.[EmailMessage]([Id]);
END
GO

IF OBJECT_ID('dbo.BillCredit', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_BillCredit_SourceEmailMessageId' AND object_id = OBJECT_ID('dbo.BillCredit')
)
BEGIN
    CREATE INDEX IX_BillCredit_SourceEmailMessageId ON dbo.[BillCredit] ([SourceEmailMessageId])
        WHERE [SourceEmailMessageId] IS NOT NULL;
END
GO
