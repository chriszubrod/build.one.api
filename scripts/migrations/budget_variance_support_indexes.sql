-- Supporting indexes for the Budget variance engine (Phase 2, 2026-06-12).
--
-- ReadBudgetVarianceByProjectId aggregates the labor line tables by
-- ProjectId; before this migration:
--   * dbo.ContractLaborLineItem's only nonclustered index was
--     IX_ContractLaborLineItem_ContractLaborId — every variance call
--     full-scanned the table (fed daily by the TimeEntry→ContractLabor
--     pipeline).
--   * dbo.EmployeeLaborLineItem had ZERO nonclustered indexes — not even
--     EmployeeLaborId or PublicId, so the baseline pair is added here too
--     (ordinary CRUD reads want them regardless of variance).
--
-- Idempotent — safe to re-run. Mirrored guards live in the base DDL files
-- (entities/contract_labor/sql/dbo.contract_labor.sql,
--  entities/employee_labor/sql/dbo.employee_labor.sql) for fresh bootstraps;
-- this standalone file exists so prod picks them up WITHOUT re-running the
-- full canonical files.
--
-- RUN: .venv/bin/python scripts/run_sql.py scripts/migrations/budget_variance_support_indexes.sql

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_ContractLaborLineItem_ProjectId'
                 AND object_id = OBJECT_ID('dbo.ContractLaborLineItem'))
BEGIN
    CREATE INDEX [IX_ContractLaborLineItem_ProjectId]
        ON dbo.[ContractLaborLineItem] ([ProjectId])
        INCLUDE ([SubCostCodeId], [Hours], [Price], [Markup],
                 [IsOverhead], [IsBillable], [BillLineItemId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_EmployeeLaborLineItem_ProjectId'
                 AND object_id = OBJECT_ID('dbo.EmployeeLaborLineItem'))
BEGIN
    CREATE INDEX [IX_EmployeeLaborLineItem_ProjectId]
        ON dbo.[EmployeeLaborLineItem] ([ProjectId])
        INCLUDE ([SubCostCodeId], [Hours], [Rate], [IsOverhead]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_EmployeeLaborLineItem_EmployeeLaborId'
                 AND object_id = OBJECT_ID('dbo.EmployeeLaborLineItem'))
BEGIN
    CREATE INDEX [IX_EmployeeLaborLineItem_EmployeeLaborId]
        ON dbo.[EmployeeLaborLineItem] ([EmployeeLaborId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_EmployeeLaborLineItem_PublicId'
                 AND object_id = OBJECT_ID('dbo.EmployeeLaborLineItem'))
BEGIN
    CREATE INDEX [IX_EmployeeLaborLineItem_PublicId]
        ON dbo.[EmployeeLaborLineItem] ([PublicId]);
END
GO
