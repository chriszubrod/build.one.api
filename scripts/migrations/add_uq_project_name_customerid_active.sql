-- Add UNIQUE(Name, CustomerId) filtered to active rows on dbo.Project.
--
-- Background: the recurring 4-hourly QBO Customer sync produced 22 dup
-- dbo.Project rows over a multi-week window because
-- CustomerProjectConnector.sync_from_qbo_customer (pre-a70dea8) created
-- a fresh dbo.Project on every QboCustomer pull that lacked a
-- qbo.CustomerProject mapping — without checking for a same-name local
-- row. The connector fix (a70dea8) is now live; this index is the
-- belt-and-suspenders that makes a future regression fail loud at the
-- DB layer instead of silently creating duplicates.
--
-- Shape decided in docs/dedupe-project-rows.md decision #3:
--   UNIQUE(Name, CustomerId) — filtered to Status='active'.
-- Allows same Name on different Customers (unusual but defensible) and
-- lets archived projects free up the name. SQL Server default collation
-- is case-insensitive so 'HP' == 'hp'.
--
-- Idempotent — only creates the index when it doesn't already exist.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py scripts/migrations/add_uq_project_name_customerid_active.sql

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_Project_Name_CustomerId_Active'
      AND object_id = OBJECT_ID('dbo.Project')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Project_Name_CustomerId_Active
        ON dbo.Project(Name, CustomerId)
        WHERE Status = 'active';
    PRINT 'add_uq_project_name_customerid_active: index created.';
END
ELSE
BEGIN
    PRINT 'add_uq_project_name_customerid_active: index already exists — no-op.';
END;
