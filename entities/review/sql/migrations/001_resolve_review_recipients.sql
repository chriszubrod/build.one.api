-- Phase 1c — Review Notifications
-- Resolves the user list to notify when a Review is submitted on a Bill.
-- Walk: Bill -> BillLineItem -> Project -> UserProject (filtered to
-- 'Project Manager' / 'Owner' roles) -> User -> Contact (first non-null
-- Email per user, picked by Contact.Id ascending).
--
-- Bills span multiple projects via their line items; this resolver
-- unions across all distinct ProjectIds on the bill. Recipients are
-- deduped by UserId with PM beating Owner when a user holds both roles
-- across the bill's projects.
--
-- Recipients without an email row are still returned (Email = NULL)
-- so the caller can log + count them. The caller filters at send time.
--
-- Idempotent (CREATE OR ALTER).

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
