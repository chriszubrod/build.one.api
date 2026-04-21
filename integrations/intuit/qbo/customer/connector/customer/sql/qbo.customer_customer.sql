GO

IF OBJECT_ID('qbo.CustomerCustomer', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[CustomerCustomer]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CustomerId] BIGINT NOT NULL,
    [QboCustomerId] BIGINT NOT NULL,
    CONSTRAINT [UQ_CustomerCustomer_CustomerId] UNIQUE ([CustomerId]),
    CONSTRAINT [UQ_CustomerCustomer_QboCustomerId] UNIQUE ([QboCustomerId])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateCustomerCustomer
(
    @CustomerId BIGINT,
    @QboCustomerId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[CustomerCustomer] ([CreatedDatetime], [ModifiedDatetime], [CustomerId], [QboCustomerId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CustomerId],
        INSERTED.[QboCustomerId]
    VALUES (@Now, @Now, @CustomerId, @QboCustomerId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadCustomerCustomerById
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
        [CustomerId],
        [QboCustomerId]
    FROM [qbo].[CustomerCustomer]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadCustomerCustomerByCustomerId
(
    @CustomerId BIGINT
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
        [CustomerId],
        [QboCustomerId]
    FROM [qbo].[CustomerCustomer]
    WHERE [CustomerId] = @CustomerId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadCustomerCustomerByQboCustomerId
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
        [CustomerId],
        [QboCustomerId]
    FROM [qbo].[CustomerCustomer]
    WHERE [QboCustomerId] = @QboCustomerId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateCustomerCustomerById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @CustomerId BIGINT,
    @QboCustomerId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[CustomerCustomer]
    SET
        [ModifiedDatetime] = @Now,
        [CustomerId] = CASE WHEN @CustomerId IS NULL THEN [CustomerId] ELSE @CustomerId END,
        [QboCustomerId] = CASE WHEN @QboCustomerId IS NULL THEN [QboCustomerId] ELSE @QboCustomerId END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CustomerId],
        INSERTED.[QboCustomerId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteCustomerCustomerById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[CustomerCustomer]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[CustomerId],
        DELETED.[QboCustomerId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

