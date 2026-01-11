CREATE TABLE [dbo].[Sync]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Provider] NVARCHAR(50) NOT NULL,
    [Env] NVARCHAR(255) NOT NULL,
    [Entity] NVARCHAR(255) NOT NULL,
    [LastSyncDatetime] DATETIME2(3) NULL
);
GO

ALTER TABLE dbo.[Sync]
ADD [LastSyncDatetime] DATETIME2(3) NULL;
GO


DROP PROCEDURE IF EXISTS CreateSync;
GO

CREATE PROCEDURE CreateSync
(
    @Provider NVARCHAR(50),
    @Env NVARCHAR(255),
    @Entity NVARCHAR(255),
    @LastSyncDatetime DATETIME2(3) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Sync] ([CreatedDatetime], [ModifiedDatetime], [Provider], [Env], [Entity], [LastSyncDatetime])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Provider],
        INSERTED.[Env],
        INSERTED.[Entity],
        CONVERT(VARCHAR(19), INSERTED.[LastSyncDatetime], 120) AS [LastSyncDatetime]
    VALUES (@Now, @Now, @Provider, @Env, @Entity, @LastSyncDatetime);

    COMMIT TRANSACTION;
END;

EXEC CreateSync
    @Provider = 'qbo',
    @Env = 'production',
    @Entity = 'vendor',
    @LastSyncDatetime = NULL;
GO


DROP PROCEDURE IF EXISTS ReadSyncs;
GO

CREATE PROCEDURE ReadSyncs
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Provider],
        [Env],
        [Entity],
        [LastSyncDatetime]
    FROM dbo.[Sync]
    ORDER BY [Provider] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadSyncs;
GO


DROP PROCEDURE IF EXISTS ReadSyncById;
GO

CREATE PROCEDURE ReadSyncById
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
        [Provider],
        [Env],
        [Entity],
        [LastSyncDatetime]
    FROM dbo.[Sync]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadSyncById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadSyncByPublicId;
GO

CREATE PROCEDURE ReadSyncByPublicId
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
        [Provider],
        [Env],
        [Entity],
        [LastSyncDatetime]
    FROM dbo.[Sync]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadSyncByPublicId
    @PublicId = 'c86edd93-a99c-424b-afa3-8df26f7de144';
GO


DROP PROCEDURE IF EXISTS ReadSyncByProvider;
GO

CREATE PROCEDURE ReadSyncByProvider
(
    @Provider NVARCHAR(50)
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
        [Provider],
        [Env],
        [Entity],
        [LastSyncDatetime]
    FROM dbo.[Sync]
    WHERE [Provider] = @Provider;

    COMMIT TRANSACTION;
END;

EXEC ReadSyncByProvider
    @Provider = 'qbo';
GO


DROP PROCEDURE IF EXISTS UpdateSyncById;
GO

CREATE PROCEDURE UpdateSyncById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Provider NVARCHAR(50),
    @Env NVARCHAR(255),
    @Entity NVARCHAR(255),
    @LastSyncDatetime DATETIME2(3) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Sync]
    SET
        [ModifiedDatetime] = @Now,
        [Provider] = @Provider,
        [Env] = @Env,
        [Entity] = @Entity,
        [LastSyncDatetime] = @LastSyncDatetime
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Provider],
        INSERTED.[Env],
        INSERTED.[Entity],
        CONVERT(VARCHAR(19), INSERTED.[LastSyncDatetime], 120) AS [LastSyncDatetime]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateSyncById
    @Id = 5,
    @RowVersion = 0x0000000000020B85,
    @Provider = 'qbo',
    @Env = 'production',
    @Entity = 'vendor',
    @LastSyncDatetime = NULL;
GO


DROP PROCEDURE IF EXISTS DeleteSyncById;
GO

CREATE PROCEDURE DeleteSyncById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Sync]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Provider],
        DELETED.[Env],
        DELETED.[Entity],
        CONVERT(VARCHAR(19), DELETED.[LastSyncDatetime], 120) AS [LastSyncDatetime]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteSyncById
    @Id = 8;
GO

UPDATE dbo.[Sync]
SET [LastSyncDatetime] = NULL
WHERE [Id] = 10 AND [RowVersion] = 0x0000000000023B40;

SELECT * FROM dbo.[Sync];