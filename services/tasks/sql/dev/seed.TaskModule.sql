-- Seed: Tasks module for sidebar (run in dev/non-production).
-- Inserts Module (Name='Tasks', Route='/tasks') if not exists.

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = N'Tasks')
BEGIN
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (@Now, @Now, N'Tasks', N'/tasks');
END
GO
