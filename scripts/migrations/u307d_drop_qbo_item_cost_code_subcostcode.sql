-- U-307d: Phase-6 guarded DROP of qbo.ItemCostCode / qbo.ItemSubCostCode /
-- qbo.Item.
--
-- STAGED, NOT APPLIED. Per feedback_builders_never_mutate_prod_data, the build
-- unit prepares this file; /em runs it after (a) batch 6 (U-316 + U-307d-prereq,
-- deployed 2026-08-26, container 48215c77) has soaked >=24h / >=6 QBO item pull
-- cycles per docs/design/u307d.md §6.2 (soak clock started 23:36Z 2026-08-26 --
-- does not clear before ~2026-08-27 23:36Z), (b) the separate identity_drift.py /
-- backfill_qbo_active_mirror.py active-mirror reconciliation unit has landed
-- (this DROP does not touch either file), and (c) a fresh re-run of §6.1's live
-- re-verify query returns clean -- do not trust this file's or the design doc's
-- cached row counts, they decay the moment they're written.
--
-- Drop order: qbo.ItemCostCode + qbo.ItemSubCostCode (children, either order
-- relative to each other) before qbo.Item (parent) -- FK_ItemCostCode_QboItem
-- and FK_ItemSubCostCode_QboItem are the only 2 edges touching qbo.Item, both
-- outward from the mapping tables (live sys.foreign_keys query,
-- docs/design/u307d.md §3); no other table FKs into any of the 3, so this is a
-- flat, 2-level, same-family drop.
--
-- Re-verify immediately before running (docs/design/u307d.md §6.1):
--   1. Zero rows created/modified in any of the 3 tables since U-307d-prereq's
--      own deploy timestamp (substitute that timestamp for the query below).
--   2. Row-count context (not a gate, just confirms nothing unexpected grew).
--   3. Zero qbo.ReconciliationIssue rows referencing any of the 3 tables since
--      that same deploy timestamp.
--   4. dbo-native parity hasn't regressed: 0 dbo.SubCostCode/dbo.CostCode rows
--      with QboId IS NULL that a live qbo.ItemSubCostCode/qbo.ItemCostCode
--      mapping row still points at.
-- Plus a fresh re-grep of cost_code_resolver.py's callers, identity_drift.py,
-- and prompt.md's then-current WIP state against whatever HEAD is about to
-- ship this drop.

IF OBJECT_ID('qbo.ItemCostCode', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[ItemCostCode];
END;
GO

IF OBJECT_ID('qbo.ItemSubCostCode', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[ItemSubCostCode];
END;
GO

IF OBJECT_ID('qbo.Item', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[Item];
END;
GO

-- Sprocs orphaned by the table drops. SQL Server does not require dropping
-- these first -- an unbound sproc body only errors at EXECUTE, not at DROP
-- TABLE time -- but leaving them live is a footgun: a stray caller gets a
-- confusing runtime error instead of an import-time failure. 20 sprocs across
-- the 3 base files (docs/design/u307d.md §3).
DROP PROCEDURE IF EXISTS dbo.CreateItemCostCode;
DROP PROCEDURE IF EXISTS dbo.ReadItemCostCodes;
DROP PROCEDURE IF EXISTS dbo.ReadItemCostCodeByCostCodeId;
DROP PROCEDURE IF EXISTS dbo.ReadItemCostCodeByQboItemId;
DROP PROCEDURE IF EXISTS dbo.UpdateItemCostCodeById;
DROP PROCEDURE IF EXISTS dbo.DeleteItemCostCodeById;
DROP PROCEDURE IF EXISTS dbo.CreateItemSubCostCode;
DROP PROCEDURE IF EXISTS dbo.ReadItemSubCostCodes;
DROP PROCEDURE IF EXISTS dbo.ReadItemSubCostCodeBySubCostCodeId;
DROP PROCEDURE IF EXISTS dbo.ReadItemSubCostCodeByQboItemId;
DROP PROCEDURE IF EXISTS dbo.UpdateItemSubCostCodeById;
DROP PROCEDURE IF EXISTS dbo.DeleteItemSubCostCodeById;
DROP PROCEDURE IF EXISTS dbo.CreateQboItem;
DROP PROCEDURE IF EXISTS dbo.ReadQboItems;
DROP PROCEDURE IF EXISTS dbo.ReadQboItemsByRealmId;
DROP PROCEDURE IF EXISTS dbo.ReadQboItemById;
DROP PROCEDURE IF EXISTS dbo.ReadQboItemByQboId;
DROP PROCEDURE IF EXISTS dbo.ReadQboItemByQboIdAndRealmId;
DROP PROCEDURE IF EXISTS dbo.UpdateQboItemByQboId;
DROP PROCEDURE IF EXISTS dbo.DeleteQboItemByQboId;
GO
