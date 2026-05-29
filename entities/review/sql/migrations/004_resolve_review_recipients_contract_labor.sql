-- =============================================================================
-- 2026-05-28 — Recipient resolver for ContractLabor reviews.
--
-- Walks: ContractLabor → ContractLaborLineItem (distinct ProjectId)
--                     → UserProject (filtered to 'Project Manager' / 'Owner')
--                     → User → Contact (first non-null Email per user)
--
-- A ContractLabor row spans the projects its line items reference (overhead
-- lines have NULL ProjectId; they're excluded). Dedupe by UserId with PM
-- precedence when a user holds both roles across the labor's projects.
--
-- Idempotent (CREATE OR ALTER). Same envelope as ResolveReviewRecipientsByBillId
-- so callers can share post-processing code.
-- =============================================================================

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


PRINT 'ResolveReviewRecipientsByContractLaborId created.';
