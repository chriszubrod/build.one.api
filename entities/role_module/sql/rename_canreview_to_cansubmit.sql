-- Rename CanReview column to CanSubmit
-- Run: python scripts/run_sql.py entities/role_module/sql/rename_canreview_to_cansubmit.sql

IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.RoleModule') AND name = 'CanReview')
BEGIN
    EXEC sp_rename 'dbo.RoleModule.CanReview', 'CanSubmit', 'COLUMN';
END
GO
