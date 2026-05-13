-- Stack module grants onto claude_agent for the MCP read-tool surface.
--
-- The base seed (seed.claude_agent.sql) gives claude_agent the
-- "Agent Orchestrator" role (zero module grants — scout-shape).
-- The MCP server's 10 read tools call the API directly, so claude_agent
-- needs broader read access. We stack 5 specialist roles and add an
-- additive UserModule for Attachments (which no specialist owns).
--
-- Stacked roles (UserRole rows):
--   Vendor Specialist     -> Vendors module
--   Customer Specialist   -> Customers + Projects (parent read)
--   Project Specialist    -> Projects + Customers (parent read)
--   Bill Specialist       -> Bills + Vendors
--   Expense Specialist    -> Expenses (+ adjacent reads)
--
-- Additive grant (UserModule row, read-only by definition):
--   Attachments           -> covers /get/bill-line-item-attachment/...
--                          and /get/expense-line-item-attachment/...
--
-- Idempotent — safe to re-run.
--
-- AFTER running this, also re-run the UserProject backfill so claude_agent
-- gets a UserProject row per Project (gates Bill/Expense/Project list paths):
--   .venv/bin/python scripts/run_sql.py scripts/migrations/gap1_agent_user_project_backfill.sql
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.claude_agent_mcp_grants.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();

DECLARE @UserId BIGINT = (
    SELECT u.Id FROM dbo.[User] u
    INNER JOIN dbo.Auth a ON a.UserId = u.Id
    WHERE a.Username = 'claude_agent'
);
DECLARE @CompanyId BIGINT = (SELECT Id FROM dbo.Company WHERE Name = 'Rogers Build, Inc.');

DECLARE @VendorRoleId   BIGINT = (SELECT Id FROM dbo.Role WHERE Name = 'Vendor Specialist');
DECLARE @CustomerRoleId BIGINT = (SELECT Id FROM dbo.Role WHERE Name = 'Customer Specialist');
DECLARE @ProjectRoleId  BIGINT = (SELECT Id FROM dbo.Role WHERE Name = 'Project Specialist');
DECLARE @BillRoleId     BIGINT = (SELECT Id FROM dbo.Role WHERE Name = 'Bill Specialist');
DECLARE @ExpenseRoleId  BIGINT = (SELECT Id FROM dbo.Role WHERE Name = 'Expense Specialist');

DECLARE @AttachmentsModuleId BIGINT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Attachments');

IF @UserId IS NULL OR @CompanyId IS NULL
    OR @VendorRoleId IS NULL OR @CustomerRoleId IS NULL OR @ProjectRoleId IS NULL
    OR @BillRoleId IS NULL OR @ExpenseRoleId IS NULL
    OR @AttachmentsModuleId IS NULL
BEGIN
    RAISERROR('Missing prerequisite row (claude_agent / Company / specialist role / Attachments module). Aborting.', 16, 1);
    RETURN;
END;

-- Stack 5 specialist roles on claude_agent ─────────────────────────────
DECLARE @inserted INT = 0;

;WITH s AS (
    SELECT @VendorRoleId   AS RoleId UNION ALL
    SELECT @CustomerRoleId         UNION ALL
    SELECT @ProjectRoleId          UNION ALL
    SELECT @BillRoleId             UNION ALL
    SELECT @ExpenseRoleId
)
INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId, CompanyId)
SELECT @Now, @Now, @UserId, s.RoleId, @CompanyId
FROM s
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.UserRole ur
    WHERE ur.UserId = @UserId
      AND ur.RoleId = s.RoleId
      AND ur.CompanyId = @CompanyId
);

SET @inserted = @@ROWCOUNT;
PRINT CONCAT('  UserRole rows inserted: ', @inserted, ' (of 5 possible)');

-- Additive Attachments grant via UserModule ───────────────────────────
IF NOT EXISTS (
    SELECT 1 FROM dbo.UserModule
    WHERE UserId = @UserId AND ModuleId = @AttachmentsModuleId
)
BEGIN
    INSERT INTO dbo.UserModule (CreatedDatetime, ModifiedDatetime, UserId, ModuleId, CompanyId)
    VALUES (@Now, @Now, @UserId, @AttachmentsModuleId, @CompanyId);
    PRINT '  UserModule (Attachments) inserted';
END
ELSE
BEGIN
    PRINT '  UserModule (Attachments) already present';
END;

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — claude_agent MCP grants applied.';
PRINT 'NEXT: run scripts/migrations/gap1_agent_user_project_backfill.sql';
PRINT '────────────────────────────────────────────────────────────';
