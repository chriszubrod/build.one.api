GO

IF OBJECT_ID('qbo.CompanyInfoCompany', 'U') IS NULL
BEGIN
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
END
GO


GO

CREATE OR ALTER PROCEDURE CreateCompanyInfoCompany
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



GO

CREATE OR ALTER PROCEDURE ReadCompanyInfoCompanyById
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



GO

CREATE OR ALTER PROCEDURE ReadCompanyInfoCompanyByPublicId
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



GO

CREATE OR ALTER PROCEDURE ReadCompanyInfoCompanyByCompanyId
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



GO

CREATE OR ALTER PROCEDURE ReadCompanyInfoCompanyByQboCompanyInfoId
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



GO

CREATE OR ALTER PROCEDURE UpdateCompanyInfoCompanyById
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



GO

CREATE OR ALTER PROCEDURE DeleteCompanyInfoCompanyById
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


