-- Add permission columns to RoleModule table
-- Run: python scripts/run_sql.py entities/role_module/sql/add_permission_columns.sql

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.RoleModule') AND name = 'CanCreate')
BEGIN
    ALTER TABLE dbo.[RoleModule] ADD
        [CanCreate] BIT NOT NULL DEFAULT 0,
        [CanRead] BIT NOT NULL DEFAULT 0,
        [CanUpdate] BIT NOT NULL DEFAULT 0,
        [CanDelete] BIT NOT NULL DEFAULT 0,
        [CanSubmit] BIT NOT NULL DEFAULT 0,
        [CanApprove] BIT NOT NULL DEFAULT 0,
        [CanComplete] BIT NOT NULL DEFAULT 0;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.RoleModule') AND name = 'CanComplete')
BEGIN
    ALTER TABLE dbo.[RoleModule] ADD [CanComplete] BIT NOT NULL DEFAULT 0;
END
GO
