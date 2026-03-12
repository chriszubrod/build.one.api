IF OBJECT_ID('dbo.Contact', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Contact]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Email] NVARCHAR(255) NULL,
    [OfficePhone] NVARCHAR(50) NULL,
    [MobilePhone] NVARCHAR(50) NULL,
    [Fax] NVARCHAR(50) NULL,
    [Notes] NVARCHAR(MAX) NULL,
    [UserId] BIGINT NULL,
    [CompanyId] BIGINT NULL,
    [CustomerId] BIGINT NULL,
    [ProjectId] BIGINT NULL,
    [VendorId] BIGINT NULL
);
END
GO


GO


GO

CREATE OR ALTER PROCEDURE CreateContact
(
    @Email NVARCHAR(255),
    @OfficePhone NVARCHAR(50),
    @MobilePhone NVARCHAR(50),
    @Fax NVARCHAR(50),
    @Notes NVARCHAR(MAX),
    @UserId BIGINT,
    @CompanyId BIGINT,
    @CustomerId BIGINT,
    @ProjectId BIGINT,
    @VendorId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Contact] ([CreatedDatetime], [ModifiedDatetime], [Email], [OfficePhone], [MobilePhone], [Fax], [Notes], [UserId], [CompanyId], [CustomerId], [ProjectId], [VendorId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Email],
        INSERTED.[OfficePhone],
        INSERTED.[MobilePhone],
        INSERTED.[Fax],
        INSERTED.[Notes],
        INSERTED.[UserId],
        INSERTED.[CompanyId],
        INSERTED.[CustomerId],
        INSERTED.[ProjectId],
        INSERTED.[VendorId]
    VALUES (@Now, @Now, @Email, @OfficePhone, @MobilePhone, @Fax, @Notes, @UserId, @CompanyId, @CustomerId, @ProjectId, @VendorId);

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadContacts
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Email],
        [OfficePhone],
        [MobilePhone],
        [Fax],
        [Notes],
        [UserId],
        [CompanyId],
        [CustomerId],
        [ProjectId],
        [VendorId]
    FROM dbo.[Contact]
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadContactById
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
        [Email],
        [OfficePhone],
        [MobilePhone],
        [Fax],
        [Notes],
        [UserId],
        [CompanyId],
        [CustomerId],
        [ProjectId],
        [VendorId]
    FROM dbo.[Contact]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadContactByPublicId
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
        [Email],
        [OfficePhone],
        [MobilePhone],
        [Fax],
        [Notes],
        [UserId],
        [CompanyId],
        [CustomerId],
        [ProjectId],
        [VendorId]
    FROM dbo.[Contact]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadContactsByUserId
(
    @UserId BIGINT
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
        [Email],
        [OfficePhone],
        [MobilePhone],
        [Fax],
        [Notes],
        [UserId],
        [CompanyId],
        [CustomerId],
        [ProjectId],
        [VendorId]
    FROM dbo.[Contact]
    WHERE [UserId] = @UserId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadContactsByCompanyId
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
        [Email],
        [OfficePhone],
        [MobilePhone],
        [Fax],
        [Notes],
        [UserId],
        [CompanyId],
        [CustomerId],
        [ProjectId],
        [VendorId]
    FROM dbo.[Contact]
    WHERE [CompanyId] = @CompanyId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadContactsByCustomerId
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
        [Email],
        [OfficePhone],
        [MobilePhone],
        [Fax],
        [Notes],
        [UserId],
        [CompanyId],
        [CustomerId],
        [ProjectId],
        [VendorId]
    FROM dbo.[Contact]
    WHERE [CustomerId] = @CustomerId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadContactsByProjectId
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
        [Email],
        [OfficePhone],
        [MobilePhone],
        [Fax],
        [Notes],
        [UserId],
        [CompanyId],
        [CustomerId],
        [ProjectId],
        [VendorId]
    FROM dbo.[Contact]
    WHERE [ProjectId] = @ProjectId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadContactsByVendorId
(
    @VendorId BIGINT
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
        [Email],
        [OfficePhone],
        [MobilePhone],
        [Fax],
        [Notes],
        [UserId],
        [CompanyId],
        [CustomerId],
        [ProjectId],
        [VendorId]
    FROM dbo.[Contact]
    WHERE [VendorId] = @VendorId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdateContactById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Email NVARCHAR(255),
    @OfficePhone NVARCHAR(50),
    @MobilePhone NVARCHAR(50),
    @Fax NVARCHAR(50),
    @Notes NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Contact]
    SET
        [ModifiedDatetime] = @Now,
        [Email] = @Email,
        [OfficePhone] = @OfficePhone,
        [MobilePhone] = @MobilePhone,
        [Fax] = @Fax,
        [Notes] = @Notes
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Email],
        INSERTED.[OfficePhone],
        INSERTED.[MobilePhone],
        INSERTED.[Fax],
        INSERTED.[Notes],
        INSERTED.[UserId],
        INSERTED.[CompanyId],
        INSERTED.[CustomerId],
        INSERTED.[ProjectId],
        INSERTED.[VendorId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeleteContactById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Contact]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Email],
        DELETED.[OfficePhone],
        DELETED.[MobilePhone],
        DELETED.[Fax],
        DELETED.[Notes],
        DELETED.[UserId],
        DELETED.[CompanyId],
        DELETED.[CustomerId],
        DELETED.[ProjectId],
        DELETED.[VendorId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
