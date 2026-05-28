-- =============================================================================
-- 2026-05-28 — Phase 7c: surface downstream lineage for a TimeEntry.
--
-- ReadTimeEntryBilledLineage(@TimeEntryId)
--   Returns 0..N rows — one per ContractLabor or EmployeeLabor row that
--   carries SourceTimeEntryId = @TimeEntryId. When the labor row is linked
--   to a Bill (vendor path) or Invoice (employee path), those fields are
--   non-NULL; otherwise the labor row is queued for review/billing.
--
--   Used by the React TimeEntryView "Billed via Bill #X" / "Invoiced via
--   Invoice #Y" section + future cross-entity audit views.
--
-- Idempotent. Safe to re-run.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


CREATE OR ALTER PROCEDURE dbo.ReadTimeEntryBilledLineage
(
    @TimeEntryId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    -- Vendor path: ContractLabor → BillLineItem → Bill
    SELECT
        N'ContractLabor'                          AS TargetTable,
        cl.[Id]                                   AS TargetId,
        CAST(cl.[PublicId] AS NVARCHAR(36))       AS TargetPublicId,
        cl.[Status]                               AS LaborStatus,
        CONVERT(VARCHAR(10), cl.[WorkDate], 120)  AS WorkDate,
        cl.[VendorId]                             AS WorkerId,           -- VendorId for this row
        v.[Name]                                  AS WorkerName,
        cl.[TotalAmount]                          AS TotalAmount,
        b.[Id]                                    AS LinkedTargetId,     -- Bill.Id when billed
        CAST(b.[PublicId] AS NVARCHAR(36))        AS LinkedTargetPublicId,
        N'Bill'                                   AS LinkedTargetTable,
        b.[BillNumber]                            AS LinkedTargetNumber
    FROM dbo.[ContractLabor] cl
    LEFT JOIN dbo.[Vendor]       v   ON v.[Id]   = cl.[VendorId]
    LEFT JOIN dbo.[BillLineItem] bli ON bli.[Id] = cl.[BillLineItemId]
    LEFT JOIN dbo.[Bill]         b   ON b.[Id]   = bli.[BillId]
    WHERE cl.[SourceTimeEntryId] = @TimeEntryId

    UNION ALL

    -- Employee path: EmployeeLabor → InvoiceLineItem → Invoice
    SELECT
        N'EmployeeLabor'                          AS TargetTable,
        el.[Id]                                   AS TargetId,
        CAST(el.[PublicId] AS NVARCHAR(36))       AS TargetPublicId,
        el.[Status]                               AS LaborStatus,
        CONVERT(VARCHAR(10), el.[WorkDate], 120)  AS WorkDate,
        el.[EmployeeId]                           AS WorkerId,           -- EmployeeId for this row
        e.[Firstname] + ' ' + e.[Lastname]        AS WorkerName,
        el.[TotalAmount]                          AS TotalAmount,
        i.[Id]                                    AS LinkedTargetId,
        CAST(i.[PublicId] AS NVARCHAR(36))        AS LinkedTargetPublicId,
        N'Invoice'                                AS LinkedTargetTable,
        i.[InvoiceNumber]                         AS LinkedTargetNumber
    FROM dbo.[EmployeeLabor] el
    LEFT JOIN dbo.[Employee]        e   ON e.[Id]   = el.[EmployeeId]
    LEFT JOIN dbo.[InvoiceLineItem] ili ON ili.[Id] = el.[InvoiceLineItemId]
    LEFT JOIN dbo.[Invoice]         i   ON i.[Id]   = ili.[InvoiceId]
    WHERE el.[SourceTimeEntryId] = @TimeEntryId

    ORDER BY WorkDate, TargetTable;
END;
GO

PRINT 'ReadTimeEntryBilledLineage sproc created.';
