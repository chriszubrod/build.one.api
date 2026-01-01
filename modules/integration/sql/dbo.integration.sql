CREATE TABLE [dbo].[Integration]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Status] NVARCHAR(50) NULL,
    [Endpoint] NVARCHAR(MAX) NULL
);
GO

DROP TABLE IF EXISTS dbo.[Integration];
GO


DROP PROCEDURE IF EXISTS CreateIntegration;
GO

CREATE PROCEDURE CreateIntegration
(
    @Name NVARCHAR(50),
    @Status NVARCHAR(50),
    @Endpoint NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Integration] ([CreatedDatetime], [ModifiedDatetime], [Name], [Status], [Endpoint])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Status],
        INSERTED.[Endpoint]
    VALUES (@Now, @Now, @Name, @Status, @Endpoint);

    COMMIT TRANSACTION;
END;

EXEC CreateIntegration
    @Name = 'QuickBooks Online',
    @Status = 'connected',
    @Endpoint = 'https://www.quickbooks.com';
GO


DROP PROCEDURE IF EXISTS ReadIntegrations;
GO

CREATE PROCEDURE ReadIntegrations
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
        [Status],
        [Endpoint]
    FROM dbo.[Integration]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadIntegrations;
GO


DROP PROCEDURE IF EXISTS ReadIntegrationById;
GO

CREATE PROCEDURE ReadIntegrationById
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
        [Name],
        [Status],
        [Endpoint]
    FROM dbo.[Integration]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadIntegrationById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadIntegrationByPublicId;
GO

CREATE PROCEDURE ReadIntegrationByPublicId
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
        [Name],
        [Status],
        [Endpoint]
    FROM dbo.[Integration]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadIntegrationByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadIntegrationByName;
GO

CREATE PROCEDURE ReadIntegrationByName
(
    @Name NVARCHAR(50)
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
        [Status],
        [Endpoint]
    FROM dbo.[Integration]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;

EXEC ReadIntegrationByName
    @Name = 'QuickBooks Online';
GO


DROP PROCEDURE IF EXISTS UpdateIntegrationById;
GO

CREATE PROCEDURE UpdateIntegrationById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Status NVARCHAR(50),
    @Endpoint NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Integration]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Status] = @Status,
        [Endpoint] = @Endpoint
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Status],
        INSERTED.[Endpoint]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateIntegrationById
    @Id = 1,
    @RowVersion = 0x0000000000021B43,
    @Name = 'QuickBooks Online',
    @Status = 'disconnected',
    @Endpoint = '/api/v1/intuit/qbo/auth/request';
GO


DROP PROCEDURE IF EXISTS DeleteIntegrationById;
GO

CREATE PROCEDURE DeleteIntegrationById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Integration]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Status],
        DELETED.[Endpoint]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteIntegrationById
    @Id = 2;
GO
