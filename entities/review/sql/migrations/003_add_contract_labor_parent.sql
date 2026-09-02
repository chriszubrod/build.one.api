-- =============================================================================
-- 2026-05-28 — extend Review to support ContractLabor as a parent type.
--
-- Mirrors the existing Bill/Expense/BillCredit/Invoice pattern: nullable FK
-- column + filtered index + CHECK constraint requiring exactly one parent
-- FK to be non-null.
--
-- Adapts the polymorphic review pipeline for the AP/billing review of
-- ContractLabor rows (PMs/Owners approving rate/markup/SCC decisions before
-- the row transitions to 'ready' and gets picked up by Generate Bills).
--
-- Idempotent. Safe to re-run.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-357b, 2026-09-01) — schema statements removed, NOT the intent.
-- Canonical definition now lives in exactly ONE place:
--   entities/review/sql/dbo.review.sql
-- Schema formerly applied here (now the base file's guarded ALTER block + CREATE TABLE):
--   dbo.Review.[ContractLaborId] · FK_Review_ContractLabor · IX_Review_ContractLaborId
--   · CK_Review_OneParent (5-way)
-- Re-running this file is now a no-op for these objects. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is the single-source hazard
-- (U-037: a stale redefinition dropped live sproc params -> SQL 8144 outage).
-- ---------------------------------------------------------------------------

PRINT 'migrations/003 is superseded — no-op (schema now lives in dbo.review.sql).';
