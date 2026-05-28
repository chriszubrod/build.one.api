-- =============================================================================
-- 2026-05-27 — Phase 5: downstream-lock check sproc.
--
-- IsTimeEntryDownstreamLocked(@TimeEntryId)
--   Returns: 1 if any downstream aggregated row is at a terminal
--   (billed/invoiced) state — meaning further edits to this TimeEntry would
--   silently desync from a posted bill or invoice. 0 otherwise.
--
--   Used by TimeEntryService.update / reject to block edits once the row
--   has already flowed through.
--
-- Idempotent. Safe to re-run.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


CREATE OR ALTER PROCEDURE dbo.IsTimeEntryDownstreamLocked
(
    @TimeEntryId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Locked BIT = 0;

    IF EXISTS (
        SELECT 1 FROM dbo.[ContractLabor]
        WHERE [SourceTimeEntryId] = @TimeEntryId
          AND [Status] = 'billed'
    )
        SET @Locked = 1;

    IF @Locked = 0 AND OBJECT_ID('dbo.[EmployeeLabor]', 'U') IS NOT NULL
    BEGIN
        IF EXISTS (
            SELECT 1 FROM dbo.[EmployeeLabor]
            WHERE [SourceTimeEntryId] = @TimeEntryId
              AND [Status] = 'invoiced'
        )
            SET @Locked = 1;
    END

    SELECT @Locked AS Locked;
END;
GO

PRINT 'IsTimeEntryDownstreamLocked sproc created.';
