-- U-053 · RBAC join-table integrity — dbo.RoleModule FK + UQ, dbo.Module.Name UQ
--
-- WHY THIS EXISTS
--   entities/role_module/sql/dbo.rolemodule.sql has ALWAYS declared
--   FK_RoleModule_Role, FK_RoleModule_Module and UQ_RoleModule_RoleId_ModuleId,
--   but none of them ever reached prod. There was no GO between
--   DeleteRoleModuleById's closing END; and the trailing constraint block, and a
--   stored-procedure body runs to the end of the BATCH (not to its matching END),
--   so the first constraint block was swallowed into the sproc's definition and
--   never executed as DDL. U-048 added the missing GO; the base was never
--   re-applied. Verified in prod 2026-07-26: sys.foreign_keys for dbo.RoleModule
--   returns ZERO rows and the only constraint present is the PK. The load-bearing
--   RBAC join table has had no referential integrity and no (RoleId, ModuleId)
--   uniqueness guard.
--
--   Bundled here (the U-139 deferral in TODO.md): dbo.Module.Name has no unique
--   constraint either, so every module seed hand-rolls IF NOT EXISTS-by-Name and a
--   concurrent double-apply could mint duplicate module rows. RBAC resolves modules
--   by name — uniqueness is a real invariant the schema should own.
--
-- WHY A MIGRATION AND NOT A BASE RE-APPLY
--   Re-applying dbo.rolemodule.sql is a no-op for its 8 sprocs (base == live,
--   verified 2026-07-16) but would create these constraints as an UNTRACKED side
--   effect. This file makes the schema write explicit, guarded and auditable. The
--   base files keep the canonical declarations; this is the apply vehicle, exactly
--   as sprocs live in a base file and are applied by running it.
--
-- SAFETY MODEL — fails loud, never partial
--   scripts/run_sql.py splits on GO and runs EVERY batch on ONE connection with
--   autocommit=False; shared/database.py::get_connection commits only on a clean
--   exit and rolls back on any exception. So every batch below is ONE transaction:
--   a RAISERROR severity 16 anywhere rolls the entire run back. Each constraint is
--   preceded by the data guard that constraint depends on (0 orphans / 0
--   duplicates), so a point-in-time dirty row aborts the run BEFORE any DDL rather
--   than half-applying. A post-apply block then re-reads the catalog and asserts
--   all four constraints exist, are trusted, carry the right columns, and that no
--   rows moved.
--
--   APPLY IN A QUIET WINDOW. The final check asserts the RoleModule/Module row
--   counts did not move across the run. Constraint DDL cannot move rows, so this
--   only ever fires on a concurrent writer. The exposed window is small — from the
--   first ALTER onward the schema-modification lock blocks every other session
--   until commit — but a valid grant inserted by another admin between the
--   baseline SELECT and that first ALTER WILL abort and roll back an otherwise
--   correct apply. That is deliberate and fail-closed: nothing is left half-applied
--   and re-running the file is safe and sufficient.
--
--   NB SET XACT_ABORT ON does NOT abort on RAISERROR — the RETURN ends the batch
--   and the runner's rollback undoes the transaction. Same shape as
--   entities/role/sql/migrations/006_provision_employee_labor_module.sql.
--
-- DATA AS OF 2026-07-26 (prod, point-in-time — the guards below re-assert it):
--   dbo.RoleModule 140 rows · 0 duplicate (RoleId, ModuleId) · 0 orphan RoleId ·
--   0 orphan ModuleId.  dbo.Module: 0 duplicate Name.
--
-- BEHAVIOUR CHANGE (accepted at Gate 1, option B1 — follow-up unit in TODO.md):
--   * DELETE /role/{id} and DELETE /module/{id} do not pre-clean RoleModule
--     children, so deleting a role/module that still holds grants now fails loudly
--     with SQL 547 instead of silently orphaning the grants.
--   * Re-POSTing an existing (RoleId, ModuleId) grant now fails with SQL 2627
--     instead of writing a silent duplicate row.
--   Both are admin-only paths. Mapping 547/2627 to HTTP 409 and pre-cleaning the
--   delete paths is a separate unit.
--
-- Idempotent — safe to re-run (IF NOT EXISTS on every create).
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py entities/role_module/sql/migrations/001_rbac_join_integrity_constraints.sql

