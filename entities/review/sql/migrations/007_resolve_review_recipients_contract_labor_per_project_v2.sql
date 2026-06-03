-- =============================================================================
-- 2026-06-03 — v2 of the per-project CL recipient resolver: include Owners.
--
-- Mirrors Bill's ResolveReviewRecipientsByBillId envelope: PMs go TO,
-- Owners go CC. Caller splits on RoleName. RolePrecedence column is
-- included so the caller can rank consistently if a user holds both
-- roles on the same project (PM wins).
-- =============================================================================

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


PRINT 'ResolveContractLaborReviewRecipientsPerProject v2 (with Owners) applied.';
