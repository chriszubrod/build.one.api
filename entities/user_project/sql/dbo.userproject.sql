IF OBJECT_ID('dbo.UserProject', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[UserProject]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [UserId] BIGINT NOT NULL,
    [ProjectId] BIGINT NOT NULL
);
END
GO

-- UNIQUE(UserId, ProjectId) — fail-loud against accidental duplicate
-- grants from backfill scripts. Added 2026-06-04 after the 2026-05-27
-- mass-backfill produced silent duplicate UP rows on (17, 64) and
-- (33, 64). See scripts/migrations/add_uq_userproject_user_project.sql
-- for the dedup migration that paired with this constraint.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_UserProject_UserId_ProjectId'
      AND object_id = OBJECT_ID('dbo.UserProject')
)
BEGIN
    CREATE UNIQUE INDEX UQ_UserProject_UserId_ProjectId
        ON dbo.UserProject(UserId, ProjectId);
END
GO

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

-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserProject_User')
BEGIN
    ALTER TABLE [dbo].[UserProject] ADD CONSTRAINT [FK_UserProject_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserProject_Project')
BEGIN
    ALTER TABLE [dbo].[UserProject] ADD CONSTRAINT [FK_UserProject_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO
