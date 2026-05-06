-- Gap 1 — Project list scoped by UserProject membership.
-- Per Q1.2 = (a): non-admin users see only Projects they have a
-- UserProject row for. System admins (@ActorIsSystemAdmin = 1) bypass.
-- NULL @ActorUserId also bypasses (back-compat during deploy).
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

CREATE OR ALTER PROCEDURE ReadProjects
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project] p
    WHERE
        @ActorIsSystemAdmin = 1
        OR @ActorUserId IS NULL
        OR EXISTS (
            SELECT 1 FROM dbo.[UserProject] up
            WHERE up.[UserId] = @ActorUserId AND up.[ProjectId] = p.[Id]
        )
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadProjectById
(
    @Id BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project] p
    WHERE [Id] = @Id
      AND (
            @ActorIsSystemAdmin = 1
            OR @ActorUserId IS NULL
            OR EXISTS (
                SELECT 1 FROM dbo.[UserProject] up
                WHERE up.[UserId] = @ActorUserId AND up.[ProjectId] = p.[Id]
            )
      );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadProjectByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project] p
    WHERE [PublicId] = @PublicId
      AND (
            @ActorIsSystemAdmin = 1
            OR @ActorUserId IS NULL
            OR EXISTS (
                SELECT 1 FROM dbo.[UserProject] up
                WHERE up.[UserId] = @ActorUserId AND up.[ProjectId] = p.[Id]
            )
      );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadProjectByName
(
    @Name NVARCHAR(50),
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project] p
    WHERE [Name] = @Name
      AND (
            @ActorIsSystemAdmin = 1
            OR @ActorUserId IS NULL
            OR EXISTS (
                SELECT 1 FROM dbo.[UserProject] up
                WHERE up.[UserId] = @ActorUserId AND up.[ProjectId] = p.[Id]
            )
      );

    COMMIT TRANSACTION;
END;
GO

PRINT 'Gap 1 Project scope filter applied.';
