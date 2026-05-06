-- Seed Project Manager and Owner Role rows used by the review-submit
-- notification system to qualify UserProject relationships:
-- - 'Project Manager' resolves to To: on review notifications
-- - 'Owner' resolves to Cc:
--
-- Idempotent (IF NOT EXISTS guards). Safe to re-run. Phase 2 of the
-- access-control rebuild also seeds 'Project Manager' — the IF NOT
-- EXISTS guard there will skip this row when both seeds run.

IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'Project Manager')
BEGIN
    DECLARE @NowPM DATETIME2(3) = SYSUTCDATETIME();
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@NowPM, @NowPM, 'Project Manager');
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'Owner')
BEGIN
    DECLARE @NowOwner DATETIME2(3) = SYSUTCDATETIME();
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@NowOwner, @NowOwner, 'Owner');
END
GO
