-- ============================================================================
-- time_entry_view_team.sql
--
-- RoleModule CanViewTeam migration (filed 2026-05-26; time_entry sproc/UDF
-- bodies removed U-045, 2026-07-16; RoleModule schema+sprocs removed U-048,
-- 2026-07-16).
--
-- This file is the SOLE canonical source for:
--   - RoleModule CanViewTeam seed grants (section 2)
--
-- RoleModule schema (CanViewTeam column) and CRUD sprocs are canonical in
-- entities/role_module/sql/dbo.rolemodule.sql
-- (see entities/role_module/sql/README.md).
--
-- TimeEntry/TimeLog/TimeEntryStatus sprocs and dbo.UserCanAccessTimeEntry UDF
-- are canonical in entities/time_entry/sql/dbo.time_entry.sql
-- (see entities/time_entry/sql/README.md).
--
-- Idempotent. Safe to re-run (the section-2 seed is a guarded UPDATE).
--
-- ORDER MATTERS: entities/role_module/sql/dbo.rolemodule.sql must be applied
-- BEFORE this file — it creates the CanViewTeam column the seed updates.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-048, 2026-07-16) — CanViewTeam column guard removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Add CanViewTeam BIT NOT NULL with DEFAULT 0 to dbo.RoleModule.
--
-- The canonical definition of this schema change now lives in exactly ONE place:
--   entities/role_module/sql/dbo.rolemodule.sql
--
-- Re-running this file is now a no-op for this object. Do NOT reintroduce a
-- body here — a copy that drifts from the base file breaks net-zero with prod.
-- ---------------------------------------------------------------------------


-- ----------------------------------------------------------------------------
-- 2. Seed: grant CanViewTeam=1 to Owner / Project Manager / Controller /
--    Tenant Admin on the Time Tracking module. Idempotent UPDATE — re-runs
--    are no-ops once values are set.
-- ----------------------------------------------------------------------------
DECLARE @TimeTrackingModuleId BIGINT = (
    SELECT TOP 1 Id FROM dbo.Module WHERE Name = 'Time Tracking'
);

IF @TimeTrackingModuleId IS NULL
BEGIN
    RAISERROR('Time Tracking module not found in dbo.Module — abort seed', 16, 1);
END;
ELSE
BEGIN
    UPDATE rm
       SET rm.CanViewTeam = 1
      FROM dbo.RoleModule rm
      JOIN dbo.Role r ON r.Id = rm.RoleId
     WHERE rm.ModuleId = @TimeTrackingModuleId
       AND r.Name IN ('Owner', 'Project Manager', 'Controller', 'Tenant Admin')
       AND rm.CanViewTeam <> 1;
END;
GO


-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-045, 2026-07-16) — sproc/UDF bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Thread CanViewTeam through TimeEntry read/mutation sprocs and add
--   dbo.UserCanAccessTimeEntry UDF for service-layer access checks.
--
-- The canonical definition of these objects now lives in exactly ONE place:
--   entities/time_entry/sql/dbo.time_entry.sql
--
-- Objects formerly defined here (now canonical in the base file):
--   dbo.UserCanAccessTimeEntry (UDF) and the 16 RBAC-scoped TimeEntry/TimeLog/
--   TimeEntryStatus read+mutation sprocs — see
--   entities/time_entry/sql/dbo.time_entry.sql for the canonical set.
--
-- Re-running this file is now a no-op for these objects. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-048, 2026-07-16) — sproc bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Extend RoleModule CRUD sprocs to round-trip the CanViewTeam column so
--   RoleModuleRepository._from_db can read it and admin UI updates persist it.
--
-- The canonical definition of these objects now lives in exactly ONE place:
--   entities/role_module/sql/dbo.rolemodule.sql
--
-- Objects formerly defined here (now canonical in the base file):
--   ReadRoleModules, ReadRoleModuleById, ReadRoleModuleByPublicId,
--   ReadRoleModuleByRoleId, ReadRoleModuleByModuleId,
--   CreateRoleModule, UpdateRoleModuleById
--
-- Re-running this file is now a no-op for these objects. Do NOT reintroduce a
-- body here — a copy that drifts from the base file silently reverts the
-- CanViewTeam round-trip. shared/rbac.py reads the flag via
-- getattr(rm, 'can_view_team', False), so a sproc body missing the column
-- resolves False with no error raised, and every non-admin who depends on team
-- visibility goes blind on team rows. (An RBAC permission regression — NOT the
-- 2026-07-15 SQL 8144 outage; that was time_entry migration 015, which never
-- touched RoleModule. See the U-045 banner above for that one.)
-- ---------------------------------------------------------------------------


-- ----------------------------------------------------------------------------
-- 8. Post-migration sanity checks (run AFTER everything above completes).
--    These are SELECTs only — copy/paste and confirm the values.
-- ----------------------------------------------------------------------------
PRINT '--- post-migration sanity ---';

SELECT 'RoleModule.CanViewTeam column exists' AS Check_Item,
       CASE WHEN EXISTS (
            SELECT 1 FROM sys.columns
            WHERE object_id = OBJECT_ID('dbo.RoleModule') AND name = 'CanViewTeam'
        ) THEN 'OK' ELSE 'MISSING' END AS Result;

SELECT r.Id AS RoleId, r.Name AS RoleName, rm.CanViewTeam
  FROM dbo.RoleModule rm
  JOIN dbo.Role r ON r.Id = rm.RoleId
 WHERE rm.ModuleId = (SELECT Id FROM dbo.Module WHERE Name = 'Time Tracking')
   AND rm.CanViewTeam = 1
 ORDER BY r.Name;

SELECT 'dbo.UserCanAccessTimeEntry UDF exists' AS Check_Item,
       CASE WHEN OBJECT_ID('dbo.UserCanAccessTimeEntry') IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS Result;

-- verify 16 TimeEntry/TimeLog/TimeEntryStatus sprocs + 7 RoleModule sprocs exist
-- (none are defined in this file — canonical in base files)
SELECT name AS Sproc, 'OK' AS Result
  FROM sys.procedures
 WHERE name IN (
    'ReadTimeEntries','ReadTimeEntryById','ReadTimeEntryByPublicId',
    'ReadTimeEntriesByUserId','ReadTimeEntriesByProjectId',
    'ReadTimeEntriesPaginated','CountTimeEntries',
    'UpdateTimeEntryById','DeleteTimeEntryById',
    'ReadTimeLogsByTimeEntryId','ReadTimeLogById','ReadTimeLogByPublicId',
    'UpdateTimeLogById','DeleteTimeLogById',
    'ReadTimeEntryStatusesByTimeEntryId','ReadCurrentTimeEntryStatus',
    'ReadRoleModules','ReadRoleModuleById','ReadRoleModuleByPublicId',
    'ReadRoleModuleByRoleId','ReadRoleModuleByModuleId',
    'CreateRoleModule','UpdateRoleModuleById'
 )
 ORDER BY name;
