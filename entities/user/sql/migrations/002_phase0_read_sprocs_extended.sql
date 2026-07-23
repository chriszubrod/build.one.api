-- Phase 0 — Access Control Rebuild
-- Update Read sprocs on dbo.[User] to also return the new access-control
-- discriminator columns (IsSystemAdmin, IsAgent, LastCompanyId).
-- CreateUser / UpdateUserById / DeleteUserById are NOT touched — those
-- columns get dedicated mutation sprocs in Phase 1+.
-- Idempotent (CREATE OR ALTER).

CREATE OR ALTER PROCEDURE ReadUsers
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[User]
    ORDER BY [Lastname] ASC, [Firstname] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserById
(
    @Id BIGINT
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
        [Firstname],
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[User]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserByPublicId
(
    @PublicId UNIQUEIDENTIFIER
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
        [Firstname],
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[User]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserByFirstname
(
    @Firstname NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[User]
    WHERE [Firstname] = @Firstname;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserByLastname
(
    @Lastname NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[User]
    WHERE [Lastname] = @Lastname;

    COMMIT TRANSACTION;
END;
GO


-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-126, 2026-07-23) — sproc body removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Persist the user's last active Company on switch-company.
--
-- The canonical definition of this sproc now lives in exactly ONE place:
--   entities/user/sql/dbo.user.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.SetUserLastCompanyId
--
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------
