-- =============================================================================
-- 2026-06-09 — Restrict review notification recipients to real human users.
--
-- Excludes two non-human classes from the recipient walk:
--   (a) LLM agent accounts — dbo.User.IsAgent = 1
--   (b) Persona test accounts — dbo.Auth.Username starting with 'persona_'
--
-- Personas are end-to-end test accounts seeded by intelligence/persistence/sql/
-- seed.personas.sql (UserIds 55-61):
--   persona_owner / persona_tenant_admin / persona_controller /
--   persona_project_manager / persona_field_crew / persona_intern /
--   persona_sysadmin
-- Each persona is linked into every active project's UserProject (with the
-- right RoleId on Owner/PM rows). Deliberate for cross-flow notification
-- testing — without this filter, every review draft TO/CC included
-- persona.project.manager@buildone.test / persona.owner@buildone.test
-- alongside the real PMs/Owners.
--
-- Filter applied identically in each resolver's role-walk CTE:
--   AND NOT EXISTS (
--       SELECT 1 FROM dbo.[User] u
--       WHERE u.[Id] = up.[UserId] AND u.[IsAgent] = 1
--   )
--   AND NOT EXISTS (
--       SELECT 1 FROM dbo.[Auth] a
--       WHERE a.[UserId] = up.[UserId]
--         AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
--   )
--
-- LTRIM defends against an accidental leading-whitespace Auth.Username
-- bypassing the prefix match. N-prefix preserves the v2 sproc's literal
-- discipline.
--
-- The persona prefix is reserved-by-convention: any future legitimate
-- user must not have an Auth.Username starting with 'persona_'.
--
-- Three sprocs touched:
--   1. dbo.ResolveReviewRecipientsByBillId           (supersedes 001)
--   2. dbo.ResolveReviewRecipientsByContractLaborId  (supersedes 004)
--   3. dbo.ResolveContractLaborReviewRecipientsPerProject (supersedes 007)
--
-- *** Migration ordering note ***
-- If 001, 004, or 007 is ever re-applied AFTER 008, the human-only filter
-- is wiped. Header comments in those files now warn against that.
--
-- Verified pre-migration via DB query: filter matches all 7 seeded personas
-- (UserIds 55-61) and no false positives across the ~100 non-persona
-- accounts. Empirical side-by-side on ContractLabor 597 (Wilmer Diaz):
-- 4 recipients before → 2 after (drops Persona Owner + Persona PM, keeps
-- Austin Rogers + Tanner Baker).
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Bill resolver — filter personas in UserProjectRoles
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ResolveReviewRecipientsByBillId
(
    @BillId BIGINT,
    @ExcludeUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    WITH BillProjects AS (
        SELECT DISTINCT bli.[ProjectId]
        FROM dbo.[BillLineItem] bli
        WHERE bli.[BillId] = @BillId
          AND bli.[ProjectId] IS NOT NULL
    ),
    UserProjectRoles AS (
        SELECT
            up.[UserId],
            up.[ProjectId],
            r.[Name] AS [RoleName],
            CASE r.[Name]
                WHEN 'Project Manager' THEN 1
                WHEN 'Owner'           THEN 2
                ELSE 99
            END AS [RolePrecedence]
        FROM dbo.[UserProject] up
        INNER JOIN BillProjects bp ON bp.[ProjectId] = up.[ProjectId]
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] IN ('Project Manager', 'Owner')
          AND (@ExcludeUserId IS NULL OR up.[UserId] <> @ExcludeUserId)
          -- Restrict recipients to real human users: exclude LLM agent
          -- accounts (User.IsAgent = 1) and persona test accounts
          -- (Auth.Username starting with 'persona_', whitespace-tolerant).
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[User] u
              WHERE u.[Id] = up.[UserId]
                AND u.[IsAgent] = 1
          )
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[Auth] a
              WHERE a.[UserId] = up.[UserId]
                AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
          )
    ),
    DedupedRoles AS (
        SELECT
            [UserId],
            [RoleName],
            [ProjectId],
            ROW_NUMBER() OVER (
                PARTITION BY [UserId]
                ORDER BY [RolePrecedence] ASC, [ProjectId] ASC
            ) AS rn
        FROM UserProjectRoles
    ),
    UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        u.[Id]        AS [UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        dr.[RoleName],
        dr.[ProjectId]
    FROM DedupedRoles dr
    INNER JOIN dbo.[User] u ON u.[Id] = dr.[UserId]
    LEFT JOIN UserEmails ue
        ON ue.[UserId] = dr.[UserId]
       AND ue.rn = 1
    WHERE dr.rn = 1
    ORDER BY dr.[RoleName], u.[Lastname], u.[Firstname];

    COMMIT TRANSACTION;
END;
GO


-- -----------------------------------------------------------------------------
-- 2. ContractLabor resolver — filter personas in UserProjectRoles
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ResolveReviewRecipientsByContractLaborId
(
    @ContractLaborId BIGINT,
    @ExcludeUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    WITH ContractLaborProjects AS (
        SELECT DISTINCT cli.[ProjectId]
        FROM dbo.[ContractLaborLineItem] cli
        WHERE cli.[ContractLaborId] = @ContractLaborId
          AND cli.[ProjectId] IS NOT NULL
    ),
    UserProjectRoles AS (
        SELECT
            up.[UserId],
            up.[ProjectId],
            r.[Name] AS [RoleName],
            CASE r.[Name]
                WHEN 'Project Manager' THEN 1
                WHEN 'Owner'           THEN 2
                ELSE 99
            END AS [RolePrecedence]
        FROM dbo.[UserProject] up
        INNER JOIN ContractLaborProjects clp ON clp.[ProjectId] = up.[ProjectId]
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] IN ('Project Manager', 'Owner')
          AND (@ExcludeUserId IS NULL OR up.[UserId] <> @ExcludeUserId)
          -- Restrict recipients to real human users: exclude LLM agent
          -- accounts (User.IsAgent = 1) and persona test accounts
          -- (Auth.Username starting with 'persona_', whitespace-tolerant).
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[User] u
              WHERE u.[Id] = up.[UserId]
                AND u.[IsAgent] = 1
          )
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[Auth] a
              WHERE a.[UserId] = up.[UserId]
                AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
          )
    ),
    DedupedRoles AS (
        SELECT
            [UserId],
            [RoleName],
            [ProjectId],
            ROW_NUMBER() OVER (
                PARTITION BY [UserId]
                ORDER BY [RolePrecedence] ASC, [ProjectId] ASC
            ) AS rn
        FROM UserProjectRoles
    ),
    UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        u.[Id]        AS [UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        dr.[RoleName],
        dr.[ProjectId]
    FROM DedupedRoles dr
    INNER JOIN dbo.[User] u ON u.[Id] = dr.[UserId]
    LEFT JOIN UserEmails ue
        ON ue.[UserId] = dr.[UserId]
       AND ue.rn = 1
    WHERE dr.rn = 1
    ORDER BY dr.[RoleName], u.[Lastname], u.[Firstname];

    COMMIT TRANSACTION;
END;
GO


-- -----------------------------------------------------------------------------
-- 3. Per-project ContractLabor resolver (v2 envelope, includes Owners)
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ResolveContractLaborReviewRecipientsPerProject
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    WITH ContractLaborProjects AS (
        SELECT DISTINCT cli.[ProjectId]
        FROM dbo.[ContractLaborLineItem] cli
        WHERE cli.[ContractLaborId] = @ContractLaborId
          AND cli.[ProjectId] IS NOT NULL
    ),
    UserProjectRoles AS (
        SELECT
            up.[ProjectId],
            up.[UserId],
            r.[Name] AS [RoleName],
            CASE r.[Name]
                WHEN N'Project Manager' THEN 1
                WHEN N'Owner'           THEN 2
                ELSE 99
            END AS [RolePrecedence]
        FROM dbo.[UserProject] up
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] IN (N'Project Manager', N'Owner')
          -- Restrict recipients to real human users: exclude LLM agent
          -- accounts (User.IsAgent = 1) and persona test accounts
          -- (Auth.Username starting with 'persona_', whitespace-tolerant).
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[User] u
              WHERE u.[Id] = up.[UserId]
                AND u.[IsAgent] = 1
          )
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[Auth] a
              WHERE a.[UserId] = up.[UserId]
                AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
          )
    ),
    -- PM wins when a user holds both roles on the same project.
    DedupedUserProjectRoles AS (
        SELECT
            [ProjectId],
            [UserId],
            [RoleName],
            ROW_NUMBER() OVER (
                PARTITION BY [ProjectId], [UserId]
                ORDER BY [RolePrecedence] ASC
            ) AS rn
        FROM UserProjectRoles
    ),
    UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        clp.[ProjectId],
        p.[Name]         AS [ProjectName],
        p.[Abbreviation] AS [ProjectAbbreviation],
        dpr.[UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        dpr.[RoleName]
    FROM ContractLaborProjects clp
    INNER JOIN dbo.[Project] p ON p.[Id] = clp.[ProjectId]
    LEFT JOIN DedupedUserProjectRoles dpr
        ON dpr.[ProjectId] = clp.[ProjectId]
       AND dpr.rn = 1
    LEFT JOIN dbo.[User] u      ON u.[Id] = dpr.[UserId]
    LEFT JOIN UserEmails ue     ON ue.[UserId] = dpr.[UserId] AND ue.rn = 1
    ORDER BY clp.[ProjectId], dpr.[RoleName], u.[Lastname], u.[Firstname];
END;
GO


PRINT 'ResolveReviewRecipientsByBillId — human-only filter applied (supersedes 001).';
PRINT 'ResolveReviewRecipientsByContractLaborId — human-only filter applied (supersedes 004).';
PRINT 'ResolveContractLaborReviewRecipientsPerProject — human-only filter applied (supersedes 007).';
PRINT 'Migration 008 applied — review recipient resolvers exclude LLM agents and persona test accounts.';