SET XACT_ABORT ON;
SET NOCOUNT ON;

-- Dropped in its own batch so the SELECT INTO below always lands on a clean name
-- even if this file is re-run inside a reused session.
DROP TABLE IF EXISTS #U053Baseline;
GO

-- Baseline row counts, carried across batches. A #temp table is required here:
-- DECLARE is batch-scoped and this file spans GO boundaries.
SELECT
    (SELECT COUNT(*) FROM dbo.[RoleModule]) AS [RoleModuleRows],
    (SELECT COUNT(*) FROM dbo.[Module])     AS [ModuleRows]
INTO #U053Baseline;

DECLARE @RmBaseline INT = (SELECT [RoleModuleRows] FROM #U053Baseline);
DECLARE @MBaseline  INT = (SELECT [ModuleRows]     FROM #U053Baseline);

PRINT '────────────────────────────────────────────────────────────';
PRINT 'U-053 · RBAC join-table integrity';
PRINT CONCAT('  baseline: dbo.RoleModule ', @RmBaseline, ' rows · dbo.Module ', @MBaseline, ' rows');
GO

-- ─────────────────────────────────────────────────────────────────────────────
-- 1/4 · FK_RoleModule_Role   (dbo.RoleModule.RoleId -> dbo.Role.Id)
-- ─────────────────────────────────────────────────────────────────────────────
DECLARE @OrphanRoleIds INT = (
    SELECT COUNT(*)
    FROM dbo.[RoleModule] rm
    WHERE NOT EXISTS (SELECT 1 FROM dbo.[Role] r WHERE r.[Id] = rm.[RoleId])
);
IF @OrphanRoleIds <> 0
BEGIN
    RAISERROR('ABORT: %d dbo.RoleModule row(s) carry a RoleId with no matching dbo.Role row — FK_RoleModule_Role cannot be created. Resolve the orphan grants, then re-run. Nothing has been applied.', 16, 1, @OrphanRoleIds);
    RETURN;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE [name] = 'FK_RoleModule_Role'
      AND parent_object_id = OBJECT_ID('dbo.RoleModule')
)
BEGIN
    ALTER TABLE [dbo].[RoleModule] WITH CHECK
        ADD CONSTRAINT [FK_RoleModule_Role]
        FOREIGN KEY ([RoleId]) REFERENCES [dbo].[Role] ([Id]);
    PRINT '  + FK_RoleModule_Role created (RoleId -> dbo.Role.Id, WITH CHECK)';
END
ELSE
    PRINT '  = FK_RoleModule_Role already present — no-op';
GO

-- ─────────────────────────────────────────────────────────────────────────────
-- 2/4 · FK_RoleModule_Module   (dbo.RoleModule.ModuleId -> dbo.Module.Id)
-- ─────────────────────────────────────────────────────────────────────────────
DECLARE @OrphanModuleIds INT = (
    SELECT COUNT(*)
    FROM dbo.[RoleModule] rm
    WHERE NOT EXISTS (SELECT 1 FROM dbo.[Module] m WHERE m.[Id] = rm.[ModuleId])
);
IF @OrphanModuleIds <> 0
BEGIN
    RAISERROR('ABORT: %d dbo.RoleModule row(s) carry a ModuleId with no matching dbo.Module row — FK_RoleModule_Module cannot be created. Resolve the orphan grants, then re-run. Nothing has been applied.', 16, 1, @OrphanModuleIds);
    RETURN;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE [name] = 'FK_RoleModule_Module'
      AND parent_object_id = OBJECT_ID('dbo.RoleModule')
)
BEGIN
    ALTER TABLE [dbo].[RoleModule] WITH CHECK
        ADD CONSTRAINT [FK_RoleModule_Module]
        FOREIGN KEY ([ModuleId]) REFERENCES [dbo].[Module] ([Id]);
    PRINT '  + FK_RoleModule_Module created (ModuleId -> dbo.Module.Id, WITH CHECK)';
