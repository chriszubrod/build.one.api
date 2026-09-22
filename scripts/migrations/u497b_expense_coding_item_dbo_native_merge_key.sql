-- U-497b: Move ExpenseCodingItem MERGE/dedupe key from QboPurchaseLineId (volatile
-- qbo.PurchaseLine.Id staging PK) to dbo-native (QboPurchaseQboId, RealmId, QboLineId).
-- UpsertExpenseCodingItem refreshes QboPurchaseLineId on WHEN MATCHED so staging-PK
-- readers self-heal after pull churn without repointing readers.
--
-- STAGED, NOT APPLIED. Per feedback_builders_never_mutate_prod_data, /em applies at
-- Gate 2 only after every precondition below is RE-MEASURED LIVE. Do not trust counts
-- in this header or in docs/operations/u497_family11_followups.md — they decay.
--
-- PREREQUISITE: UpsertExpenseCodingItem + ReadExternallyResolvedCodingItemCandidates
-- arm-1 restatement from entities/expense_coding_item/sql/dbo.expense_coding_item.sql
-- must be APPLIED TO PROD (run_sql.py on the base file) BEFORE or together with this DDL.
-- Applying DDL alone without the sproc change leaves MERGE on the old key.
--
-- PRECONDITIONS (re-measure every one immediately before running):
--
-- P1. Zero NULL identity triple columns (must be 0 for NOT NULL ALTER):
--     SELECT COUNT(*) AS null_qbo_line_id
--       FROM dbo.ExpenseCodingItem WHERE QboLineId IS NULL;
--     SELECT COUNT(*) AS null_qbo_purchase_qbo_id
--       FROM dbo.ExpenseCodingItem WHERE QboPurchaseQboId IS NULL;
--     SELECT COUNT(*) AS null_realm_id
--       FROM dbo.ExpenseCodingItem WHERE RealmId IS NULL;
--
-- P2. Zero duplicate dbo-native identity groups (must be 0 before unique index):
--     SELECT COUNT(*) AS dup_groups FROM (
--         SELECT QboPurchaseQboId, RealmId, QboLineId, COUNT(*) AS n
--           FROM dbo.ExpenseCodingItem
--          GROUP BY QboPurchaseQboId, RealmId, QboLineId
--         HAVING COUNT(*) > 1
--     ) d;
--
-- P3. Arm-1 equivalence spot-check (optional corroboration, not a gate substitute):
--     Re-run the U-483 candidate count before/after arm-1 restatement on a copy;
--     scoping measured 139 rows unchanged.
--
-- Run (from repo root, after preconditions pass):
--   ./.venv/bin/python scripts/run_sql.py scripts/migrations/u497b_expense_coding_item_dbo_native_merge_key.sql

IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NOT NULL
BEGIN
    IF EXISTS (SELECT 1 FROM dbo.ExpenseCodingItem WHERE QboLineId IS NULL)
        THROW 51001, 'U-497b P1: QboLineId NULL rows exist — aborting NOT NULL', 1;
    IF EXISTS (SELECT 1 FROM dbo.ExpenseCodingItem WHERE QboPurchaseQboId IS NULL)
        THROW 51002, 'U-497b P1: QboPurchaseQboId NULL rows exist — aborting NOT NULL', 1;
    IF EXISTS (SELECT 1 FROM dbo.ExpenseCodingItem WHERE RealmId IS NULL)
        THROW 51003, 'U-497b P1: RealmId NULL rows exist — aborting NOT NULL', 1;
END
GO

IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NOT NULL
BEGIN
    ALTER TABLE dbo.[ExpenseCodingItem] ALTER COLUMN [QboLineId] NVARCHAR(50) NOT NULL;
END
GO

IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NOT NULL
BEGIN
    ALTER TABLE dbo.[ExpenseCodingItem] ALTER COLUMN [QboPurchaseQboId] NVARCHAR(50) NOT NULL;
END
GO

IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NOT NULL
BEGIN
    ALTER TABLE dbo.[ExpenseCodingItem] ALTER COLUMN [RealmId] NVARCHAR(50) NOT NULL;
END
GO

IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NOT NULL
   AND EXISTS (
       SELECT 1
         FROM dbo.ExpenseCodingItem
        GROUP BY QboPurchaseQboId, RealmId, QboLineId
       HAVING COUNT(*) > 1
   )
BEGIN
    THROW 51004, 'U-497b P2: duplicate (QboPurchaseQboId, RealmId, QboLineId) groups — aborting unique index', 1;
END
GO

IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes
     WHERE name = 'UQ_ExpenseCodingItem_PurchaseLineIdentity'
       AND object_id = OBJECT_ID('dbo.ExpenseCodingItem')
)
BEGIN
    CREATE UNIQUE INDEX UQ_ExpenseCodingItem_PurchaseLineIdentity
        ON dbo.[ExpenseCodingItem] ([QboPurchaseQboId], [RealmId], [QboLineId]);
END
GO
