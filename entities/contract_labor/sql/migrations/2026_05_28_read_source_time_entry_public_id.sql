-- =============================================================================
-- 2026-05-28 — surface SourceTimeEntryPublicId on the detail read.
--
-- The React ContractLabor Edit page wants to embed the source TimeEntry's
-- per-log breakdown ("Time Log Details" section). It already has the
-- TimeEntry detail endpoint (`GET /api/v1/time-entries/{public_id}`); it
-- just needs the source PublicId to address it. SourceTimeEntryId (BIGINT)
-- is opaque to the client.
--
-- LEFT JOIN to dbo.TimeEntry so Excel-imported rows (NULL SourceTimeEntryId)
-- return NULL for the new column.
--
-- Idempotent — CREATE OR ALTER.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

CREATE OR ALTER PROCEDURE ReadContractLaborByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        cl.[Id],
        cl.[PublicId],
        cl.[RowVersion],
        CONVERT(VARCHAR(19), cl.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cl.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        cl.[VendorId],
        cl.[ProjectId],
        cl.[EmployeeName],
        cl.[JobName],
        CONVERT(VARCHAR(10), cl.[WorkDate], 120) AS [WorkDate],
        cl.[TimeIn],
        cl.[TimeOut],
        cl.[BreakTime],
        cl.[RegularHours],
        cl.[OvertimeHours],
        cl.[TotalHours],
        cl.[HourlyRate],
        cl.[Markup],
        cl.[TotalAmount],
        cl.[SubCostCodeId],
        cl.[Description],
        CONVERT(VARCHAR(10), cl.[BillingPeriodStart], 120) AS [BillingPeriodStart],
        cl.[Status],
        cl.[BillLineItemId],
        cl.[BillVendorId],
        CONVERT(VARCHAR(10), cl.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(10), cl.[DueDate], 120) AS [DueDate],
        cl.[BillNumber],
        cl.[ImportBatchId],
        cl.[SourceFile],
        cl.[SourceRow],
        cl.[SourceTimeEntryId],
        te.[PublicId] AS [SourceTimeEntryPublicId]
    FROM dbo.[ContractLabor] cl
    LEFT JOIN dbo.[TimeEntry] te ON te.[Id] = cl.[SourceTimeEntryId]
    WHERE cl.[PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

PRINT 'ReadContractLaborByPublicId now returns SourceTimeEntryPublicId.';
