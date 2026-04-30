-- Phase 0 — Access Control Rebuild — Backfill
-- One-shot backfill that:
--   1. Sets User.IsSystemAdmin = 1 for users currently holding the "Admin" role
--      (this is the prior magic-string bypass; it now becomes an explicit flag).
--   2. Sets User.IsAgent = 1 for users currently holding any agent role
--      (Agent Orchestrator + *_specialist).
--   3. Sets Company.OrganizationId from the single existing OrganizationCompany row.
--   4. Sets UserRole.CompanyId for all existing rows to the single existing Company.
--   5. Sets UserModule.CompanyId for all existing rows to the single existing Company.
--   6. Inserts UserCompany rows for every User who doesn't already have one,
--      granting access to the single existing Company.
--
-- Idempotent — safe to re-run. Each step is gated so no row is double-mutated.
-- Required: at least one Company and at least one Organization must exist.
-- Required: the existing OrganizationCompany row must be present (used as the
-- source for Company.OrganizationId).

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @SingleCompanyId BIGINT;
DECLARE @SingleOrganizationId BIGINT;

SELECT @SingleCompanyId = MIN([Id]) FROM dbo.[Company];
SELECT @SingleOrganizationId = MIN([Id]) FROM dbo.[Organization];

IF @SingleCompanyId IS NULL OR @SingleOrganizationId IS NULL
BEGIN
    RAISERROR('Backfill aborted: Company or Organization is missing.', 16, 1);
    RETURN;
END

PRINT CONCAT('Single Company id: ', @SingleCompanyId);
PRINT CONCAT('Single Organization id: ', @SingleOrganizationId);

-------------------------------------------------------------------------------
-- 1. User.IsSystemAdmin
-------------------------------------------------------------------------------
UPDATE u
   SET u.[IsSystemAdmin] = 1
  FROM dbo.[User] u
  JOIN dbo.[UserRole] ur ON ur.[UserId] = u.[Id]
  JOIN dbo.[Role] r ON r.[Id] = ur.[RoleId]
 WHERE LOWER(LTRIM(RTRIM(r.[Name]))) = 'admin'
   AND u.[IsSystemAdmin] = 0;
PRINT CONCAT('IsSystemAdmin set on ', @@ROWCOUNT, ' user(s).');

-------------------------------------------------------------------------------
-- 2. User.IsAgent
-------------------------------------------------------------------------------
UPDATE u
   SET u.[IsAgent] = 1
  FROM dbo.[User] u
  JOIN dbo.[UserRole] ur ON ur.[UserId] = u.[Id]
  JOIN dbo.[Role] r ON r.[Id] = ur.[RoleId]
 WHERE (
        LOWER(LTRIM(RTRIM(r.[Name]))) = 'agent orchestrator'
     OR LOWER(LTRIM(RTRIM(r.[Name]))) LIKE '%specialist%'
       )
   AND u.[IsAgent] = 0;
PRINT CONCAT('IsAgent set on ', @@ROWCOUNT, ' user(s).');

-------------------------------------------------------------------------------
-- 3. Company.OrganizationId — pulled from the single existing
--    OrganizationCompany join row, falling back to the single Organization.
-------------------------------------------------------------------------------
UPDATE c
   SET c.[OrganizationId] = COALESCE(oc.[OrganizationId], @SingleOrganizationId)
  FROM dbo.[Company] c
  LEFT JOIN dbo.[OrganizationCompany] oc ON oc.[CompanyId] = c.[Id]
 WHERE c.[OrganizationId] IS NULL;
PRINT CONCAT('Company.OrganizationId backfilled on ', @@ROWCOUNT, ' row(s).');

-------------------------------------------------------------------------------
-- 4. UserRole.CompanyId
-------------------------------------------------------------------------------
UPDATE dbo.[UserRole]
   SET [CompanyId] = @SingleCompanyId
 WHERE [CompanyId] IS NULL;
PRINT CONCAT('UserRole.CompanyId backfilled on ', @@ROWCOUNT, ' row(s).');

-------------------------------------------------------------------------------
-- 5. UserModule.CompanyId
-------------------------------------------------------------------------------
UPDATE dbo.[UserModule]
   SET [CompanyId] = @SingleCompanyId
 WHERE [CompanyId] IS NULL;
PRINT CONCAT('UserModule.CompanyId backfilled on ', @@ROWCOUNT, ' row(s).');

-------------------------------------------------------------------------------
-- 6. UserCompany — every existing User gets a row for the single Company
--    so they can authenticate after Phase 0 lands.
-------------------------------------------------------------------------------
INSERT INTO dbo.[UserCompany] ([CreatedDatetime], [ModifiedDatetime], [UserId], [CompanyId])
SELECT SYSUTCDATETIME(), SYSUTCDATETIME(), u.[Id], @SingleCompanyId
  FROM dbo.[User] u
 WHERE NOT EXISTS (
        SELECT 1 FROM dbo.[UserCompany] uc
         WHERE uc.[UserId] = u.[Id]
           AND uc.[CompanyId] = @SingleCompanyId
       );
PRINT CONCAT('UserCompany rows inserted: ', @@ROWCOUNT);

PRINT 'Phase 0 backfill complete.';
