-- Phase 1b — Review Notifications
-- Re-issues UserProject sprocs to thread @RoleId. Read sprocs LEFT JOIN
-- dbo.Role to surface RoleName denormalized for the recipient resolver
-- and React UI dropdowns. @RoleId defaults to NULL so callers that
-- haven't been updated still work (existing UserProject rows stay
-- unclassified).
-- Idempotent (CREATE OR ALTER).

CREATE OR ALTER PROCEDURE CreateUserProject
(
    @UserId BIGINT,
    @ProjectId BIGINT,
    @RoleId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[UserProject]
        ([CreatedDatetime], [ModifiedDatetime], [UserId], [ProjectId],
         [RoleId], [CreatedByUserId], [ModifiedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[ProjectId],
        INSERTED.[RoleId],
        CAST(NULL AS NVARCHAR(255)) AS [RoleName],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    VALUES
        (@Now, @Now, @UserId, @ProjectId,
         @RoleId, @CreatedByUserId, COALESCE(@ModifiedByUserId, @CreatedByUserId));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserProjects
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        up.[Id],
        up.[PublicId],
        up.[RowVersion],
        CONVERT(VARCHAR(19), up.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), up.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        up.[UserId],
        up.[ProjectId],
        up.[RoleId],
        r.[Name] AS [RoleName],
        up.[CreatedByUserId],
        up.[ModifiedByUserId]
    FROM dbo.[UserProject] up
    LEFT JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
    ORDER BY up.[UserId] ASC, up.[ProjectId] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserProjectById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        up.[Id],
        up.[PublicId],
        up.[RowVersion],
        CONVERT(VARCHAR(19), up.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), up.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        up.[UserId],
        up.[ProjectId],
        up.[RoleId],
        r.[Name] AS [RoleName],
        up.[CreatedByUserId],
        up.[ModifiedByUserId]
    FROM dbo.[UserProject] up
    LEFT JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
    WHERE up.[Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserProjectByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        up.[Id],
        up.[PublicId],
        up.[RowVersion],
        CONVERT(VARCHAR(19), up.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), up.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        up.[UserId],
        up.[ProjectId],
        up.[RoleId],
        r.[Name] AS [RoleName],
        up.[CreatedByUserId],
        up.[ModifiedByUserId]
    FROM dbo.[UserProject] up
    LEFT JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
    WHERE up.[PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserProjectByUserId
(
    @UserId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        up.[Id],
        up.[PublicId],
        up.[RowVersion],
        CONVERT(VARCHAR(19), up.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), up.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        up.[UserId],
        up.[ProjectId],
        up.[RoleId],
        r.[Name] AS [RoleName],
        up.[CreatedByUserId],
        up.[ModifiedByUserId]
    FROM dbo.[UserProject] up
    LEFT JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
    WHERE up.[UserId] = @UserId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserProjectByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        up.[Id],
        up.[PublicId],
        up.[RowVersion],
        CONVERT(VARCHAR(19), up.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), up.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        up.[UserId],
        up.[ProjectId],
        up.[RoleId],
        r.[Name] AS [RoleName],
        up.[CreatedByUserId],
        up.[ModifiedByUserId]
    FROM dbo.[UserProject] up
    LEFT JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
    WHERE up.[ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateUserProjectById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @ProjectId BIGINT,
    @RoleId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[UserProject]
    SET
        [ModifiedDatetime] = @Now,
        [UserId] = @UserId,
        [ProjectId] = @ProjectId,
        [RoleId] = @RoleId,
        [ModifiedByUserId] = @ModifiedByUserId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[ProjectId],
        INSERTED.[RoleId],
        CAST(NULL AS NVARCHAR(255)) AS [RoleName],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteUserProjectById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[UserProject]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        DELETED.[ProjectId],
        DELETED.[RoleId],
        CAST(NULL AS NVARCHAR(255)) AS [RoleName],
        DELETED.[CreatedByUserId],
        DELETED.[ModifiedByUserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
