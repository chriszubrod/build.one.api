-- ---------------------------------------------------------------------------
-- U-099 (2026-10-08) — Project.IsCostPlus: the project is cost-plus — its expenses are billed on
-- to the customer. Drives BillableStatus on the expense-coding recode: a
-- confirmed project with IsCostPlus = 1 stamps Billable; overhead projects
-- (set to 0 by hand) and unconfirmed items stamp NotBillable.
--
-- DDL ONLY. Proc bodies live in entities/project/sql/dbo.project.sql, the
-- single source of truth (tests/test_sproc_single_source.py). Do not add
-- procedure definitions to this migration.
-- ---------------------------------------------------------------------------

IF COL_LENGTH('dbo.Project', 'IsCostPlus') IS NULL
BEGIN
    ALTER TABLE dbo.[Project]
        ADD [IsCostPlus] BIT NOT NULL CONSTRAINT DF_Project_IsCostPlus DEFAULT 1;
END
GO
