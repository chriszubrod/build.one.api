-- Phase 2 — Access Control Rebuild — seed the 8 human Role rows.
-- Idempotent (IF NOT EXISTS guards). Safe to re-run.
--
-- These are the curated human roles that replace the legacy single
-- 'Admin' role-name model. System administration is now handled by
-- `User.IsSystemAdmin = 1`, not a role; the legacy 'Admin' Role row
-- stays inert until cleaned up in a follow-up.
--
-- No RoleModule grants are seeded here — assign module permissions
-- per-role via the React /role/:id Modules tab. 'Project Manager' may
-- already exist (seeded by 001_seed_review_roles.sql for the review
-- notification system); the IF NOT EXISTS guard skips it.

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

-- 1. Tenant Admin — full access within a tenant; NOT IsSystemAdmin
IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'Tenant Admin')
BEGIN
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@Now, @Now, 'Tenant Admin');
    PRINT 'Seeded Role: Tenant Admin';
END
ELSE
BEGIN
    PRINT 'Role Tenant Admin already exists — skipping.';
END

-- 2. Controller — full ledger oversight (typically gets all financial modules)
IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'Controller')
BEGIN
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@Now, @Now, 'Controller');
    PRINT 'Seeded Role: Controller';
END
ELSE
BEGIN
    PRINT 'Role Controller already exists — skipping.';
END

-- 3. AP Specialist — accounts payable: Bills, Vendors, Expenses, BillCredits
IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'AP Specialist')
BEGIN
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@Now, @Now, 'AP Specialist');
    PRINT 'Seeded Role: AP Specialist';
END
ELSE
BEGIN
    PRINT 'Role AP Specialist already exists — skipping.';
END

-- 4. AR Specialist — accounts receivable: Invoices, Customers, Projects
IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'AR Specialist')
BEGIN
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@Now, @Now, 'AR Specialist');
    PRINT 'Seeded Role: AR Specialist';
END
ELSE
BEGIN
    PRINT 'Role AR Specialist already exists — skipping.';
END

-- 5. Project Manager — project-scoped operational access
--    (already seeded by 001_seed_review_roles.sql; guard is defensive)
IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'Project Manager')
BEGIN
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@Now, @Now, 'Project Manager');
    PRINT 'Seeded Role: Project Manager';
END
ELSE
BEGIN
    PRINT 'Role Project Manager already exists — skipping.';
END

-- 6. Reviewer — read + approve on Bills/Expenses/BillCredits/Invoices
IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'Reviewer')
BEGIN
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@Now, @Now, 'Reviewer');
    PRINT 'Seeded Role: Reviewer';
END
ELSE
BEGIN
    PRINT 'Role Reviewer already exists — skipping.';
END

-- 7. Time Clerk — TimeEntry + ContractLabor entry
IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'Time Clerk')
BEGIN
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@Now, @Now, 'Time Clerk');
    PRINT 'Seeded Role: Time Clerk';
END
ELSE
BEGIN
    PRINT 'Role Time Clerk already exists — skipping.';
END

-- 8. Auditor — read-only across the financial surface
IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'Auditor')
BEGIN
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@Now, @Now, 'Auditor');
    PRINT 'Seeded Role: Auditor';
END
ELSE
BEGIN
    PRINT 'Role Auditor already exists — skipping.';
END

PRINT 'Phase 2 human-role seeding complete.';
