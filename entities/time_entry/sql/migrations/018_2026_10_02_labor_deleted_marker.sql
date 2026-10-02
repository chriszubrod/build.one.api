-- U-596 (2026-10-02): LaborDeletedDatetime — the office deleted the labor row(s) aggregated
-- from this TimeEntry. Deleting the parent cascades its lines and with them every marker
-- the labor rows carried (EditedSinceAggregation, bill links, Review rows), so the record
-- has to live on the entry itself. dbo.IsTimeEntryLaborUntouched reads it: such a day is
-- 'touched' — it never auto-reopens and a changing resubmit is refused — until the office
-- decides otherwise. Set by DeleteContractLaborById / DeleteEmployeeLaborById; never cleared
-- here. Idempotent; additive.
IF COL_LENGTH('dbo.TimeEntry', 'LaborDeletedDatetime') IS NULL
BEGIN
    ALTER TABLE dbo.[TimeEntry] ADD [LaborDeletedDatetime] DATETIME2(3) NULL;
END
GO
