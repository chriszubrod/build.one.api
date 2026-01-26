-- =============================================================================
-- Seed: Workflow Inbox Module
-- Adds the Workflow Inbox module to dbo.Module
-- =============================================================================

-- Create the Module (if not exists)
IF NOT EXISTS (SELECT 1 FROM dbo.Module WHERE Name = 'Workflow Inbox')
BEGIN
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (@Now, @Now, 'Workflow Inbox', '/agent/list');
    
    PRINT 'Created Module: Workflow Inbox';
END
ELSE
BEGIN
    PRINT 'Module already exists: Workflow Inbox';
END
GO

-- Note: RoleModule table assignment is not implemented yet.
-- When RoleModule is implemented, run the following to assign to roles:
--
-- DECLARE @ModulePublicId UNIQUEIDENTIFIER;
-- SELECT @ModulePublicId = PublicId FROM dbo.Module WHERE Name = 'Workflow Inbox';
-- 
-- INSERT INTO dbo.[RoleModule] ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId])
-- SELECT DISTINCT SYSUTCDATETIME(), SYSUTCDATETIME(), ur.RoleId, @ModulePublicId
-- FROM dbo.[UserRole] ur
-- WHERE NOT EXISTS (
--     SELECT 1 FROM dbo.[RoleModule] rm 
--     WHERE rm.RoleId = ur.RoleId AND rm.ModuleId = @ModulePublicId
-- );
