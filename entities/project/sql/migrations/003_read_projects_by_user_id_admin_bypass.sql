-- Migration: ReadProjectsByUserId — add @ActorIsSystemAdmin bypass
-- When ActorIsSystemAdmin = 1, return all projects without requiring
-- UserProject membership (same pattern as Phase 3 TimeEntry scoping).
-- Closes the gap where IsSystemAdmin=1 users only see projects they
-- have explicit UserProject rows for.

CREATE OR ALTER PROCEDURE ReadProjectsByUserId
(
    @UserId BIGINT,
    @ActorIsSystemAdmin BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF @ActorIsSystemAdmin = 1
    BEGIN
        SELECT
            p.[Id],
            p.[PublicId],
            p.[RowVersion],
            CONVERT(VARCHAR(19), p.[CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), p.[ModifiedDatetime], 120) AS [ModifiedDatetime],
            p.[Name],
            p.[Description],
            p.[Status],
            p.[CustomerId],
            p.[Abbreviation],
            p.[Notes]
        FROM dbo.[Project] p
        ORDER BY p.[Name] ASC;
    END
    ELSE
    BEGIN
        SELECT DISTINCT
            p.[Id],
            p.[PublicId],
            p.[RowVersion],
            CONVERT(VARCHAR(19), p.[CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), p.[ModifiedDatetime], 120) AS [ModifiedDatetime],
            p.[Name],
            p.[Description],
            p.[Status],
            p.[CustomerId],
            p.[Abbreviation],
            p.[Notes]
        FROM dbo.[Project] p
        INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = p.[Id]
        WHERE up.[UserId] = @UserId
        ORDER BY p.[Name] ASC;
    END

    COMMIT TRANSACTION;
END;
GO
