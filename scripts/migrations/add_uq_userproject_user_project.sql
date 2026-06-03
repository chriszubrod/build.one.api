-- Add UNIQUE(UserId, ProjectId) on dbo.UserProject.
--
-- Today there's no DB-level guard against duplicate (UserId, ProjectId)
-- pairs — the PK is on Id only. The 2026-05-27 13:40:09 mass-backfill
-- inserted UP rows without pre-checking existence, producing 2 known
-- dup pairs by 2026-06-04:
--   Id 3215 + 3248 — UserId=17 (Christopher) ProjectId=64 (BR-MAIN)
--   Id 1514 + 3320 — UserId=33 (Claude Agent) ProjectId=64 (BR-MAIN)
-- Both rows in each pair have RoleId NULL — no metadata loss to drop
-- the later one. Same fix idea as the Project Name uniqueness work
-- (a70dea8 / UQ_Project_Name_CustomerId_Active): belt-and-suspenders
-- at the DB layer so future regressing scripts fail loud instead of
-- silently re-duping.
--
-- Idempotent — both the dedup pass and the CREATE INDEX are guarded.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py scripts/migrations/add_uq_userproject_user_project.sql

SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- ─── 1. Dedup existing pairs ───────────────────────────────────────────
-- For each (UserId, ProjectId) pair with >1 row, keep the row with the
-- smallest Id (most stable, predates the buggy backfill) and delete the
-- rest. Idempotent — second run finds zero dup pairs and deletes nothing.
DECLARE @DupRowsDeleted INT;
;WITH ranked AS (
    SELECT Id,
           ROW_NUMBER() OVER (PARTITION BY UserId, ProjectId ORDER BY Id ASC) AS rn
    FROM dbo.UserProject
)
DELETE FROM dbo.UserProject
WHERE Id IN (SELECT Id FROM ranked WHERE rn > 1);
SET @DupRowsDeleted = @@ROWCOUNT;

PRINT CONCAT('add_uq_userproject_user_project: deduplicated rows removed=', @DupRowsDeleted);

-- ─── 2. Confirm zero remaining dup pairs ───────────────────────────────
DECLARE @StillDup INT = (
    SELECT COUNT(*) FROM (
        SELECT UserId, ProjectId
        FROM dbo.UserProject
        GROUP BY UserId, ProjectId
        HAVING COUNT(*) > 1
    ) AS dups
);
IF @StillDup <> 0
BEGIN
    RAISERROR('add_uq_userproject_user_project: dup pairs still present after dedup; aborting before index create.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- ─── 3. Create the unique index ────────────────────────────────────────
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_UserProject_UserId_ProjectId'
      AND object_id = OBJECT_ID('dbo.UserProject')
)
BEGIN
    CREATE UNIQUE INDEX UQ_UserProject_UserId_ProjectId
        ON dbo.UserProject(UserId, ProjectId);
    PRINT 'add_uq_userproject_user_project: index created.';
END
ELSE
BEGIN
    PRINT 'add_uq_userproject_user_project: index already exists — no-op.';
END;

COMMIT TRANSACTION;
PRINT 'add_uq_userproject_user_project: COMMITTED.';