END
ELSE
    PRINT '  = FK_RoleModule_Module already present — no-op';
GO

-- ─────────────────────────────────────────────────────────────────────────────
-- 3/4 · UQ_RoleModule_RoleId_ModuleId   (one grant row per role+module)
-- ─────────────────────────────────────────────────────────────────────────────
DECLARE @DupPairs INT = (
    SELECT COUNT(*) FROM (
        SELECT [RoleId], [ModuleId]
        FROM dbo.[RoleModule]
        GROUP BY [RoleId], [ModuleId]
        HAVING COUNT(*) > 1
    ) d
);
IF @DupPairs <> 0
BEGIN
    RAISERROR('ABORT: %d duplicate (RoleId, ModuleId) pair(s) in dbo.RoleModule — UQ_RoleModule_RoleId_ModuleId cannot be created. De-duplicate (keep the row whose permission flags are correct — permissions are an OR-union, so dropping the wrong row silently REVOKES access), then re-run. Nothing has been applied.', 16, 1, @DupPairs);
    RETURN;
END;

-- Guard on sys.indexes rather than sys.key_constraints: a UNIQUE constraint always
-- materialises an index of the same name, so this also catches a pre-existing bare
-- unique index that would otherwise pass a constraint-only guard and then fail the
-- ALTER with a duplicate-index-name error.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE [name] = 'UQ_RoleModule_RoleId_ModuleId'
      AND object_id = OBJECT_ID('dbo.RoleModule')
)
BEGIN
    ALTER TABLE [dbo].[RoleModule]
        ADD CONSTRAINT [UQ_RoleModule_RoleId_ModuleId] UNIQUE ([RoleId], [ModuleId]);
    PRINT '  + UQ_RoleModule_RoleId_ModuleId created (RoleId, ModuleId)';
END
ELSE
    PRINT '  = UQ_RoleModule_RoleId_ModuleId already present — no-op';
GO

-- ─────────────────────────────────────────────────────────────────────────────
-- 4/4 · UQ_Module_Name   (RBAC resolves modules by name)
-- ─────────────────────────────────────────────────────────────────────────────
-- The duplicate check groups on [Name] under the column's own collation, so it is
-- exactly the comparison the unique index will enforce: under a case-insensitive
-- collation 'Bills' and 'bills' are one key, which is what we want, since
-- dbo.ReadModuleByName resolves under that same collation.
DECLARE @DupNames INT = (
    SELECT COUNT(*) FROM (
        SELECT [Name] FROM dbo.[Module] GROUP BY [Name] HAVING COUNT(*) > 1
    ) d
);
IF @DupNames <> 0
BEGIN
    RAISERROR('ABORT: %d duplicate dbo.Module.[Name] value(s) — UQ_Module_Name cannot be created. See entities/module/sql/cleanup_phantom_modules.sql for the remediation path, then re-run. Nothing has been applied.', 16, 1, @DupNames);
    RETURN;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE [name] = 'UQ_Module_Name'
      AND object_id = OBJECT_ID('dbo.Module')
)
BEGIN
    ALTER TABLE [dbo].[Module] ADD CONSTRAINT [UQ_Module_Name] UNIQUE ([Name]);
    PRINT '  + UQ_Module_Name created (Name)';
END
ELSE
    PRINT '  = UQ_Module_Name already present — no-op';
GO

-- ─────────────────────────────────────────────────────────────────────────────
-- POST-APPLY VERIFICATION
-- Any anomaly RAISERRORs at severity 16, which propagates to scripts/run_sql.py
-- and rolls the WHOLE run back. This file can never end in a partial apply or a
-- silent DONE.
-- ─────────────────────────────────────────────────────────────────────────────

