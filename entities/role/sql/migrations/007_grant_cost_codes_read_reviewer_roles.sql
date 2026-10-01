-- Grant 'Cost Codes' READ to the human reviewer roles that already hold 'Tasks'
-- read, so the iOS Tasks tab's cost-code catalog loads for the people it was
-- built for.
--
-- Why (U-553). Both catalog reads the iOS picker uses — GET /get/sub-cost-codes
-- and GET /get/sub-cost-code/search — are gated on Modules.COST_CODES
-- (entities/sub_cost_code/api/router.py). Migration 003 granted Tasks read to
-- Project Manager / Reviewer / AP Specialist / AR Specialist and said nothing
-- about Cost Codes, and every existing Cost Codes grant goes to a service
-- identity, Controller, Owner or Tenant Admin. Verified against prod on
-- 2026-10-01: Cassidy (18), Zach (19) and Tanner (43) are Project Manager and
-- therefore 403 on the catalog — an empty picker, and approval carries the
-- coding; Austin (20) is Owner and does not. System admins bypass module checks,
-- which is why this never surfaced in single-account testing.
--
-- Scope: READ ONLY. These roles must never mutate the chart of cost codes — it is
-- reference data (umbrella memory feedback_qbo_accounts_are_reference_data).
-- Deliberately NOT granted to AP Specialist / AR Specialist. Their Tasks read
-- covers the Sent-box and `scope=all` views, neither of which codes a line item;
-- the iOS tab renders `scope=mine` filtered to `assigned_to_me`, and assignment
-- arrives through the Project Manager / Owner envelope — so anyone who becomes a
-- reviewer holds one of those two roles on the project and is covered here.
-- Module-level RBAC means this read also admits every other GET gated on
-- `Cost Codes`; the catalog is reference data, so that is acceptable.
--
-- Idempotent (MERGE). Safe to re-run: an existing readable row is left untouched.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py entities/role/sql/migrations/007_grant_cost_codes_read_reviewer_roles.sql

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @Now         DATETIME2(3) = SYSUTCDATETIME();
DECLARE @CostCodesId BIGINT       = (SELECT [Id] FROM dbo.[Module] WHERE [Name] = N'Cost Codes');

IF @CostCodesId IS NULL
BEGIN
    RAISERROR('Cost Codes Module row missing — run entities/module/sql/seed.AllModules.sql first.', 16, 1);
    RETURN;
END;

DECLARE @ReadRoles TABLE ([Name] NVARCHAR(100));
INSERT INTO @ReadRoles ([Name]) VALUES
    (N'Project Manager'),
    (N'Reviewer');

MERGE dbo.[RoleModule] AS target
USING (
    SELECT r.[Id] AS RoleId, @CostCodesId AS ModuleId
    FROM dbo.[Role] r
    INNER JOIN @ReadRoles rr ON rr.[Name] = r.[Name]
) AS src
ON target.[RoleId] = src.RoleId AND target.[ModuleId] = src.ModuleId
WHEN MATCHED AND target.[CanRead] = 0 THEN
    UPDATE SET [CanRead] = 1, [ModifiedDatetime] = @Now
WHEN NOT MATCHED THEN
    INSERT ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
            [CanCreate], [CanRead], [CanUpdate], [CanDelete],
            [CanSubmit], [CanApprove], [CanComplete])
    VALUES (@Now, @Now, src.RoleId, src.ModuleId,
            0, 1, 0, 0, 0, 0, 0);

PRINT CONCAT('  Cost Codes read for Project Manager / Reviewer: ', @@ROWCOUNT, ' grants merged');
