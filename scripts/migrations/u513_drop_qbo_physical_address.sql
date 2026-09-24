-- U-513 ph3b: guarded DROP of qbo.PhysicalAddress and its 7 CRUD sprocs.
--
-- STAGED, NOT APPLIED BY THE BUILD. Per feedback_builders_never_mutate_prod_data
-- the build unit writes this file and stops; /em applies it, and only after a
-- LIVE sentinel confirms the table has stopped being written. The pre-drop guard
-- below is the machine half of that sentinel -- it is not a substitute for
-- checking, it is what makes a mistaken check non-destructive.
--
-- ---------------------------------------------------------------------------
-- WHAT THIS DROPS
--   1 table   qbo.PhysicalAddress
--   7 sprocs  dbo.CreateQboPhysicalAddress
--             dbo.ReadQboPhysicalAddresses
--             dbo.ReadQboPhysicalAddressById
--             dbo.ReadQboPhysicalAddressByPublicId
--             dbo.ReadQboPhysicalAddressByQboId
--             dbo.UpdateQboPhysicalAddressById
--             dbo.DeleteQboPhysicalAddressById
--   (The sprocs were created with no schema prefix in
--   integrations/intuit/qbo/physical_address/sql/qbo.physical_address.sql --
--   deleted by this same unit -- so they live in dbo even though the TABLE is
--   in qbo. That asymmetry is why they are spelled dbo.* here.)
--
-- WHAT THIS DELIBERATELY DOES NOT DROP -- out of scope, a separate later phase:
--   qbo.Customer.BillAddrId, qbo.Customer.ShipAddrId, qbo.Vendor.BillAddrId.
--   Those columns point into the dropped table, but they sit on two SURVIVING
--   staging tables whose own CRUD sprocs reference them in 46 places. Removing
--   them means rewriting those sprocs; leaving them NULL and unread is harmless.
--   There is NO `ALTER TABLE ... DROP COLUMN` anywhere in this file, and
--   tests/test_u513_ph3b_package_removed.py fails if one appears.
--
-- ---------------------------------------------------------------------------
-- WHAT WAS VERIFIED BEFORE WRITING THIS (re-verify at apply time -- a live-state
-- reading is perishable and these decay the moment they are written):
--
--   * NO family writes the table. ph3a (b3c80b92 customer, 5cd4fdad vendor +
--     company_info) removed the last writes; every family now projects addresses
--     from the INLINE QBO payload. Pinned by
--     tests/test_u513_ph3_{customer,vendor,company_info}_no_staging_write.py.
--   * NO code reads the table. ph3b (this unit) deleted the repository, service,
--     model, router, schemas and external client -- the only code that ever
--     named a PhysicalAddress sproc -- plus
--     `PhysicalAddressAddressConnector.sync_from_qbo_to_address`, the one
--     staging-read entry point. Its last two callers are removed by the parallel
--     customer and vendor units; this file must not be applied before those land
--     and deploy, or the deployed container will 500 on every address projection.
--   * ZERO FK edges. Neither qbo.Customer nor qbo.Vendor declares a FOREIGN KEY
--     on its *AddrId columns (they are plain BIGINTs), and qbo.CompanyInfo --
--     which also carried address ids -- was itself dropped by U-505b. Re-run a
--     live sys.foreign_keys check anyway rather than trusting this line.
--
-- ONE WRITER THE GUARD CANNOT SEE: scripts/backfill_qbo_identity_reference.py's
-- `backfill_address_stage0` issues a raw `UPDATE qbo.[PhysicalAddress] SET
-- [RealmId]` that does NOT touch ModifiedDatetime, so a run of it would be
-- invisible below. It is a manual, --apply-gated, one-shot U-238c backfill tool
-- with no scheduler binding, not a live writer. Confirm by hand that nobody is
-- running it, then apply. (That script's Stage 0 stops working once this lands;
-- there is nothing left for it to backfill.)
--
-- ROLLBACK: THERE IS NONE. A dropped table cannot be un-dropped -- the staged
-- address rows are gone the instant this commits, and nothing in this file or
-- anywhere else can put them back. Recovery means a point-in-time restore of the
-- database from backup. That asymmetry is the ENTIRE reason for the pre-drop
-- guard: the cost of refusing wrongly is one re-run, and the cost of proceeding
-- wrongly is a restore. The data loss itself is accepted -- every field on this
-- table is already projected onto dbo.Address (street_one/street_two/city/
-- state/zip) or is re-derivable from a fresh QBO pull; only `Country`, which
-- nothing ever read off this table, has no dbo home.
--
-- IDEMPOTENT: every statement is IF EXISTS / DROP ... IF EXISTS shaped, so a
-- re-run after a partial failure is safe and a re-run after a clean run is a
-- no-op. The guard itself reads the table through sp_executesql precisely so
-- that a re-run AFTER the drop does not fail batch compilation on a table that
-- no longer exists (an ad-hoc batch resolves object names at compile time, so a
-- direct `SELECT ... FROM qbo.PhysicalAddress` inside an `IF OBJECT_ID(...)`
-- block would still raise Msg 208 once the table is gone).
--
-- TRANSACTIONALITY: scripts/run_sql.py splits on GO and runs every batch on ONE
-- connection with autocommit=False, committing only after the last batch and
-- rolling back on any exception. So the THROW below aborts the whole file, not
-- just its own batch, and nothing is dropped. Do not add a `GO`-separated
-- COMMIT, and do not run this through a tool that commits per batch.
-- ---------------------------------------------------------------------------


