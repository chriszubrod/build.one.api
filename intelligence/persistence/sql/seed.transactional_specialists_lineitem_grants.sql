-- Add Cost Codes (read) + Projects (read) module grants to bill_agent /
-- expense_agent / bill_credit_agent roles. Required so the line-item
-- creation tools can resolve sub_cost_code_id (BIGINT) and confirm
-- project public_ids without 401-ing through search/read.
--
-- Idempotent: UPDATE-or-INSERT pattern per RoleModule row.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.transactional_specialists_lineitem_grants.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @CostCodesModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Cost Codes');
DECLARE @ProjectsModuleId  INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Projects');

IF @CostCodesModuleId IS NULL OR @ProjectsModuleId IS NULL
BEGIN
    RAISERROR('Required Module rows (Cost Codes / Projects) missing. Aborting.', 16, 1);
    RETURN;
END;

DECLARE @RoleNames TABLE (Name NVARCHAR(100));
INSERT INTO @RoleNames (Name) VALUES
    ('Bill Specialist'),
    ('Expense Specialist'),
    ('Bill Credit Specialist');

DECLARE @RoleName NVARCHAR(100);
DECLARE @RoleId BIGINT;

DECLARE role_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT Name FROM @RoleNames;
OPEN role_cursor;
FETCH NEXT FROM role_cursor INTO @RoleName;

WHILE @@FETCH_STATUS = 0
BEGIN
    SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;
    IF @RoleId IS NULL
    BEGIN
        PRINT CONCAT('  SKIP — role not found: ', @RoleName);
    END
    ELSE
    BEGIN
        -- Cost Codes (read only) — covers SubCostCode lookups.
        IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @CostCodesModuleId)
            UPDATE dbo.RoleModule
               SET CanRead = 1, ModifiedDatetime = @Now
             WHERE RoleId = @RoleId AND ModuleId = @CostCodesModuleId;
        ELSE
            INSERT INTO dbo.RoleModule
                (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
                 CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
            VALUES (@Now, @Now, @RoleId, @CostCodesModuleId, 0, 1, 0, 0, 0, 0, 0);

        -- Projects (read only).
        IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @ProjectsModuleId)
            UPDATE dbo.RoleModule
               SET CanRead = 1, ModifiedDatetime = @Now
             WHERE RoleId = @RoleId AND ModuleId = @ProjectsModuleId;
        ELSE
            INSERT INTO dbo.RoleModule
                (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
                 CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
            VALUES (@Now, @Now, @RoleId, @ProjectsModuleId, 0, 1, 0, 0, 0, 0, 0);

        PRINT CONCAT('  ', @RoleName, ': Cost Codes + Projects read grants set');
    END;

    FETCH NEXT FROM role_cursor INTO @RoleName;
END;

CLOSE role_cursor;
DEALLOCATE role_cursor;

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — line-item resolution reads granted.';
PRINT '────────────────────────────────────────────────────────────';
