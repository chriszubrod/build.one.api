DROP TABLE IF EXISTS dbo.[ProjectAddress];
GO

CREATE TABLE [dbo].[ProjectAddress]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ProjectId] BIGINT NULL,
    [AddressId] BIGINT NULL,
    [AddressTypeId] BIGINT NULL,
    CONSTRAINT [FK_ProjectAddress_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]),
    CONSTRAINT [FK_ProjectAddress_Address] FOREIGN KEY ([AddressId]) REFERENCES [dbo].[Address]([Id]),
    CONSTRAINT [FK_ProjectAddress_AddressType] FOREIGN KEY ([AddressTypeId]) REFERENCES [dbo].[AddressType]([Id])
);
GO




DROP PROCEDURE IF EXISTS CreateProjectAddress;
GO

CREATE PROCEDURE CreateProjectAddress
(
    @ProjectId BIGINT,
    @AddressId BIGINT,
    @AddressTypeId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ProjectAddress] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [AddressId], [AddressTypeId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[AddressId],
        INSERTED.[AddressTypeId]
    VALUES (@Now, @Now, @ProjectId, @AddressId, @AddressTypeId);

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS ReadProjectAddresses;
GO

CREATE PROCEDURE ReadProjectAddresses
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    ORDER BY [ProjectId] ASC, [AddressId] ASC, [AddressTypeId] ASC;

    COMMIT TRANSACTION;
END;



DROP PROCEDURE IF EXISTS ReadProjectAddressById;
GO

CREATE PROCEDURE ReadProjectAddressById
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS ReadProjectAddressByPublicId;
GO

CREATE PROCEDURE ReadProjectAddressByPublicId
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS ReadProjectAddressByProjectId;
GO

CREATE PROCEDURE ReadProjectAddressByProjectId
(
    @ProjectId BIGINT
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS ReadProjectAddressByAddressId;
GO

CREATE PROCEDURE ReadProjectAddressByAddressId
(
    @AddressId BIGINT
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [AddressId] = @AddressId;

    COMMIT TRANSACTION;
END;



DROP PROCEDURE IF EXISTS ReadProjectAddressByAddressTypeId;
GO

CREATE PROCEDURE ReadProjectAddressByAddressTypeId
(
    @AddressTypeId BIGINT
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [AddressTypeId] = @AddressTypeId;

    COMMIT TRANSACTION;
END;







DROP PROCEDURE IF EXISTS UpdateProjectAddressById;
GO

CREATE PROCEDURE UpdateProjectAddressById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ProjectId BIGINT,
    @AddressId BIGINT,
    @AddressTypeId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ProjectAddress]
    SET
        [ModifiedDatetime] = @Now,
        [ProjectId] = @ProjectId,
        [AddressId] = @AddressId,
        [AddressTypeId] = @AddressTypeId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[AddressId],
        INSERTED.[AddressTypeId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS DeleteProjectAddressById;
GO

CREATE PROCEDURE DeleteProjectAddressById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ProjectAddress]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[AddressId],
        DELETED.[AddressTypeId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS DeleteProjectAddressByProjectId;
GO

CREATE PROCEDURE DeleteProjectAddressByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ProjectAddress]
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;

SELECT * FROM dbo.ProjectAddress;