-- ===========================================================================
-- PRE-DROP GUARD -- abort if anything still writes the table.
-- ===========================================================================
-- The whole premise of this drop is that qbo.PhysicalAddress is dead. If a row
-- has been created or modified in the last 2 hours, that premise is false: a
-- writer survived somewhere (an un-deployed container still running ph2-era
-- code, a re-introduced staging write, a manual EXEC), and dropping the table
-- would destroy rows something is still producing. Refuse instead.
--
-- COALESCE(ModifiedDatetime, CreatedDatetime): ModifiedDatetime is NULLable and
-- `NULL > @cutoff` is NULL, i.e. NOT matched -- a freshly inserted row with a
-- NULL ModifiedDatetime would slip past a bare ModifiedDatetime comparison,
-- which is exactly the row this guard exists to catch. CreatedDatetime is
-- NOT NULL.
--
-- 2 hours, not 24: the QBO pulls that used to write this table run on a
-- sub-hourly scheduler tick, so a live writer shows up inside one window. A
-- wider window would instead make the guard fire on the last legitimate ph2-era
-- write and block the drop forever.
IF OBJECT_ID('qbo.PhysicalAddress', 'U') IS NOT NULL
BEGIN
    DECLARE @RecentWrites INT = 0;

    EXEC sp_executesql
        N'SELECT @out = COUNT(*)
          FROM [qbo].[PhysicalAddress]
          WHERE COALESCE([ModifiedDatetime], [CreatedDatetime])
                > DATEADD(HOUR, -2, SYSUTCDATETIME());',
        N'@out INT OUTPUT',
        @out = @RecentWrites OUTPUT;

    IF @RecentWrites > 0
        THROW 51513,
              'U-513 ph3b ABORT: qbo.PhysicalAddress was written in the last 2 hours, so something still writes it and the drop is NOT safe. Check, in order: (1) is the deployed API container actually running the ph3a+ build, or is an older one still serving? (2) re-grep the deployed tree for QboPhysicalAddressRepository / QboPhysicalAddressService / a physical_address sproc name; (3) is scripts/backfill_qbo_identity_reference.py --apply running? (4) SELECT TOP 20 Id, QboId, RealmId, CreatedDatetime, ModifiedDatetime FROM qbo.PhysicalAddress ORDER BY COALESCE(ModifiedDatetime, CreatedDatetime) DESC -- the QboId suffix (_bill / _ship) names the family that wrote it. Nothing has been dropped; the whole file rolled back. Re-run once the writer is gone.',
              1;
END;
GO


-- ===========================================================================
-- 1. SPROCS -- dropped FIRST, before the table.
-- ===========================================================================
-- SQL Server does not require this order (an unbound sproc body errors only at
-- EXECUTE, not at DROP TABLE time), but sprocs-first is the strictly safer
-- sequence: once they are gone there is no remaining path by which a straggler
-- caller can write the table between this batch and the next. Dropping the
-- table first would leave a window where 7 live sprocs point at nothing.
DROP PROCEDURE IF EXISTS dbo.CreateQboPhysicalAddress;
DROP PROCEDURE IF EXISTS dbo.ReadQboPhysicalAddresses;
DROP PROCEDURE IF EXISTS dbo.ReadQboPhysicalAddressById;
DROP PROCEDURE IF EXISTS dbo.ReadQboPhysicalAddressByPublicId;
DROP PROCEDURE IF EXISTS dbo.ReadQboPhysicalAddressByQboId;
DROP PROCEDURE IF EXISTS dbo.UpdateQboPhysicalAddressById;
DROP PROCEDURE IF EXISTS dbo.DeleteQboPhysicalAddressById;
GO


-- ===========================================================================
-- 2. TABLE
-- ===========================================================================
IF OBJECT_ID('qbo.PhysicalAddress', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[PhysicalAddress];
END;
GO
