-- Migration 001 — add FindContractLaborVendorByEmail sproc.
--
-- Part of the contract_labor_specialist agent build (Phase 1; see
-- TODO.md line 204 / item 5b). When the email_specialist sees a
-- `contract_labor_timesheet` classification, the new agent uses this
-- sproc to bind the sender email back to the worker's Vendor row.
--
-- Lookup pattern (locked 2026-05-26):
--   Vendor INNER JOIN Contact ON Contact.VendorId = Vendor.Id
--   WHERE Vendor.IsContractLabor = 1
--     AND Vendor.IsDeleted     = 0
--     AND LOWER(Contact.Email) = LOWER(@SenderEmail)
--
-- Returns a single Vendor row (TOP 1, ORDER BY Vendor.Id) or an empty
-- result set when no match. Projection mirrors ReadVendorByPublicId so
-- VendorRepository._from_db maps cleanly.
--
-- Idempotent: CREATE OR ALTER. Safe to re-apply.
GO


CREATE OR ALTER PROCEDURE FindContractLaborVendorByEmail
(
    @SenderEmail NVARCHAR(320)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1
        v.[Id],
        v.[PublicId],
        v.[RowVersion],
        CONVERT(VARCHAR(19), v.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), v.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        v.[Name],
        v.[Abbreviation],
        v.[VendorTypeId],
        v.[TaxpayerId],
        v.[IsDraft],
        v.[IsDeleted],
        v.[IsContractLabor],
        v.[Notes]
    FROM dbo.[Vendor] v
    INNER JOIN dbo.[Contact] c ON c.[VendorId] = v.[Id]
    WHERE v.[IsContractLabor] = 1
      AND v.[IsDeleted]       = 0
      AND LOWER(c.[Email])    = LOWER(@SenderEmail)
    ORDER BY v.[Id];
END;
GO
