DROP TABLE IF EXISTS [ms].[DriveCompany];
GO

CREATE TABLE [ms].[DriveCompany]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CompanyId] BIGINT NOT NULL,
    [MsDriveId] BIGINT NOT NULL,
    CONSTRAINT [UQ_DriveCompany_CompanyId] UNIQUE ([CompanyId]),
    CONSTRAINT [UQ_DriveCompany_MsDriveId] UNIQUE ([MsDriveId]),
    CONSTRAINT [FK_DriveCompany_Drive] FOREIGN KEY ([MsDriveId]) REFERENCES [ms].[Drive]([Id]) ON DELETE CASCADE
);
GO


DROP PROCEDURE IF EXISTS CreateDriveCompany;
GO

CREATE PROCEDURE CreateDriveCompany
(
    @CompanyId BIGINT,
    @MsDriveId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[DriveCompany] ([CreatedDatetime], [ModifiedDatetime], [CompanyId], [MsDriveId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CompanyId],
        INSERTED.[MsDriveId]
    VALUES (@Now, @Now, @CompanyId, @MsDriveId);

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveCompanyById;
GO

CREATE PROCEDURE ReadDriveCompanyById
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
        [MsDriveId]
    FROM [ms].[DriveCompany]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveCompanyByCompanyId;
GO

CREATE PROCEDURE ReadDriveCompanyByCompanyId
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
        [MsDriveId]
    FROM [ms].[DriveCompany]
    WHERE [CompanyId] = @CompanyId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveCompanyByMsDriveId;
GO

CREATE PROCEDURE ReadDriveCompanyByMsDriveId
(
    @MsDriveId BIGINT
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
        [MsDriveId]
    FROM [ms].[DriveCompany]
    WHERE [MsDriveId] = @MsDriveId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteDriveCompanyById;
GO

CREATE PROCEDURE DeleteDriveCompanyById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveCompany]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[CompanyId],
        DELETED.[MsDriveId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteDriveCompanyByCompanyId;
GO

CREATE PROCEDURE DeleteDriveCompanyByCompanyId
(
    @CompanyId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveCompany]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[CompanyId],
        DELETED.[MsDriveId]
    WHERE [CompanyId] = @CompanyId;

    COMMIT TRANSACTION;
END;
GO

SELECT * FROM [ms].[DriveCompany];
