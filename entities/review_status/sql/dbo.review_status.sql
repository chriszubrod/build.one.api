GO

IF OBJECT_ID('dbo.ReviewStatus', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ReviewStatus]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(100) NOT NULL,
    [Description] NVARCHAR(500) NULL,
    [SortOrder] INT NOT NULL DEFAULT 0,
    [IsFinal] BIT NOT NULL DEFAULT 0,
    [IsDeclined] BIT NOT NULL DEFAULT 0,
    [IsActive] BIT NOT NULL DEFAULT 1,
    [Color] NVARCHAR(7) NULL
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateReviewStatus
(
    @Name NVARCHAR(100),
    @Description NVARCHAR(500) = NULL,
    @SortOrder INT = 0,
    @IsFinal BIT = 0,
    @IsDeclined BIT = 0,
    @IsActive BIT = 1,
    @Color NVARCHAR(7) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [Color])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[SortOrder],
        INSERTED.[IsFinal],
        INSERTED.[IsDeclined],
        INSERTED.[IsActive],
        INSERTED.[Color]
    VALUES (@Now, @Now, @Name, @Description, @SortOrder, @IsFinal, @IsDeclined, @IsActive, @Color);

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewStatuses
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
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [Color]
    FROM dbo.[ReviewStatus]
    ORDER BY [SortOrder] ASC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewStatusById
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
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [Color]
    FROM dbo.[ReviewStatus]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewStatusByPublicId
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
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [Color]
    FROM dbo.[ReviewStatus]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadNextReviewStatus
(
    @CurrentSortOrder INT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [Color]
    FROM dbo.[ReviewStatus]
    WHERE [SortOrder] > @CurrentSortOrder
      AND [IsDeclined] = 0
      AND [IsActive] = 1
    ORDER BY [SortOrder] ASC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadFirstReviewStatus
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Description],
        [SortOrder],
        [IsFinal],
        [IsDeclined],
        [IsActive],
        [Color]
    FROM dbo.[ReviewStatus]
    WHERE [IsDeclined] = 0
      AND [IsActive] = 1
    ORDER BY [SortOrder] ASC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE UpdateReviewStatusById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(100),
    @Description NVARCHAR(500) = NULL,
    @SortOrder INT = 0,
    @IsFinal BIT = 0,
    @IsDeclined BIT = 0,
    @IsActive BIT = 1,
    @Color NVARCHAR(7) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ReviewStatus]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Description] = @Description,
        [SortOrder] = @SortOrder,
        [IsFinal] = @IsFinal,
        [IsDeclined] = @IsDeclined,
        [IsActive] = @IsActive,
        [Color] = @Color
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[SortOrder],
        INSERTED.[IsFinal],
        INSERTED.[IsDeclined],
        INSERTED.[IsActive],
        INSERTED.[Color]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE DeleteReviewStatusById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ReviewStatus]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Description],
        DELETED.[SortOrder],
        DELETED.[IsFinal],
        DELETED.[IsDeclined],
        DELETED.[IsActive],
        DELETED.[Color]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
