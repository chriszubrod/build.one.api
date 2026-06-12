-- Grants on the new 'Budgets' Module (project contract value / schedule of
-- values) for the human roles that should work with budgets.
--
-- Scope assignment:
--   Tenant Admin     — full CRUD + approve (activate budgets / approve change orders)
--   Controller       — full CRUD + approve
--   Project Manager  — create / read / update (drafts change orders; cannot approve or delete)
--   Reviewer         — read
--   Auditor          — read
--
-- CanSubmit and CanComplete stay 0 everywhere (CanSubmit is a dead flag —
-- zero routers read it; Budget has no complete action). CanViewTeam (the
-- 8th RoleModule flag, added by scripts/migrations/time_entry_view_team.sql)
-- stays 0 — it is a Time Tracking concept.
-- System admins bypass module checks via User.IsSystemAdmin = 1.
--
-- ── OPS PREREQUISITES (in order) ────────────────────────────────────────
-- 0. FRESH ENVIRONMENTS ONLY: RoleModule.CanViewTeam must exist (added by
--    scripts/migrations/time_entry_view_team.sql; live in prod since
--    2026-05-26). The MERGEs below reference the column and fail without it.
-- 1. BEFORE this file: run entities/module/sql/seed.BudgetsModule.sql so the
--    'Budgets' Module row exists (do NOT re-run seed.AllModules.sql — it
--    re-seeds ghost modules). This migration RAISERRORs if the row is missing.
-- 2. AFTER this file: re-run intelligence/persistence/sql/seed.owner_role.sql
--    — Owner mirrors Tenant Admin's grants via MERGE; without the re-run the
--    Owner role cannot see Budgets.
--
-- Idempotent (MERGE). Safe to re-run.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py entities/module/sql/seed.BudgetsModule.sql
--   .venv/bin/python scripts/run_sql.py entities/role/sql/migrations/005_grant_budgets_module.sql
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.owner_role.sql

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @Now       DATETIME2(3) = SYSUTCDATETIME();
DECLARE @BudgetsId BIGINT       = (SELECT [Id] FROM dbo.[Module] WHERE [Name] = N'Budgets');

IF @BudgetsId IS NULL
BEGIN
    RAISERROR('Budgets Module row missing — run entities/module/sql/seed.BudgetsModule.sql first.', 16, 1);
    RETURN;
END;

-- ---------------------------------------------------------------------
-- Tenant Admin + Controller: full CRUD + approve
-- (CanSubmit / CanComplete / CanViewTeam deliberately 0)
-- ---------------------------------------------------------------------
DECLARE @ApproverRoles TABLE ([Name] NVARCHAR(100));
INSERT INTO @ApproverRoles ([Name]) VALUES
    (N'Tenant Admin'),
    (N'Controller');

MERGE dbo.[RoleModule] AS target
USING (
    SELECT r.[Id] AS RoleId, @BudgetsId AS ModuleId
    FROM dbo.[Role] r
    INNER JOIN @ApproverRoles ar ON ar.[Name] = r.[Name]
) AS src
ON target.[RoleId] = src.RoleId AND target.[ModuleId] = src.ModuleId
WHEN MATCHED THEN
    UPDATE SET [CanCreate] = 1, [CanRead] = 1, [CanUpdate] = 1, [CanDelete] = 1,
               [CanSubmit] = 0, [CanApprove] = 1, [CanComplete] = 0,
               [CanViewTeam] = 0,
               [ModifiedDatetime] = @Now
WHEN NOT MATCHED THEN
    INSERT ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
            [CanCreate], [CanRead], [CanUpdate], [CanDelete],
            [CanSubmit], [CanApprove], [CanComplete], [CanViewTeam])
    VALUES (@Now, @Now, src.RoleId, src.ModuleId,
            1, 1, 1, 1, 0, 1, 0, 0);

DECLARE @ApproverGranted INT = @@ROWCOUNT;
PRINT CONCAT('  Approver roles (Tenant Admin / Controller): ', @ApproverGranted, ' grants merged');

-- ---------------------------------------------------------------------
-- Project Manager: create / read / update (no delete, no approve)
-- ---------------------------------------------------------------------
DECLARE @PmId BIGINT = (SELECT [Id] FROM dbo.[Role] WHERE [Name] = N'Project Manager');
IF @PmId IS NOT NULL
BEGIN
    MERGE dbo.[RoleModule] AS target
    USING (SELECT @PmId AS RoleId, @BudgetsId AS ModuleId) AS src
    ON target.[RoleId] = src.RoleId AND target.[ModuleId] = src.ModuleId
    WHEN MATCHED THEN
        UPDATE SET [CanCreate] = 1, [CanRead] = 1, [CanUpdate] = 1, [CanDelete] = 0,
                   [CanSubmit] = 0, [CanApprove] = 0, [CanComplete] = 0,
                   [CanViewTeam] = 0,
                   [ModifiedDatetime] = @Now
    WHEN NOT MATCHED THEN
        INSERT ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
                [CanCreate], [CanRead], [CanUpdate], [CanDelete],
                [CanSubmit], [CanApprove], [CanComplete], [CanViewTeam])
        VALUES (@Now, @Now, src.RoleId, src.ModuleId,
                1, 1, 1, 0, 0, 0, 0, 0);
    PRINT '  Project Manager: Budgets create/read/update merged';
END
ELSE
BEGIN
    PRINT '  Project Manager role missing — skipping.';
END

-- ---------------------------------------------------------------------
-- Read-only grants: Reviewer, Auditor
-- ---------------------------------------------------------------------
DECLARE @ReadRoles TABLE ([Name] NVARCHAR(100));
INSERT INTO @ReadRoles ([Name]) VALUES
    (N'Reviewer'),
    (N'Auditor');

MERGE dbo.[RoleModule] AS target
USING (
    SELECT r.[Id] AS RoleId, @BudgetsId AS ModuleId
    FROM dbo.[Role] r
    INNER JOIN @ReadRoles rr ON rr.[Name] = r.[Name]
) AS src
ON target.[RoleId] = src.RoleId AND target.[ModuleId] = src.ModuleId
WHEN MATCHED THEN
    UPDATE SET [CanCreate] = 0, [CanRead] = 1, [CanUpdate] = 0, [CanDelete] = 0,
               [CanSubmit] = 0, [CanApprove] = 0, [CanComplete] = 0,
               [CanViewTeam] = 0,
               [ModifiedDatetime] = @Now
WHEN NOT MATCHED THEN
    INSERT ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
            [CanCreate], [CanRead], [CanUpdate], [CanDelete],
            [CanSubmit], [CanApprove], [CanComplete], [CanViewTeam])
    VALUES (@Now, @Now, src.RoleId, src.ModuleId,
            0, 1, 0, 0, 0, 0, 0, 0);

DECLARE @ReadGranted INT = @@ROWCOUNT;
PRINT CONCAT('  Read-only roles (Reviewer / Auditor): ', @ReadGranted, ' grants merged');

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Budgets Module grants seeded for human roles.';
PRINT '       Re-run intelligence/persistence/sql/seed.owner_role.sql';
PRINT '       to propagate the Tenant Admin grants to Owner.';
PRINT '────────────────────────────────────────────────────────────';
