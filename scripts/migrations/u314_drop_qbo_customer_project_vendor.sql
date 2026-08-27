-- U-314: Phase-6 guarded DROP of qbo.CustomerCustomer / qbo.CustomerProject /
-- qbo.VendorVendor.
--
-- STAGED, NOT APPLIED. Per feedback_builders_never_mutate_prod_data, the build
-- unit prepares this file; /em runs it after (a) batch 6 (U-316 + U-314-prereq,
-- deployed 2026-08-26, container 48215c77) has soaked >=24h / >=6 QBO
-- customer/vendor pull cycles per docs/design/u314.md §7 (soak clock started
-- 23:36Z 2026-08-26 -- does not clear before ~2026-08-27 23:36Z), (b) the
-- separate identity_drift.py / backfill_qbo_active_mirror.py active-mirror
-- reconciliation unit has landed (this DROP does not touch either file), and
-- (c) a fresh re-run of §6's live re-verify query returns clean -- do not
-- trust this file's or the design doc's cached row counts, they decay the
-- moment they're written.
--
-- Drop order: unconstrained. All 3 tables are leaf junctions with 2 outward
-- FKs each (one to their dbo.<Entity> parent, one to their qbo.* staging
-- parent) and zero inward FKs -- no cross-table dependency among the three
-- (live sys.foreign_keys query, docs/design/u314.md §1). Any order, same
-- batch, is safe.
--
-- Re-verify immediately before running (docs/design/u314.md §6):
--   1. Parity/orphan check, all 3 families -- expect the same 0/0/0 shape.
--   2. Zero rows created/modified in any of the 3 tables since batch 6's own
--      deploy timestamp (substitute that timestamp; proves U-314-prereq's
--      heal_missing_mapping fix actually stopped the one confirmed live writer).
--   3. Zero qbo.ReconciliationIssue rows referencing any of the 3 families
--      since that same deploy timestamp.
--   4. FK graph (§1's query) -- re-run to confirm no NEW FK was added into
--      any of the 3 tables since this document was written.
-- Plus a fresh re-grep of heal_missing_mapping's caller, backfill_qbo_bills.py's
-- CTE, the identity_drift.py spec rows, and the dead-DI-param sites against
-- whatever HEAD is about to ship this drop.

IF OBJECT_ID('qbo.CustomerCustomer', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[CustomerCustomer];
END;
GO

IF OBJECT_ID('qbo.CustomerProject', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[CustomerProject];
END;
GO

IF OBJECT_ID('qbo.VendorVendor', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[VendorVendor];
END;
GO

-- Sprocs orphaned by the table drops. SQL Server does not require dropping
-- these first -- an unbound sproc body only errors at EXECUTE, not at DROP
-- TABLE time -- but leaving them live is a footgun: a stray caller gets a
-- confusing runtime error instead of an import-time failure. 18 CRUD sprocs
-- (6 per table -- the assignment estimated 15; the live base files and
-- docs/design/u314.md §1 both confirm 6: Create/ReadById/ReadByLocalFk/
-- ReadByQboFk/UpdateById/DeleteById) + 3 shared identity-check sprocs.
DROP PROCEDURE IF EXISTS dbo.CreateCustomerCustomer;
DROP PROCEDURE IF EXISTS dbo.ReadCustomerCustomerById;
DROP PROCEDURE IF EXISTS dbo.ReadCustomerCustomerByCustomerId;
DROP PROCEDURE IF EXISTS dbo.ReadCustomerCustomerByQboCustomerId;
DROP PROCEDURE IF EXISTS dbo.UpdateCustomerCustomerById;
DROP PROCEDURE IF EXISTS dbo.DeleteCustomerCustomerById;

DROP PROCEDURE IF EXISTS dbo.CreateCustomerProject;
DROP PROCEDURE IF EXISTS dbo.ReadCustomerProjectById;
DROP PROCEDURE IF EXISTS dbo.ReadCustomerProjectByProjectId;
DROP PROCEDURE IF EXISTS dbo.ReadCustomerProjectByQboCustomerId;
DROP PROCEDURE IF EXISTS dbo.UpdateCustomerProjectById;
DROP PROCEDURE IF EXISTS dbo.DeleteCustomerProjectById;

DROP PROCEDURE IF EXISTS dbo.CreateVendorVendor;
DROP PROCEDURE IF EXISTS dbo.ReadVendorVendorById;
DROP PROCEDURE IF EXISTS dbo.ReadVendorVendorByVendorId;
DROP PROCEDURE IF EXISTS dbo.ReadVendorVendorByQboVendorId;
DROP PROCEDURE IF EXISTS dbo.UpdateVendorVendorById;
DROP PROCEDURE IF EXISTS dbo.DeleteVendorVendorById;

-- U-306's shared JOIN'd identity-check sprocs (integrations/intuit/qbo/base/
-- sql/identity_consistency_reads.sql) -- drop only the 3 belonging to these
-- families. ReadBillBillIdentityCheckByBillId in the SAME file belongs to
-- Bill (qbo.BillBill), a DIFFERENT family, OUT OF SCOPE -- do not touch it.
DROP PROCEDURE IF EXISTS dbo.ReadCustomerCustomerIdentityCheckByCustomerId;
DROP PROCEDURE IF EXISTS dbo.ReadCustomerProjectIdentityCheckByProjectId;
DROP PROCEDURE IF EXISTS dbo.ReadVendorVendorIdentityCheckByVendorId;
GO
