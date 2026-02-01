IF OBJECT_ID('dbo.Sync', 'U') IS NULL
BEGIN
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
END
GO

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Sync' AND COLUMN_NAME = 'LastSyncDatetime'
)
BEGIN
    ALTER TABLE dbo.Sync
    ADD LastSyncDatetime DATETIME2(3) NULL;
END
GO


GO

CREATE OR ALTER PROCEDURE CreateSync
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



GO

CREATE OR ALTER PROCEDURE ReadSyncs
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



GO

CREATE OR ALTER PROCEDURE ReadSyncById
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



GO

CREATE OR ALTER PROCEDURE ReadSyncByPublicId
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



GO

CREATE OR ALTER PROCEDURE ReadSyncByProvider
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



GO

CREATE OR ALTER PROCEDURE UpdateSyncById
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



GO

CREATE OR ALTER PROCEDURE DeleteSyncById
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


UPDATE dbo.[Sync]
SET [LastSyncDatetime] = '2026-01-01 00:00:00.000'
WHERE [Id] = 14 AND [RowVersion] = 0x000000000004A864;