-- (a) Both foreign keys exist, are enabled, and are TRUSTED. is_not_trusted = 0 is
--     the assertion that matters: it proves the constraint was created WITH CHECK
--     and actually validated every existing row, rather than being taken on faith.
--     delete_referential_action = 0 and update_referential_action = 0 require NO
--     ACTION on every FK — this repo handles child cleanup in application code, not
--     via CASCADE. is_not_for_replication = 0 rejects NOT FOR REPLICATION FKs. A
--     same-named FK carrying ON DELETE/UPDATE CASCADE would otherwise verify as
--     correct while silently deleting RBAC grants on role deletion.
DECLARE @TrustedFks INT = (
    SELECT COUNT(*) FROM sys.foreign_keys
    WHERE parent_object_id = OBJECT_ID('dbo.RoleModule')
      AND [name] IN ('FK_RoleModule_Role', 'FK_RoleModule_Module')
      AND is_disabled = 0
      AND is_not_trusted = 0
      AND delete_referential_action = 0
      AND update_referential_action = 0
      AND is_not_for_replication = 0
);
IF @TrustedFks <> 2
BEGIN
    RAISERROR('Verification failed: expected 2 enabled, trusted, NO ACTION foreign keys on dbo.RoleModule, found %d. A same-named FK carrying ON DELETE/UPDATE CASCADE (or NOT FOR REPLICATION) does not count — cascading deletes on this join table would silently destroy permission grants.', 16, 1, @TrustedFks);
    RETURN;
END;

-- (b) Each FK binds the right column to the right table and column. A same-named
--     constraint pointing somewhere else would sail through a name-only check.
IF NOT EXISTS (
    SELECT 1
    FROM sys.foreign_keys fk
    JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
    WHERE fk.[name] = 'FK_RoleModule_Role'
      AND fk.parent_object_id = OBJECT_ID('dbo.RoleModule')
      AND fk.referenced_object_id = OBJECT_ID('dbo.Role')
      AND COL_NAME(fkc.parent_object_id, fkc.parent_column_id) = 'RoleId'
      AND COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) = 'Id'
)
BEGIN
    RAISERROR('Verification failed: FK_RoleModule_Role does not bind dbo.RoleModule.RoleId -> dbo.Role.Id.', 16, 1);
    RETURN;
END;

IF NOT EXISTS (
    SELECT 1
    FROM sys.foreign_keys fk
    JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
    WHERE fk.[name] = 'FK_RoleModule_Module'
      AND fk.parent_object_id = OBJECT_ID('dbo.RoleModule')
      AND fk.referenced_object_id = OBJECT_ID('dbo.Module')
      AND COL_NAME(fkc.parent_object_id, fkc.parent_column_id) = 'ModuleId'
      AND COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) = 'Id'
)
BEGIN
    RAISERROR('Verification failed: FK_RoleModule_Module does not bind dbo.RoleModule.ModuleId -> dbo.Module.Id.', 16, 1);
    RETURN;
END;

-- Each FK must bind exactly ONE column pair. The two binding checks above prove the
-- intended pair is present, but a same-named COMPOSITE foreign key would satisfy
-- them while additionally constraining columns nobody asked for.
DECLARE @FkColumnPairs INT = (
    SELECT COUNT(*)
    FROM sys.foreign_keys fk
    JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
    WHERE fk.parent_object_id = OBJECT_ID('dbo.RoleModule')
      AND fk.[name] IN ('FK_RoleModule_Role', 'FK_RoleModule_Module')
);
IF @FkColumnPairs <> 2
BEGIN
    RAISERROR('Verification failed: expected exactly 2 foreign-key column pairs across FK_RoleModule_Role and FK_RoleModule_Module (one each), found %d — a same-named composite FK binds more columns than intended.', 16, 1, @FkColumnPairs);
    RETURN;
END;

