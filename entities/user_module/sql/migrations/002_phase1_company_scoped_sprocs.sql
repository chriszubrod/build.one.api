-- Phase 1 — Access Control Rebuild
-- Re-issue UserModule sprocs to thread CompanyId, CreatedByUserId,
-- ModifiedByUserId. Adds ReadUserModulesByUserIdAndCompanyId for the
-- Phase 2 permission resolver. New params default to NULL.
-- Idempotent (CREATE OR ALTER).

CREATE OR ALTER PROCEDURE CreateUserModule
(
    @UserId BIGINT,
    @ModuleId BIGINT,
    @CompanyId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[UserModule]
        ([CreatedDatetime], [ModifiedDatetime], [UserId], [ModuleId],
         [CompanyId], [CreatedByUserId], [ModifiedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[ModuleId],
        INSERTED.[CompanyId],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    VALUES
        (@Now, @Now, @UserId, @ModuleId,
         @CompanyId, @CreatedByUserId, COALESCE(@ModifiedByUserId, @CreatedByUserId));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserModules
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [ModuleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserModule]
    ORDER BY [UserId] ASC, [ModuleId] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserModuleById
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
        [UserId],
        [ModuleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserModule]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserModuleByPublicId
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
        [UserId],
        [ModuleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserModule]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserModuleByUserId
(
    @UserId BIGINT
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
        [UserId],
        [ModuleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserModule]
    WHERE [UserId] = @UserId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserModulesByUserId
(
    @UserId BIGINT
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
        [UserId],
        [ModuleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserModule]
    WHERE [UserId] = @UserId
    ORDER BY [ModuleId] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserModuleByModuleId
(
    @ModuleId BIGINT
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
        [UserId],
        [ModuleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserModule]
    WHERE [ModuleId] = @ModuleId;

    COMMIT TRANSACTION;
END;
GO


-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-126, 2026-07-23) — sproc body removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Phase 2 permission resolver — additive UserModule grants per active Company.
--
-- The canonical definition of this sproc now lives in exactly ONE place:
--   entities/user_module/sql/dbo.usermodule.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.ReadUserModulesByUserIdAndCompanyId
--
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------


CREATE OR ALTER PROCEDURE UpdateUserModuleById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @ModuleId BIGINT,
    @CompanyId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[UserModule]
    SET
        [ModifiedDatetime] = @Now,
        [UserId] = @UserId,
        [ModuleId] = @ModuleId,
        [CompanyId] = CASE WHEN @CompanyId IS NULL THEN [CompanyId] ELSE @CompanyId END,
        [ModifiedByUserId] = @ModifiedByUserId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[ModuleId],
        INSERTED.[CompanyId],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteUserModuleById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[UserModule]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        DELETED.[ModuleId],
        DELETED.[CompanyId],
        DELETED.[CreatedByUserId],
        DELETED.[ModifiedByUserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
