GO

IF OBJECT_ID('qbo.Term', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[Term]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(50) NULL,
    [SyncToken] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [Name] NVARCHAR(31) NULL,
    [DiscountPercent] DECIMAL(5,2) NULL,
    [DiscountDays] INT NULL,
    [Active] BIT NULL,
    [Type] NVARCHAR(20) NULL,
    [DayOfMonthDue] INT NULL,
    [DiscountDayOfMonth] INT NULL,
    [DueNextMonthDays] INT NULL,
    [DueDays] INT NULL
);
END
GO

IF OBJECT_ID('qbo.Term', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboTerm_QboId' AND object_id = OBJECT_ID('qbo.Term'))
BEGIN
CREATE INDEX IX_QboTerm_QboId ON [qbo].[Term] ([QboId]);
END
GO

IF OBJECT_ID('qbo.Term', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboTerm_RealmId' AND object_id = OBJECT_ID('qbo.Term'))
BEGIN
CREATE INDEX IX_QboTerm_RealmId ON [qbo].[Term] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.Term', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboTerm_Name' AND object_id = OBJECT_ID('qbo.Term'))
BEGIN
CREATE INDEX IX_QboTerm_Name ON [qbo].[Term] ([Name]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateQboTerm
(
    @QboId NVARCHAR(50),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @Name NVARCHAR(31),
    @DiscountPercent DECIMAL(5,2),
    @DiscountDays INT,
    @Active BIT,
    @Type NVARCHAR(20),
    @DayOfMonthDue INT,
    @DiscountDayOfMonth INT,
    @DueNextMonthDays INT,
    @DueDays INT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Term] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId],
        [Name], [DiscountPercent], [DiscountDays], [Active], [Type],
        [DayOfMonthDue], [DiscountDayOfMonth], [DueNextMonthDays], [DueDays]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[Name],
        INSERTED.[DiscountPercent],
        INSERTED.[DiscountDays],
        INSERTED.[Active],
        INSERTED.[Type],
        INSERTED.[DayOfMonthDue],
        INSERTED.[DiscountDayOfMonth],
        INSERTED.[DueNextMonthDays],
        INSERTED.[DueDays]
    VALUES (
        @Now, @Now, @QboId, @SyncToken, @RealmId,
        @Name, @DiscountPercent, @DiscountDays, @Active, @Type,
        @DayOfMonthDue, @DiscountDayOfMonth, @DueNextMonthDays, @DueDays
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboTerms
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [QboId],
        [SyncToken],
        [RealmId],
        [Name],
        [DiscountPercent],
        [DiscountDays],
        [Active],
        [Type],
        [DayOfMonthDue],
        [DiscountDayOfMonth],
        [DueNextMonthDays],
        [DueDays]
    FROM [qbo].[Term]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboTermsByRealmId
(
    @RealmId NVARCHAR(50)
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
        [QboId],
        [SyncToken],
        [RealmId],
        [Name],
        [DiscountPercent],
        [DiscountDays],
        [Active],
        [Type],
        [DayOfMonthDue],
        [DiscountDayOfMonth],
        [DueNextMonthDays],
        [DueDays]
    FROM [qbo].[Term]
    WHERE [RealmId] = @RealmId
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboTermById
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
        [QboId],
        [SyncToken],
        [RealmId],
        [Name],
        [DiscountPercent],
        [DiscountDays],
        [Active],
        [Type],
        [DayOfMonthDue],
        [DiscountDayOfMonth],
        [DueNextMonthDays],
        [DueDays]
    FROM [qbo].[Term]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboTermByQboId
(
    @QboId NVARCHAR(50)
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
        [QboId],
        [SyncToken],
        [RealmId],
        [Name],
        [DiscountPercent],
        [DiscountDays],
        [Active],
        [Type],
        [DayOfMonthDue],
        [DiscountDayOfMonth],
        [DueNextMonthDays],
        [DueDays]
    FROM [qbo].[Term]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboTermByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50)
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
        [QboId],
        [SyncToken],
        [RealmId],
        [Name],
        [DiscountPercent],
        [DiscountDays],
        [Active],
        [Type],
        [DayOfMonthDue],
        [DiscountDayOfMonth],
        [DueNextMonthDays],
        [DueDays]
    FROM [qbo].[Term]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboTermByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @Name NVARCHAR(31),
    @DiscountPercent DECIMAL(5,2),
    @DiscountDays INT,
    @Active BIT,
    @Type NVARCHAR(20),
    @DayOfMonthDue INT,
    @DiscountDayOfMonth INT,
    @DueNextMonthDays INT,
    @DueDays INT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Term]
    SET
        [ModifiedDatetime] = @Now,
        [SyncToken] = @SyncToken,
        [RealmId] = @RealmId,
        [Name] = @Name,
        [DiscountPercent] = @DiscountPercent,
        [DiscountDays] = @DiscountDays,
        [Active] = @Active,
        [Type] = @Type,
        [DayOfMonthDue] = @DayOfMonthDue,
        [DiscountDayOfMonth] = @DiscountDayOfMonth,
        [DueNextMonthDays] = @DueNextMonthDays,
        [DueDays] = @DueDays
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[Name],
        INSERTED.[DiscountPercent],
        INSERTED.[DiscountDays],
        INSERTED.[Active],
        INSERTED.[Type],
        INSERTED.[DayOfMonthDue],
        INSERTED.[DiscountDayOfMonth],
        INSERTED.[DueNextMonthDays],
        INSERTED.[DueDays]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteQboTermByQboId
(
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Term]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[SyncToken],
        DELETED.[RealmId],
        DELETED.[Name],
        DELETED.[DiscountPercent],
        DELETED.[DiscountDays],
        DELETED.[Active],
        DELETED.[Type],
        DELETED.[DayOfMonthDue],
        DELETED.[DiscountDayOfMonth],
        DELETED.[DueNextMonthDays],
        DELETED.[DueDays]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


