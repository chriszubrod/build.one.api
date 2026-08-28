GO



IF OBJECT_ID('dbo.Customer', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Customer]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Email] NVARCHAR(255) NULL,
    [Phone] NVARCHAR(50) NULL
);
END
GO

-- Idempotent column add for existing environments. Live since migration
-- 238c_qbo_identity_reference.sql (2026-06); declared here so a fresh
-- environment built from just the base file matches prod. Pull-only
-- entity — no SyncToken (no live Customer push path).
IF OBJECT_ID('dbo.Customer', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Customer') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Customer] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Customer', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Customer') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Customer] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Customer', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Customer_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Customer')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Customer_QboId_RealmId ON [dbo].[Customer] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

CREATE OR ALTER PROCEDURE CreateCustomer
(
    @Name NVARCHAR(50),
    @Email NVARCHAR(255),
    @Phone NVARCHAR(50),
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Customer] ([CreatedDatetime], [ModifiedDatetime], [Name], [Email], [Phone], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Email],
        INSERTED.[Phone]
    VALUES (@Now, @Now, @Name, @Email, @Phone, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadCustomers
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
        [Email],
        [Phone]
    FROM dbo.[Customer]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadCustomerById
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
        [Email],
        [Phone],
        [QboId],
        [RealmId]
    FROM dbo.[Customer]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadCustomerByPublicId
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
        [Email],
        [Phone]
    FROM dbo.[Customer]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadCustomerByName
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
        [Email],
        [Phone],
        [QboId],
        [RealmId]
    FROM dbo.[Customer]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdateCustomerById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Email NVARCHAR(255),
    @Phone NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Customer]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Email] = @Email,
        [Phone] = @Phone
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Email],
        INSERTED.[Phone]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeleteCustomerById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Customer]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Email],
        DELETED.[Phone]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

-- PublicId index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Customer_PublicId' AND object_id = OBJECT_ID('dbo.Customer'))
BEGIN
    CREATE INDEX [IX_Customer_PublicId] ON [dbo].[Customer] ([PublicId]);
END
GO

CREATE OR ALTER PROCEDURE SetCustomerQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50) = NULL,
    @RealmId NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    IF @QboId IS NOT NULL
    BEGIN
        UPDATE dbo.[Customer]
        SET [QboId] = NULL, [RealmId] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[Customer]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[QboId],
        INSERTED.[RealmId],
        @Stolen AS [Stolen]
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
      );
END;
GO

-- U-276 (Phase-4 pilot): direct dbo-native identity lookup. Lets a QBO
-- connector resolve "does a dbo.Customer already exist for this external
-- QBO id" WITHOUT hopping through the qbo.Customer / qbo.CustomerCustomer
-- staging/mapping tables — every Customer synced at least once already
-- carries QboId/RealmId via SetCustomerQboIdentity, so this is the
-- steady-state fast path; the mapping-table lookup remains as a fallback
-- for rows that predate identity stamping. RealmId NULL-equality mirrors
-- SetCustomerQboIdentity's own stolen-identity comparison.
CREATE OR ALTER PROCEDURE ReadCustomerByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50) = NULL
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
        [Email],
        [Phone],
        [QboId],
        [RealmId]
    FROM dbo.[Customer]
    WHERE [QboId] = @QboId
      AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

    COMMIT TRANSACTION;
END;
GO
