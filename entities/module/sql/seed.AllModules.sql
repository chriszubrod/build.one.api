-- =============================================================================
-- Seed all application modules for RBAC enforcement.
-- Each Module.Name matches a constant in shared/rbac_constants.py.
-- Idempotent — safe to re-run.
-- =============================================================================

-- Financial entities
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Bills')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Bills', '/bill/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Bill Credits')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Bill Credits', '/bill-credit/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Expenses')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Expenses', '/expense/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Invoices')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Invoices', '/invoice/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Contract Labor')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Contract Labor', '/contract-labor/list');
GO

-- Reference data
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Vendors')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Vendors', '/vendor/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Customers')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Customers', '/customer/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Projects')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Projects', '/project/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Cost Codes')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Cost Codes', '/cost-code/list');
GO

-- Inbox & email processing
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Inbox')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Inbox', '/inbox');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Email Threads')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Email Threads', '/email-threads');
GO

-- AI & processing
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Anomaly Detection')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Anomaly Detection', '/anomaly');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Categorization')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Categorization', '/categorization');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Copilot')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Copilot', '/copilot');
GO

-- Attachments & documents
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Attachments')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Attachments', '/attachments');
GO

-- Search
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Search')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Search', '/search');
GO

-- Administration
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Users')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Users', '/user/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Roles')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Roles', '/role/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Organizations')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Organizations', '/organization/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Companies')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Companies', '/company/list');
GO

-- Integrations
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Integrations')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Integrations', '/integration/list');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'QBO Sync')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'QBO Sync', '/sync');
GO

-- Dashboard
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Dashboard')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Dashboard', '/dashboard');
GO

-- Classification overrides (admin)
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Classification Overrides')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Classification Overrides', '/admin/overrides');
GO

-- Pending actions
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Pending Actions')
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Pending Actions', '/pending-actions');
GO
