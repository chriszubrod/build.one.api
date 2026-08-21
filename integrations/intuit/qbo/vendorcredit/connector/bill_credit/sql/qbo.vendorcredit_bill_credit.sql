-- =============================================
-- Table: [qbo].[VendorCreditBillCredit]
-- Description: Mapping between QBO VendorCredit and local BillCredit
-- =============================================
--
-- U-225 (2026-08-16): this file used to ALSO declare a `CREATE TABLE
-- [qbo].[VendorCreditBillCredit]` body (INT ids, ON DELETE CASCADE both sides),
-- guarded by the same `IF OBJECT_ID(...) IS NULL` idiom as the table's real home,
-- integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql. Both files were
-- unordered relative to each other, so whichever ran first silently won -- a
-- base-file single-source-of-truth violation (found by U-218a's Pass 2, TODO.md
-- U-225). Live prod was measured (2026-08-16, re-measured after an earlier
-- 2026-08-11 measurement) as BIGINT ids + NO ACTION on both FKs, which matches
-- ONLY the qbo.vendorcredit.sql body -- this file's INT/CASCADE body never won
-- the race in prod, and its two extra non-unique indexes (IX_VendorCreditBillCredit_
-- QboVendorCreditId / IX_VendorCreditBillCredit_BillCreditId, which were nested
-- inside the same losing IF-NULL guard) were confirmed absent from prod's live
-- sys.indexes for this table. The table is now declared in exactly one place:
-- qbo.vendorcredit.sql. This file's sprocs below are unaffected by this change.

IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'qbo')
BEGIN
    EXEC('CREATE SCHEMA [qbo]')
END
GO

-- =============================================
-- U-286 (2026-08-20): CreateVendorCreditBillCredit / ReadVendorCreditBillCreditByQboVendorCreditId /
-- ReadVendorCreditBillCreditByBillCreditId retired FROM THIS FILE ONLY. These 3 were declared here
-- under the [qbo] schema — permanently unreachable, since shared/database.py::call_procedure always
-- emits EXEC dbo.<name>. The live, actually-called bodies of the same 3 names are the implicit-dbo
-- CREATE OR ALTER PROCEDUREs in integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql — those
-- are untouched by this unit. tests/sproc_drift_ledger.py's "known-dup" entries for these 3 names are
-- removed in the same unit (now single-sourced).
-- =============================================
DROP PROCEDURE IF EXISTS [qbo].[CreateVendorCreditBillCredit];
GO
DROP PROCEDURE IF EXISTS [qbo].[ReadVendorCreditBillCreditByQboVendorCreditId];
GO
DROP PROCEDURE IF EXISTS [qbo].[ReadVendorCreditBillCreditByBillCreditId];
GO

-- =============================================
-- Stored Procedure: DeleteVendorCreditBillCreditByQboVendorCreditId
-- =============================================
GO

-- NOTE: defined in dbo (call_procedure issues EXEC dbo.{name}); the TABLE stays
-- in qbo. The old [qbo].[...] copy was unreachable. @QboVendorCreditId is BIGINT
-- (the local qbo.VendorCredit PK).
CREATE OR ALTER PROCEDURE [dbo].[DeleteVendorCreditBillCreditByQboVendorCreditId]
    @QboVendorCreditId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM [qbo].[VendorCreditBillCredit]
    WHERE [QboVendorCreditId] = @QboVendorCreditId
END
GO
