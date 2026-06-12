-- Add BillLineItemId to ContractLaborLineItem for link-back from Contract Labor to Bill/BillLineItem
--
-- This file is the SOLE definition site for the column + FK — the base
-- ContractLaborLineItem DDL in dbo.contract_labor.sql predates the column.
-- Idempotent: safe to re-run.
--
-- NOTE (2026-06-11, Budget Phase 0 consolidation): this file previously also
-- carried CREATE OR ALTER copies of the ContractLaborLineItem CRUD sprocs.
-- Those copies drifted behind the canonical definitions, and re-running this
-- file would have:
--   * reverted UpdateContractLaborLineItemById to an unconditional
--     [BillLineItemId] = @BillLineItemId SET — wiping the billed link-back on
--     any edit that omits the param and re-introducing labor double-counting
--     in the budget variance rollup;
--   * dropped the @IsOverhead param (all CRUD sprocs) and the @CreatedByUserId
--     param (CreateContractLaborLineItem) that ContractLaborLineItemRepository
--     now passes, breaking every line-item create/update at runtime.
-- The stale copies were removed. The canonical definitions for ALL line-item
-- sprocs live in entities/contract_labor/sql/dbo.contract_labor.sql:
--   * UpdateContractLaborLineItemById carries the load-bearing CASE WHEN
--     guard that preserves [BillLineItemId] when @BillLineItemId is NULL;
--   * CreateContractLaborLineItem carries @CreatedByUserId (folded in from
--     scripts/migrations/gap2_core_threading.sql on 2026-06-11 so the
--     canonical file is actually re-runnable as canonical).
-- Do NOT redefine the sprocs here.
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.ContractLaborLineItem') AND name = 'BillLineItemId'
)
BEGIN
    ALTER TABLE dbo.[ContractLaborLineItem]
    ADD [BillLineItemId] BIGINT NULL;
END
GO

-- FK guarded independently of the column: a partial prior run (column added,
-- FK missing) must still pick up the constraint — its ON DELETE SET NULL is
-- load-bearing for the budget variance no-double-count predicate.
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_ContractLaborLineItem_BillLineItem'
)
BEGIN
    ALTER TABLE dbo.[ContractLaborLineItem]
    ADD CONSTRAINT [FK_ContractLaborLineItem_BillLineItem]
    FOREIGN KEY ([BillLineItemId]) REFERENCES dbo.[BillLineItem]([Id]) ON DELETE SET NULL;
END
GO
