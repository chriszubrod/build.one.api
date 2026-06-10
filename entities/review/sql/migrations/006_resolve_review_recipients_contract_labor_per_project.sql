-- =============================================================================
-- !!! SUPERSEDED — DO NOT RE-APPLY IN ISOLATION !!!
-- This was the v1 of dbo.ResolveContractLaborReviewRecipientsPerProject
-- (PM-only). Two later migrations replaced it:
--   - 007 added Owners (PM goes TO, Owner goes CC).
--   - 008 added the human-only filter (no LLM agents, no persona test accounts).
-- Re-applying this file resets the sproc to PM-only with no filter, wiping
-- both fixes. If you need to touch the sproc body, edit 008 instead, or
-- chase this file with a re-run of 007 followed by 008.
--
-- 2026-06-03 — Per-project recipient resolver for ContractLabor reviews.
--
-- Distinct from ResolveReviewRecipientsByContractLaborId (which dedupes by
-- UserId for the bill-style single-email pattern). This one returns ONE
-- ROW PER (Project, PM) so the notification service can build a separate
-- draft per project on the ContractLabor.
--
-- Projects with NO PM still surface (left-join shape) so the caller can
-- still create a draft with an empty TO list for projects lacking a
-- configured Project Manager — per Chris' product decision: never auto-
-- send, just create the draft for manual address-and-send.
-- =============================================================================

CREATE OR ALTER PROCEDURE dbo.ResolveContractLaborReviewRecipientsPerProject
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    -- Distinct non-null projects on this CL's line items.
    WITH ContractLaborProjects AS (
        SELECT DISTINCT cli.[ProjectId]
        FROM dbo.[ContractLaborLineItem] cli
        WHERE cli.[ContractLaborId] = @ContractLaborId
          AND cli.[ProjectId] IS NOT NULL
    ),
    PMsPerProject AS (
        SELECT
            up.[ProjectId],
            up.[UserId]
        FROM dbo.[UserProject] up
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] = N'Project Manager'
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
        pm.[UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email]
    FROM ContractLaborProjects clp
    INNER JOIN dbo.[Project] p ON p.[Id] = clp.[ProjectId]
    LEFT JOIN PMsPerProject pm ON pm.[ProjectId] = clp.[ProjectId]
    LEFT JOIN dbo.[User] u      ON u.[Id] = pm.[UserId]
    LEFT JOIN UserEmails ue     ON ue.[UserId] = pm.[UserId] AND ue.rn = 1
    ORDER BY clp.[ProjectId], u.[Lastname], u.[Firstname];
END;
GO


PRINT 'ResolveContractLaborReviewRecipientsPerProject created.';
