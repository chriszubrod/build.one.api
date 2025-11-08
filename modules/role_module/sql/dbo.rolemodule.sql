CREATE TABLE [dbo].[RoleModule]
(
    [Id] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [RoleId] UNIQUEIDENTIFIER NOT NULL,
    [ModuleId] UNIQUEIDENTIFIER NOT NULL
);
GO



DROP PROCEDURE IF EXISTS CreateRoleModule;
GO

CREATE PROCEDURE CreateRoleModule
(
    @RoleId UNIQUEIDENTIFIER,
    @ModuleId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[RoleModule] ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[RoleId],
        INSERTED.[ModuleId]
    VALUES (@Now, @Now, @RoleId, @ModuleId);

    COMMIT TRANSACTION;
END;

EXEC CreateRoleModule
    @RoleId = '00000000-0000-0000-0000-000000000000',
    @ModuleId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadRoleModules;
GO

CREATE PROCEDURE ReadRoleModules
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId],
        [ModuleId]
    FROM dbo.[RoleModule]
    ORDER BY [RoleId] ASC, [ModuleId] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadRoleModules;
GO


DROP PROCEDURE IF EXISTS ReadRoleModuleById;
GO

CREATE PROCEDURE ReadRoleModuleById
(
    @Id UNIQUEIDENTIFIER
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
        [RoleId],
        [ModuleId]
    FROM dbo.[RoleModule]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadRoleModuleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadRoleModuleByPublicId;
GO

CREATE PROCEDURE ReadRoleModuleByPublicId
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
        [RoleId],
        [ModuleId]
    FROM dbo.[RoleModule]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadRoleModuleByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadRoleModuleByRoleId;
GO

CREATE PROCEDURE ReadRoleModuleByRoleId
(
    @RoleId UNIQUEIDENTIFIER
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
        [RoleId],
        [ModuleId]
    FROM dbo.[RoleModule]
    WHERE [RoleId] = @RoleId;

    COMMIT TRANSACTION;
END;

EXEC ReadRoleModuleByRoleId
    @RoleId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadRoleModuleByModuleId;
GO

CREATE PROCEDURE ReadRoleModuleByModuleId
(
    @ModuleId UNIQUEIDENTIFIER
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
        [RoleId],
        [ModuleId]
    FROM dbo.[RoleModule]
    WHERE [ModuleId] = @ModuleId;

    COMMIT TRANSACTION;
END;

EXEC ReadRoleModuleByModuleId
    @ModuleId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS UpdateRoleModuleById;
GO

CREATE PROCEDURE UpdateRoleModuleById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @RoleId UNIQUEIDENTIFIER,
    @ModuleId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[RoleModule]
    SET
        [ModifiedDatetime] = @Now,
        [RoleId] = @RoleId,
        [ModuleId] = @ModuleId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[RoleId],
        INSERTED.[ModuleId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateRoleModuleById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = 0x0000000000000000,
    @RoleId = '00000000-0000-0000-0000-000000000000',
    @ModuleId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS DeleteRoleModuleById;
GO

CREATE PROCEDURE DeleteRoleModuleById
(
    @Id UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[RoleModule]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[RoleId],
        DELETED.[ModuleId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteRoleModuleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO
