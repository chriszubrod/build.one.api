GO

IF OBJECT_ID('qbo.CustomerProject', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[CustomerProject]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ProjectId] BIGINT NOT NULL,
    [QboCustomerId] BIGINT NOT NULL,
    CONSTRAINT [UQ_CustomerProject_ProjectId] UNIQUE ([ProjectId]),
    CONSTRAINT [UQ_CustomerProject_QboCustomerId] UNIQUE ([QboCustomerId])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateCustomerProject
(
    @ProjectId BIGINT,
    @QboCustomerId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[CustomerProject] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [QboCustomerId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[QboCustomerId]
    VALUES (@Now, @Now, @ProjectId, @QboCustomerId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadCustomerProjectById
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
        [QboCustomerId]
    FROM [qbo].[CustomerProject]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadCustomerProjectByProjectId
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
        [QboCustomerId]
    FROM [qbo].[CustomerProject]
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadCustomerProjectByQboCustomerId
(
    @QboCustomerId BIGINT
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
        [QboCustomerId]
    FROM [qbo].[CustomerProject]
    WHERE [QboCustomerId] = @QboCustomerId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateCustomerProjectById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ProjectId BIGINT,
    @QboCustomerId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[CustomerProject]
    SET
        [ModifiedDatetime] = @Now,
        [ProjectId] = CASE WHEN @ProjectId IS NULL THEN [ProjectId] ELSE @ProjectId END,
        [QboCustomerId] = CASE WHEN @QboCustomerId IS NULL THEN [QboCustomerId] ELSE @QboCustomerId END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[QboCustomerId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteCustomerProjectById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[CustomerProject]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[QboCustomerId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

