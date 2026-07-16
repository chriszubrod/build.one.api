-- ============================================================================
-- time_entry_view_team.sql
--
-- RoleModule CanViewTeam migration (filed 2026-05-26; time_entry sproc/UDF
-- bodies removed U-045, 2026-07-16).
--
-- This file is the SOLE canonical source for:
--   - dbo.RoleModule.CanViewTeam column + seed grants
--   - RoleModule CRUD sprocs that round-trip CanViewTeam
--
-- TimeEntry/TimeLog/TimeEntryStatus sprocs and dbo.UserCanAccessTimeEntry UDF
-- are canonical in entities/time_entry/sql/dbo.time_entry.sql
-- (see entities/time_entry/sql/README.md).
--
-- Idempotent. Safe to re-run. ALTER TABLE guards on column existence.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Schema: add CanViewTeam to RoleModule with DEFAULT 0
-- ----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1
    FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.RoleModule') AND name = 'CanViewTeam'
)
BEGIN
    ALTER TABLE dbo.RoleModule
        ADD CanViewTeam BIT NOT NULL CONSTRAINT DF_RoleModule_CanViewTeam DEFAULT (0);
END;
GO


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


-- ----------------------------------------------------------------------------
-- 7. RoleModule CRUD sprocs — extend to round-trip the new CanViewTeam
--    column so RoleModuleRepository._from_db can read it and admin UI
--    updates persist it. Identical shape to existing Can* fields.
-- ----------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE dbo.ReadRoleModules
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule];
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [Id] = @Id;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [PublicId] = @PublicId;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleByRoleId
(
    @RoleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [RoleId] = @RoleId;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleByModuleId
(
    @ModuleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [ModuleId] = @ModuleId;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.CreateRoleModule
(
    @RoleId BIGINT,
    @ModuleId BIGINT,
    @CanCreate BIT = 0,
    @CanRead BIT = 0,
    @CanUpdate BIT = 0,
    @CanDelete BIT = 0,
    @CanSubmit BIT = 0,
    @CanApprove BIT = 0,
    @CanComplete BIT = 0,
    @CanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIME2 = SYSUTCDATETIME();
    INSERT INTO dbo.[RoleModule] (
        [CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete], [CanViewTeam]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[RoleId], INSERTED.[ModuleId],
        INSERTED.[CanCreate], INSERTED.[CanRead], INSERTED.[CanUpdate], INSERTED.[CanDelete],
        INSERTED.[CanSubmit], INSERTED.[CanApprove], INSERTED.[CanComplete],
        INSERTED.[CanViewTeam]
    VALUES (
        @Now, @Now, @RoleId, @ModuleId,
        @CanCreate, @CanRead, @CanUpdate, @CanDelete,
        @CanSubmit, @CanApprove, @CanComplete, @CanViewTeam
    );
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.UpdateRoleModuleById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @RoleId BIGINT,
    @ModuleId BIGINT,
    @CanCreate BIT = 0,
    @CanRead BIT = 0,
    @CanUpdate BIT = 0,
    @CanDelete BIT = 0,
    @CanSubmit BIT = 0,
    @CanApprove BIT = 0,
    @CanComplete BIT = 0,
    @CanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;
    UPDATE dbo.[RoleModule]
       SET [ModifiedDatetime] = SYSUTCDATETIME(),
           [RoleId]      = @RoleId,
           [ModuleId]    = @ModuleId,
           [CanCreate]   = @CanCreate,
           [CanRead]     = @CanRead,
           [CanUpdate]   = @CanUpdate,
           [CanDelete]   = @CanDelete,
           [CanSubmit]   = @CanSubmit,
           [CanApprove]  = @CanApprove,
           [CanComplete] = @CanComplete,
           [CanViewTeam] = @CanViewTeam
        OUTPUT
            INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
            CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
            INSERTED.[RoleId], INSERTED.[ModuleId],
            INSERTED.[CanCreate], INSERTED.[CanRead], INSERTED.[CanUpdate], INSERTED.[CanDelete],
            INSERTED.[CanSubmit], INSERTED.[CanApprove], INSERTED.[CanComplete],
            INSERTED.[CanViewTeam]
     WHERE [Id] = @Id
       AND [RowVersion] = @RowVersion;
    COMMIT TRANSACTION;
END;
GO


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

-- expect 16 TimeEntry/TimeLog/TimeEntryStatus sprocs + 7 RoleModule sprocs = 23 rows
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
