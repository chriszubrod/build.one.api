-- ---------------------------------------------------------------------------
-- U-099 (2026-10-08) — Project.IsCostPlus: the project is cost-plus-a-builder-fee
-- (flat or percentage) rather than a flat-rate bid. A classification only — it does
-- NOT drive BillableStatus; the per-line Billable toggle stays authoritative.
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