-- (c) Both unique indexes exist with the right key columns in the right order.
--     key_ordinal > 0 is required, NOT is_included_column = 0: on a clustered
--     table the clustered key (Id) is carried in a nonclustered unique index and
--     shows up in sys.index_columns with key_ordinal = 0 and
--     is_included_column = 0, which would corrupt the comparison below.
--     is_disabled = 0, has_filter = 0 and ignore_dup_key = 0 are also required:
--     a filtered, disabled or IGNORE_DUP_KEY index of the same name is unique in
--     name only and must not be allowed to satisfy this check.
DECLARE @RmUqCols NVARCHAR(200) = (
    SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
               WITHIN GROUP (ORDER BY ic.key_ordinal)
    FROM sys.indexes i
    JOIN sys.index_columns ic
      ON ic.object_id = i.object_id AND ic.index_id = i.index_id
    WHERE i.[name] = 'UQ_RoleModule_RoleId_ModuleId'
      AND i.object_id = OBJECT_ID('dbo.RoleModule')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.has_filter = 0
      AND i.ignore_dup_key = 0
      AND ic.key_ordinal > 0
);
DECLARE @RmUqColsMsg NVARCHAR(200) = COALESCE(@RmUqCols, N'<missing>');
IF @RmUqCols IS NULL OR @RmUqCols <> N'RoleId,ModuleId'
BEGIN
    RAISERROR('Verification failed: UQ_RoleModule_RoleId_ModuleId is missing, disabled, filtered, IGNORE_DUP_KEY, or has the wrong key columns — got "%s", expected "RoleId,ModuleId". A same-named index that does not enforce global uniqueness counts as missing here, on purpose.', 16, 1, @RmUqColsMsg);
    RETURN;
END;

DECLARE @MUqCols NVARCHAR(200) = (
    SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
               WITHIN GROUP (ORDER BY ic.key_ordinal)
    FROM sys.indexes i
    JOIN sys.index_columns ic
      ON ic.object_id = i.object_id AND ic.index_id = i.index_id
    WHERE i.[name] = 'UQ_Module_Name'
      AND i.object_id = OBJECT_ID('dbo.Module')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.has_filter = 0
      AND i.ignore_dup_key = 0
      AND ic.key_ordinal > 0
);
DECLARE @MUqColsMsg NVARCHAR(200) = COALESCE(@MUqCols, N'<missing>');
IF @MUqCols IS NULL OR @MUqCols <> N'Name'
BEGIN
    RAISERROR('Verification failed: UQ_Module_Name is missing, disabled, filtered, IGNORE_DUP_KEY, or has the wrong key columns — got "%s", expected "Name". A same-named index that does not enforce global uniqueness counts as missing here, on purpose.', 16, 1, @MUqColsMsg);
    RETURN;
END;

-- (d) Row counts unchanged. Constraint DDL cannot move rows, so a difference here
--     means something else wrote concurrently — abort rather than report success.
DECLARE @RmNow INT = (SELECT COUNT(*) FROM dbo.[RoleModule]);
DECLARE @MNow  INT = (SELECT COUNT(*) FROM dbo.[Module]);
DECLARE @RmWas INT = (SELECT [RoleModuleRows] FROM #U053Baseline);
DECLARE @MWas  INT = (SELECT [ModuleRows]     FROM #U053Baseline);
IF @RmNow <> @RmWas OR @MNow <> @MWas
BEGIN
    RAISERROR('Verification failed: row counts moved during the run — dbo.RoleModule %d -> %d, dbo.Module %d -> %d. A concurrent writer touched these tables; the whole run has been rolled back and nothing was applied. Re-run this file in a quiet window.', 16, 1, @RmWas, @RmNow, @MWas, @MNow);
    RETURN;
END;

DROP TABLE IF EXISTS #U053Baseline;

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — 4 constraints present and verified:';
PRINT '       FK_RoleModule_Role · FK_RoleModule_Module  (both enabled + trusted)';
PRINT '       UQ_RoleModule_RoleId_ModuleId · UQ_Module_Name';
PRINT CONCAT('       dbo.RoleModule ', @RmNow, ' rows · dbo.Module ', @MNow, ' rows (unchanged).');
PRINT '────────────────────────────────────────────────────────────';
GO
