-- Gap 1 follow-up (2026-05-12) — remove the `@ActorUserId IS NULL`
-- legacy-caller bypass from Project list/read sprocs. Replaces
-- 001_gap1_scope_by_user_project.sql.
--
-- Background: 001 included `OR @ActorUserId IS NULL` so that pre-Gap-1
-- callers (services that hadn't yet learned to thread actor context)
-- would keep working during the staged deploy. After a leak was
-- discovered on the TimeEntry side where this bypass turned into a
-- silent "no actor → show everything" path, we removed it here too
-- to fail closed across the entire user-scoped read surface.
--
-- Scheduler / system callers (X-Drain-Secret-gated admin endpoints
-- in `shared/api/admin.py::_require_drain_secret`) now explicitly
-- set `current_is_system_admin = True` so the sproc's
-- @ActorIsSystemAdmin = 1 clause grants them all-user visibility
-- via the intended path, not via a silent bypass.
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
            OR EXISTS (
                SELECT 1 FROM dbo.[UserProject] up
                WHERE up.[UserId] = @ActorUserId AND up.[ProjectId] = p.[Id]
            )
      );

    COMMIT TRANSACTION;
END;
GO

PRINT 'Gap 1 Project scope filter applied.';
