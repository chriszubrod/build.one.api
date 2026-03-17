IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Invoices')
BEGIN
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Invoices', '/invoice/list');
END
GO
