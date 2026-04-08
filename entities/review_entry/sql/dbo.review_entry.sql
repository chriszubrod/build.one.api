GO

IF OBJECT_ID('dbo.ReviewEntry', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ReviewEntry]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ReviewStatusId] BIGINT NOT NULL,
    [BillId] BIGINT NULL,
    [UserId] BIGINT NULL,
    [Comments] NVARCHAR(MAX) NULL,
    CONSTRAINT [FK_ReviewEntry_ReviewStatus] FOREIGN KEY ([ReviewStatusId]) REFERENCES [dbo].[ReviewStatus]([Id]),
    CONSTRAINT [FK_ReviewEntry_Bill] FOREIGN KEY ([BillId]) REFERENCES [dbo].[Bill]([Id]),
    CONSTRAINT [FK_ReviewEntry_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id])
);

CREATE INDEX [IX_ReviewEntry_BillId_Created] ON [dbo].[ReviewEntry] ([BillId], [CreatedDatetime] DESC);
CREATE INDEX [IX_ReviewEntry_ReviewStatusId] ON [dbo].[ReviewEntry] ([ReviewStatusId]);
CREATE INDEX [IX_ReviewEntry_UserId] ON [dbo].[ReviewEntry] ([UserId]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateReviewEntry
(
    @ReviewStatusId BIGINT,
    @BillId BIGINT = NULL,
    @UserId BIGINT = NULL,
    @Comments NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ReviewEntry] ([CreatedDatetime], [ModifiedDatetime], [ReviewStatusId], [BillId], [UserId], [Comments])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ReviewStatusId],
        INSERTED.[BillId],
        INSERTED.[UserId],
        INSERTED.[Comments]
    VALUES (@Now, @Now, @ReviewStatusId, @BillId, @UserId, @Comments);

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewEntryById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        re.[Id],
        re.[PublicId],
        re.[RowVersion],
        CONVERT(VARCHAR(19), re.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), re.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        re.[ReviewStatusId],
        re.[BillId],
        re.[UserId],
        re.[Comments],
        rs.[Name] AS [StatusName],
        rs.[SortOrder] AS [StatusSortOrder],
        rs.[IsFinal] AS [StatusIsFinal],
        rs.[IsDeclined] AS [StatusIsDeclined],
        rs.[Color] AS [StatusColor],
        u.[Firstname] AS [UserFirstname],
        u.[Lastname] AS [UserLastname]
    FROM dbo.[ReviewEntry] re
    INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = re.[ReviewStatusId]
    LEFT JOIN dbo.[User] u ON u.[Id] = re.[UserId]
    WHERE re.[Id] = @Id;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewEntryByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        re.[Id],
        re.[PublicId],
        re.[RowVersion],
        CONVERT(VARCHAR(19), re.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), re.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        re.[ReviewStatusId],
        re.[BillId],
        re.[UserId],
        re.[Comments],
        rs.[Name] AS [StatusName],
        rs.[SortOrder] AS [StatusSortOrder],
        rs.[IsFinal] AS [StatusIsFinal],
        rs.[IsDeclined] AS [StatusIsDeclined],
        rs.[Color] AS [StatusColor],
        u.[Firstname] AS [UserFirstname],
        u.[Lastname] AS [UserLastname]
    FROM dbo.[ReviewEntry] re
    INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = re.[ReviewStatusId]
    LEFT JOIN dbo.[User] u ON u.[Id] = re.[UserId]
    WHERE re.[PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewEntriesByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        re.[Id],
        re.[PublicId],
        re.[RowVersion],
        CONVERT(VARCHAR(19), re.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), re.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        re.[ReviewStatusId],
        re.[BillId],
        re.[UserId],
        re.[Comments],
        rs.[Name] AS [StatusName],
        rs.[SortOrder] AS [StatusSortOrder],
        rs.[IsFinal] AS [StatusIsFinal],
        rs.[IsDeclined] AS [StatusIsDeclined],
        rs.[Color] AS [StatusColor],
        u.[Firstname] AS [UserFirstname],
        u.[Lastname] AS [UserLastname]
    FROM dbo.[ReviewEntry] re
    INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = re.[ReviewStatusId]
    LEFT JOIN dbo.[User] u ON u.[Id] = re.[UserId]
    WHERE re.[BillId] = @BillId
    ORDER BY re.[CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadLatestReviewEntryByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        re.[Id],
        re.[PublicId],
        re.[RowVersion],
        CONVERT(VARCHAR(19), re.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), re.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        re.[ReviewStatusId],
        re.[BillId],
        re.[UserId],
        re.[Comments],
        rs.[Name] AS [StatusName],
        rs.[SortOrder] AS [StatusSortOrder],
        rs.[IsFinal] AS [StatusIsFinal],
        rs.[IsDeclined] AS [StatusIsDeclined],
        rs.[Color] AS [StatusColor],
        u.[Firstname] AS [UserFirstname],
        u.[Lastname] AS [UserLastname]
    FROM dbo.[ReviewEntry] re
    INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = re.[ReviewStatusId]
    LEFT JOIN dbo.[User] u ON u.[Id] = re.[UserId]
    WHERE re.[BillId] = @BillId
    ORDER BY re.[CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadReviewEntries
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        re.[Id],
        re.[PublicId],
        re.[RowVersion],
        CONVERT(VARCHAR(19), re.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), re.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        re.[ReviewStatusId],
        re.[BillId],
        re.[UserId],
        re.[Comments],
        rs.[Name] AS [StatusName],
        rs.[SortOrder] AS [StatusSortOrder],
        rs.[IsFinal] AS [StatusIsFinal],
        rs.[IsDeclined] AS [StatusIsDeclined],
        rs.[Color] AS [StatusColor],
        u.[Firstname] AS [UserFirstname],
        u.[Lastname] AS [UserLastname]
    FROM dbo.[ReviewEntry] re
    INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = re.[ReviewStatusId]
    LEFT JOIN dbo.[User] u ON u.[Id] = re.[UserId]
    ORDER BY re.[CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE UpdateReviewEntryById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Comments NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ReviewEntry]
    SET
        [ModifiedDatetime] = @Now,
        [Comments] = @Comments
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ReviewStatusId],
        INSERTED.[BillId],
        INSERTED.[UserId],
        INSERTED.[Comments]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE DeleteReviewEntryById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ReviewEntry]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ReviewStatusId],
        DELETED.[BillId],
        DELETED.[UserId],
        DELETED.[Comments]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE DeleteReviewEntriesByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ReviewEntry]
    WHERE [BillId] = @BillId;

    COMMIT TRANSACTION;
END;
