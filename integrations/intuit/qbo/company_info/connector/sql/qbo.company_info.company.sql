DROP TABLE IF EXISTS [qbo].[CompanyInfoCompany];
GO

CREATE TABLE [qbo].[CompanyInfoCompany]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CompanyId] BIGINT NOT NULL,
    [QboCompanyInfoId] BIGINT NOT NULL,
    CONSTRAINT [UQ_CompanyInfoCompany_CompanyId] UNIQUE ([CompanyId]),
    CONSTRAINT [UQ_CompanyInfoCompany_QboCompanyInfoId] UNIQUE ([QboCompanyInfoId])
);
GO


DROP PROCEDURE IF EXISTS CreateCompanyInfoCompany;
GO

CREATE PROCEDURE CreateCompanyInfoCompany
(
    @CompanyId BIGINT,
    @QboCompanyInfoId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[CompanyInfoCompany] ([CreatedDatetime], [ModifiedDatetime], [CompanyId], [QboCompanyInfoId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CompanyId],
        INSERTED.[QboCompanyInfoId]
    VALUES (@Now, @Now, @CompanyId, @QboCompanyInfoId);

    COMMIT TRANSACTION;
END;
GO

EXEC CreateCompanyInfoCompany
    @CompanyId = 1,
    @QboCompanyInfoId = 1;
GO


DROP PROCEDURE IF EXISTS ReadCompanyInfoCompanyById;
GO

CREATE PROCEDURE ReadCompanyInfoCompanyById
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
        [CompanyId],
        [QboCompanyInfoId]
    FROM [qbo].[CompanyInfoCompany]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadCompanyInfoCompanyById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadCompanyInfoCompanyByPublicId;
GO

CREATE PROCEDURE ReadCompanyInfoCompanyByPublicId
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
        [CompanyId],
        [QboCompanyInfoId]
    FROM [qbo].[CompanyInfoCompany]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadCompanyInfoCompanyByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadCompanyInfoCompanyByCompanyId;
GO

CREATE PROCEDURE ReadCompanyInfoCompanyByCompanyId
(
    @CompanyId BIGINT
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
        [CompanyId],
        [QboCompanyInfoId]
    FROM [qbo].[CompanyInfoCompany]
    WHERE [CompanyId] = @CompanyId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadCompanyInfoCompanyByCompanyId
    @CompanyId = 1;
GO


DROP PROCEDURE IF EXISTS ReadCompanyInfoCompanyByQboCompanyInfoId;
GO

CREATE PROCEDURE ReadCompanyInfoCompanyByQboCompanyInfoId
(
    @QboCompanyInfoId BIGINT
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
        [CompanyId],
        [QboCompanyInfoId]
    FROM [qbo].[CompanyInfoCompany]
    WHERE [QboCompanyInfoId] = @QboCompanyInfoId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadCompanyInfoCompanyByQboCompanyInfoId
    @QboCompanyInfoId = 1;
GO


DROP PROCEDURE IF EXISTS UpdateCompanyInfoCompanyById;
GO

CREATE PROCEDURE UpdateCompanyInfoCompanyById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @CompanyId BIGINT,
    @QboCompanyInfoId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[CompanyInfoCompany]
    SET
        [ModifiedDatetime] = @Now,
        [CompanyId] = @CompanyId,
        [QboCompanyInfoId] = @QboCompanyInfoId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CompanyId],
        INSERTED.[QboCompanyInfoId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateCompanyInfoCompanyById
    @Id = 1,
    @RowVersion = 0x0000000000021CD7,
    @CompanyId = 1,
    @QboCompanyInfoId = 1;
GO


DROP PROCEDURE IF EXISTS DeleteCompanyInfoCompanyById;
GO

CREATE PROCEDURE DeleteCompanyInfoCompanyById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[CompanyInfoCompany]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[CompanyId],
        DELETED.[QboCompanyInfoId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteCompanyInfoCompanyById
    @Id = 1;
GO

SELECT * FROM [qbo].[CompanyInfoCompany];