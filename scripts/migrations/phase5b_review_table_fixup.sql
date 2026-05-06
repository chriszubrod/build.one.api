-- Phase 5 fixup — add CompanyId to dbo.Review.
-- The original phase5_company_id_columns migration mistakenly targeted
-- dead-schema dbo.ReviewEntry instead of the live dbo.Review table.
-- This script adds CompanyId to dbo.Review and backfills.
-- Idempotent.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Review'))
    ALTER TABLE [dbo].[Review] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Review_Company')
    ALTER TABLE [dbo].[Review] ADD CONSTRAINT [FK_Review_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Review_CompanyId' AND object_id=OBJECT_ID('dbo.Review'))
    CREATE INDEX [IX_Review_CompanyId] ON [dbo].[Review] ([CompanyId]);
GO

DECLARE @DefaultCompanyId BIGINT;
SELECT TOP 1 @DefaultCompanyId = [Id] FROM dbo.[Company] ORDER BY [Id] ASC;

UPDATE [dbo].[Review] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('Review: ', @@ROWCOUNT, ' row(s) backfilled.');
GO

PRINT 'Phase 5 Review fixup complete.';
