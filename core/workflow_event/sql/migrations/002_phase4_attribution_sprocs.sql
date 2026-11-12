-- Phase 4 — Access Control Rebuild — CreateWorkflowEvent attribution param
-- Adds @CreatedByUserId BIGINT = NULL to CreateWorkflowEvent and
-- stores it on the new column. Idempotent (CREATE OR ALTER).
--
-- The legacy @CreatedBy VARCHAR param stays as a free-text
-- source/component tag (e.g. 'instant_workflow_handler', 'system').

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-789, 2026-11-12) — body removed, NOT the @CreatedByUserId attribution intent.
--
-- Canonical definition now lives in exactly ONE place:
--   core/workflow_event/sql/dbo.workflow_event.sql
--
-- The base file previously carried the STALE copy (no @CreatedByUserId) while this
-- migration carried the live one. Base files use CREATE OR ALTER and are re-run
-- routinely, so whichever ran last won — and a base re-run would have dropped the
-- param and stopped populating the column. U-789 moved the live body into the base
-- file, byte-verified against prod, and neutralised this copy.
-- Re-running this file is now a no-op for CreateWorkflowEvent. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

PRINT 'CreateWorkflowEvent extended with @CreatedByUserId.';
