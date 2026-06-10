-- =============================================================================
-- 2026-06-10 — dbo.ReadWorkers — curated list for the time-entry worker picker.
--
-- "Workers" are users who can have hours billed to them. The picker on the
-- web (and eventually iOS) populates from this list. The set is:
--   - excludes LLM agents          (User.IsAgent = 1)
--   - excludes persona test users  (Auth.Username starts with 'persona_')
--   - includes anyone with an Employee or Vendor FK linkage
--   - includes anyone holding a 'Field Crew' or 'Intern' role (covers
--     interns and non-W2 crew whose User row isn't linked to an Employee
--     or Vendor record)
--
-- Excludes pure-admin / PM / Owner / Controller / Reviewer / Auditor /
-- Time Clerk / AP-Spec / AR-Spec / Tenant Admin roles — those users
-- don't log their own time and shouldn't clutter the picker.
--
-- Same column shape as ReadUsers so the existing UserRepository._from_db
-- hydrator handles the row without changes.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =============================================================================

CREATE OR ALTER PROCEDURE dbo.ReadWorkers
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        u.[Id],
        u.[PublicId],
        u.[RowVersion],
        CONVERT(VARCHAR(19), u.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), u.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        u.[Firstname],
        u.[Lastname],
        u.[IsSystemAdmin],
        u.[IsAgent],
        u.[LastCompanyId],
        u.[CreatedByUserId],
        u.[ModifiedByUserId],
        u.[EmployeeId],
        u.[VendorId]
    FROM dbo.[User] u
    WHERE ISNULL(u.[IsAgent], 0) = 0
      AND NOT EXISTS (
          SELECT 1
          FROM dbo.[Auth] a
          WHERE a.[UserId] = u.[Id]
            AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
      )
      AND (
          u.[EmployeeId] IS NOT NULL
          OR u.[VendorId] IS NOT NULL
          OR EXISTS (
              SELECT 1
              FROM dbo.[UserRole] ur
              INNER JOIN dbo.[Role] r ON r.[Id] = ur.[RoleId]
              WHERE ur.[UserId] = u.[Id]
                AND r.[Name] IN (N'Field Crew', N'Intern')
          )
      )
    ORDER BY u.[Lastname] ASC, u.[Firstname] ASC;
END;
GO


PRINT 'ReadWorkers created.';
