GO

IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[PaymentTerm]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Description] NVARCHAR(255) NULL,
    [DiscountPercent] DECIMAL(5,2) NULL,
    [DiscountDays] INT NULL,
    [DueDays] INT NULL
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreatePaymentTerm
(
    @Name NVARCHAR(50),
    @Description NVARCHAR(255),
    @DiscountPercent DECIMAL(5,2) NULL,
    @DiscountDays INT NULL,
    @DueDays INT NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[PaymentTerm] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [DiscountPercent], [DiscountDays], [DueDays])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[DiscountPercent],
        INSERTED.[DiscountDays],
        INSERTED.[DueDays]
    VALUES (@Now, @Now, @Name, @Description, @DiscountPercent, @DiscountDays, @DueDays);

    COMMIT TRANSACTION;
END;




GO

CREATE OR ALTER PROCEDURE ReadPaymentTerms
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        pt.[Id],
        pt.[PublicId],
        pt.[RowVersion],
        CONVERT(VARCHAR(19), pt.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pt.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pt.[Name],
        pt.[Description],
        pt.[DiscountPercent],
        pt.[DiscountDays],
        pt.[DueDays],
        qt.[Active] AS [QboActive]
    FROM dbo.[PaymentTerm] pt
    LEFT JOIN qbo.[Term] qt ON qt.[QboId] = pt.[QboId] AND qt.[RealmId] = pt.[RealmId]
    ORDER BY pt.[Name] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadPaymentTermById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        pt.[Id],
        pt.[PublicId],
        pt.[RowVersion],
        CONVERT(VARCHAR(19), pt.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pt.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pt.[Name],
        pt.[Description],
        pt.[DiscountPercent],
        pt.[DiscountDays],
        pt.[DueDays],
        qt.[Active] AS [QboActive]
    FROM dbo.[PaymentTerm] pt
    LEFT JOIN qbo.[Term] qt ON qt.[QboId] = pt.[QboId] AND qt.[RealmId] = pt.[RealmId]
    WHERE pt.[Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadPaymentTermByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        pt.[Id],
        pt.[PublicId],
        pt.[RowVersion],
        CONVERT(VARCHAR(19), pt.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pt.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pt.[Name],
        pt.[Description],
        pt.[DiscountPercent],
        pt.[DiscountDays],
        pt.[DueDays],
        qt.[Active] AS [QboActive]
    FROM dbo.[PaymentTerm] pt
    LEFT JOIN qbo.[Term] qt ON qt.[QboId] = pt.[QboId] AND qt.[RealmId] = pt.[RealmId]
    WHERE pt.[PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadPaymentTermByName
(
    @Name NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        pt.[Id],
        pt.[PublicId],
        pt.[RowVersion],
        CONVERT(VARCHAR(19), pt.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pt.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pt.[Name],
        pt.[Description],
        pt.[DiscountPercent],
        pt.[DiscountDays],
        pt.[DueDays],
        qt.[Active] AS [QboActive]
    FROM dbo.[PaymentTerm] pt
    LEFT JOIN qbo.[Term] qt ON qt.[QboId] = pt.[QboId] AND qt.[RealmId] = pt.[RealmId]
    WHERE pt.[Name] = @Name;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdatePaymentTermById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Description NVARCHAR(255),
    @DiscountPercent DECIMAL(5,2) NULL,
    @DiscountDays INT NULL,
    @DueDays INT NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[PaymentTerm]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Description] = @Description,
        [DiscountPercent] = @DiscountPercent,
        [DiscountDays] = @DiscountDays,
        [DueDays] = @DueDays
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[DiscountPercent],
        INSERTED.[DiscountDays],
        INSERTED.[DueDays]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeletePaymentTermById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[PaymentTerm]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Description],
        DELETED.[DiscountPercent],
        DELETED.[DiscountDays],
        DELETED.[DueDays]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

-- PublicId index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_PaymentTerm_PublicId' AND object_id = OBJECT_ID('dbo.PaymentTerm'))
BEGIN
    CREATE INDEX [IX_PaymentTerm_PublicId] ON [dbo].[PaymentTerm] ([PublicId]);
END
GO

CREATE OR ALTER PROCEDURE SetPaymentTermQboIdentity
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
        UPDATE dbo.[PaymentTerm]
        SET [QboId] = NULL, [RealmId] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[PaymentTerm]
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
