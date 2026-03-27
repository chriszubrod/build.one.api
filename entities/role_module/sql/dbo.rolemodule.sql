IF OBJECT_ID('dbo.RoleModule', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[RoleModule]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [RoleId] BIGINT NOT NULL,
    [ModuleId] BIGINT NOT NULL,
    [CanCreate] BIT NOT NULL DEFAULT 0,
    [CanRead] BIT NOT NULL DEFAULT 0,
    [CanUpdate] BIT NOT NULL DEFAULT 0,
    [CanDelete] BIT NOT NULL DEFAULT 0,
    [CanSubmit] BIT NOT NULL DEFAULT 0,
    [CanApprove] BIT NOT NULL DEFAULT 0,
    [CanComplete] BIT NOT NULL DEFAULT 0
);
END
GO


GO


GO

CREATE OR ALTER PROCEDURE CreateRoleModule
(
    @RoleId BIGINT,
    @ModuleId BIGINT,
    @CanCreate BIT = 0,
    @CanRead BIT = 0,
    @CanUpdate BIT = 0,
    @CanDelete BIT = 0,
    @CanSubmit BIT = 0,
    @CanApprove BIT = 0,
    @CanComplete BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[RoleModule] ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId], [CanCreate], [CanRead], [CanUpdate], [CanDelete], [CanSubmit], [CanApprove], [CanComplete])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[RoleId],
        INSERTED.[ModuleId],
        INSERTED.[CanCreate],
        INSERTED.[CanRead],
        INSERTED.[CanUpdate],
        INSERTED.[CanDelete],
        INSERTED.[CanSubmit],
        INSERTED.[CanApprove],
        INSERTED.[CanComplete]
    VALUES (@Now, @Now, @RoleId, @ModuleId, @CanCreate, @CanRead, @CanUpdate, @CanDelete, @CanSubmit, @CanApprove, @CanComplete);

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadRoleModules
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
        [ModuleId],
        [CanCreate],
        [CanRead],
        [CanUpdate],
        [CanDelete],
        [CanSubmit],
        [CanApprove],
        [CanComplete]
    FROM dbo.[RoleModule]
    ORDER BY [RoleId] ASC, [ModuleId] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadRoleModuleById
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
        [RoleId],
        [ModuleId],
        [CanCreate],
        [CanRead],
        [CanUpdate],
        [CanDelete],
        [CanSubmit],
        [CanApprove],
        [CanComplete]
    FROM dbo.[RoleModule]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadRoleModuleByPublicId
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
        [ModuleId],
        [CanCreate],
        [CanRead],
        [CanUpdate],
        [CanDelete],
        [CanSubmit],
        [CanApprove],
        [CanComplete]
    FROM dbo.[RoleModule]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadRoleModuleByRoleId
(
    @RoleId BIGINT
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
        [ModuleId],
        [CanCreate],
        [CanRead],
        [CanUpdate],
        [CanDelete],
        [CanSubmit],
        [CanApprove],
        [CanComplete]
    FROM dbo.[RoleModule]
    WHERE [RoleId] = @RoleId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadRoleModuleByModuleId
(
    @ModuleId BIGINT
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
        [ModuleId],
        [CanCreate],
        [CanRead],
        [CanUpdate],
        [CanDelete],
        [CanSubmit],
        [CanApprove],
        [CanComplete]
    FROM dbo.[RoleModule]
    WHERE [ModuleId] = @ModuleId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdateRoleModuleById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @RoleId BIGINT,
    @ModuleId BIGINT,
    @CanCreate BIT = 0,
    @CanRead BIT = 0,
    @CanUpdate BIT = 0,
    @CanDelete BIT = 0,
    @CanSubmit BIT = 0,
    @CanApprove BIT = 0,
    @CanComplete BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[RoleModule]
    SET
        [ModifiedDatetime] = @Now,
        [RoleId] = @RoleId,
        [ModuleId] = @ModuleId,
        [CanCreate] = @CanCreate,
        [CanRead] = @CanRead,
        [CanUpdate] = @CanUpdate,
        [CanDelete] = @CanDelete,
        [CanSubmit] = @CanSubmit,
        [CanApprove] = @CanApprove,
        [CanComplete] = @CanComplete
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[RoleId],
        INSERTED.[ModuleId],
        INSERTED.[CanCreate],
        INSERTED.[CanRead],
        INSERTED.[CanUpdate],
        INSERTED.[CanDelete],
        INSERTED.[CanSubmit],
        INSERTED.[CanApprove],
        INSERTED.[CanComplete]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeleteRoleModuleById
(
    @Id BIGINT
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
        DELETED.[ModuleId],
        DELETED.[CanCreate],
        DELETED.[CanRead],
        DELETED.[CanUpdate],
        DELETED.[CanDelete],
        DELETED.[CanSubmit],
        DELETED.[CanApprove],
        DELETED.[CanComplete]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
