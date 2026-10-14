IF OBJECT_ID('ms.DriveItem', 'U') IS NOT NULL
GO

IF OBJECT_ID('ms.DriveItem', 'U') IS NULL
BEGIN
CREATE TABLE ms.DriveItem
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [MsDriveId] BIGINT NOT NULL,
    [ItemId] NVARCHAR(255) NOT NULL,
    [ParentItemId] NVARCHAR(255) NULL,
    [Name] NVARCHAR(255) NOT NULL,
    [ItemType] NVARCHAR(50) NOT NULL,
    [Size] BIGINT NULL,
    [MimeType] NVARCHAR(255) NULL,
    [WebUrl] NVARCHAR(MAX) NOT NULL,
    [GraphCreatedDatetime] DATETIME2(3) NULL,
    [GraphModifiedDatetime] DATETIME2(3) NULL,
    CONSTRAINT FK_DriveItem_Drive FOREIGN KEY ([MsDriveId]) REFERENCES ms.Drive([Id]) ON DELETE CASCADE
);
END
GO

IF OBJECT_ID('ms.DriveItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DriveItem_ItemId' AND object_id = OBJECT_ID('ms.DriveItem'))
BEGIN
CREATE UNIQUE INDEX IX_DriveItem_ItemId ON ms.DriveItem ([ItemId]);
END
GO

IF OBJECT_ID('ms.DriveItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DriveItem_MsDriveId' AND object_id = OBJECT_ID('ms.DriveItem'))
BEGIN
CREATE INDEX IX_DriveItem_MsDriveId ON ms.DriveItem ([MsDriveId]);
END
GO

IF OBJECT_ID('ms.DriveItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DriveItem_ParentItemId' AND object_id = OBJECT_ID('ms.DriveItem'))
BEGIN
CREATE INDEX IX_DriveItem_ParentItemId ON ms.DriveItem ([ParentItemId]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateMsDriveItem
(
    @MsDriveId BIGINT,
    @ItemId NVARCHAR(255),
    @ParentItemId NVARCHAR(255),
    @Name NVARCHAR(255),
    @ItemType NVARCHAR(50),
    @Size BIGINT,
    @MimeType NVARCHAR(255),
    @WebUrl NVARCHAR(MAX),
    @GraphCreatedDatetime DATETIME2(3),
    @GraphModifiedDatetime DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO ms.DriveItem ([CreatedDatetime], [ModifiedDatetime], [MsDriveId], [ItemId], [ParentItemId], [Name], [ItemType], [Size], [MimeType], [WebUrl], [GraphCreatedDatetime], [GraphModifiedDatetime])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[MsDriveId],
        INSERTED.[ItemId],
        INSERTED.[ParentItemId],
        INSERTED.[Name],
        INSERTED.[ItemType],
        INSERTED.[Size],
        INSERTED.[MimeType],
        INSERTED.[WebUrl],
        CONVERT(VARCHAR(19), INSERTED.[GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    VALUES (@Now, @Now, @MsDriveId, @ItemId, @ParentItemId, @Name, @ItemType, @Size, @MimeType, @WebUrl, @GraphCreatedDatetime, @GraphModifiedDatetime);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadMsDriveItems
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadMsDriveItemsByMsDriveId
(
    @MsDriveId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem
    WHERE [MsDriveId] = @MsDriveId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadMsDriveItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadMsDriveItemByItemId
(
    @ItemId NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem
    WHERE [ItemId] = @ItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadMsDriveItemsByParentItemId
(
    @ParentItemId NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem
    WHERE [ParentItemId] = @ParentItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateMsDriveItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @MsDriveId BIGINT,
    @ItemId NVARCHAR(255),
    @ParentItemId NVARCHAR(255),
    @Name NVARCHAR(255),
    @ItemType NVARCHAR(50),
    @Size BIGINT,
    @MimeType NVARCHAR(255),
    @WebUrl NVARCHAR(MAX),
    @GraphCreatedDatetime DATETIME2(3),
    @GraphModifiedDatetime DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE ms.DriveItem
    SET [ModifiedDatetime] = @Now,
        [MsDriveId] = @MsDriveId,
        [ItemId] = @ItemId,
        [ParentItemId] = @ParentItemId,
        [Name] = @Name,
        [ItemType] = @ItemType,
        [Size] = @Size,
        [MimeType] = @MimeType,
        [WebUrl] = @WebUrl,
        [GraphCreatedDatetime] = @GraphCreatedDatetime,
        [GraphModifiedDatetime] = @GraphModifiedDatetime
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[MsDriveId],
        INSERTED.[ItemId],
        INSERTED.[ParentItemId],
        INSERTED.[Name],
        INSERTED.[ItemType],
        INSERTED.[Size],
        INSERTED.[MimeType],
        INSERTED.[WebUrl],
        CONVERT(VARCHAR(19), INSERTED.[GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteMsDriveItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM ms.DriveItem
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[MsDriveId],
        DELETED.[ItemId],
        DELETED.[ParentItemId],
        DELETED.[Name],
        DELETED.[ItemType],
        DELETED.[Size],
        DELETED.[MimeType],
        DELETED.[WebUrl],
        CONVERT(VARCHAR(19), DELETED.[GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

-- Actor-scoped like dbo.ReadProjects: system admins see all; non-admins only
-- UserProject rows; NULL @ActorUserId matches no rows (fail closed).
CREATE OR ALTER PROCEDURE ReadSharePointProjectMappingHealth
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Since DATETIME2(3) = DATEADD(day, -90, SYSUTCDATETIME());

    ;WITH ActiveProjects AS (
        SELECT DISTINCT [ProjectId]
        FROM [dbo].[BillLineItem]
        WHERE [ProjectId] IS NOT NULL
        UNION
        SELECT DISTINCT [ProjectId]
        FROM [dbo].[ExpenseLineItem]
        WHERE [ProjectId] IS NOT NULL
    ),
    BillCounts AS (
        SELECT [ProjectId], COUNT(*) AS BillLines90d
        FROM [dbo].[BillLineItem]
        WHERE [ProjectId] IS NOT NULL
          AND [CreatedDatetime] >= @Since
        GROUP BY [ProjectId]
    ),
    ExpenseCounts AS (
        SELECT [ProjectId], COUNT(*) AS ExpenseLines90d
        FROM [dbo].[ExpenseLineItem]
        WHERE [ProjectId] IS NOT NULL
          AND [CreatedDatetime] >= @Since
        GROUP BY [ProjectId]
    )
    SELECT
        p.[Id] AS ProjectId,
        p.[Name] AS ProjectName,
        CASE WHEN dip.[Id] IS NOT NULL THEN 1 ELSE 0 END AS HasRoot,
        CASE WHEN m_bills.[Id] IS NOT NULL THEN 1 ELSE 0 END AS HasBillsFolder,
        CASE WHEN m_exp.[Id] IS NOT NULL THEN 1 ELSE 0 END AS HasExpensesFolder,
        CASE WHEN m_inv.[Id] IS NOT NULL THEN 1 ELSE 0 END AS HasInvoicesFolder,
        CASE WHEN m_bc.[Id] IS NOT NULL THEN 1 ELSE 0 END AS HasBillCreditsFolder,
        CASE WHEN dex.[Id] IS NOT NULL THEN 1 ELSE 0 END AS HasExcel,
        dex.[WorksheetName] AS WorksheetName,
        ISNULL(bc.[BillLines90d], 0) AS BillLines90d,
        ISNULL(ec.[ExpenseLines90d], 0) AS ExpenseLines90d
    FROM [dbo].[Project] p
    INNER JOIN ActiveProjects ap ON ap.[ProjectId] = p.[Id]
    LEFT JOIN [ms].[DriveItemProject] dip ON dip.[ProjectId] = p.[Id]
    LEFT JOIN [dbo].[Module] mod_bills ON mod_bills.[Name] = N'Bills'
    LEFT JOIN [ms].[DriveItemProjectModule] m_bills
        ON m_bills.[ProjectId] = p.[Id] AND m_bills.[ModuleId] = mod_bills.[Id]
    LEFT JOIN [dbo].[Module] mod_exp ON mod_exp.[Name] = N'Expenses'
    LEFT JOIN [ms].[DriveItemProjectModule] m_exp
        ON m_exp.[ProjectId] = p.[Id] AND m_exp.[ModuleId] = mod_exp.[Id]
    LEFT JOIN [dbo].[Module] mod_inv ON mod_inv.[Name] = N'Invoices'
    LEFT JOIN [ms].[DriveItemProjectModule] m_inv
        ON m_inv.[ProjectId] = p.[Id] AND m_inv.[ModuleId] = mod_inv.[Id]
    LEFT JOIN [dbo].[Module] mod_bc ON mod_bc.[Name] = N'Bill Credits'
    LEFT JOIN [ms].[DriveItemProjectModule] m_bc
        ON m_bc.[ProjectId] = p.[Id] AND m_bc.[ModuleId] = mod_bc.[Id]
    LEFT JOIN [ms].[DriveItemProjectExcel] dex ON dex.[ProjectId] = p.[Id]
    LEFT JOIN BillCounts bc ON bc.[ProjectId] = p.[Id]
    LEFT JOIN ExpenseCounts ec ON ec.[ProjectId] = p.[Id]
    WHERE
        @ActorIsSystemAdmin = 1
        OR EXISTS (
            SELECT 1 FROM dbo.[UserProject] up
            WHERE up.[UserId] = @ActorUserId AND up.[ProjectId] = p.[Id]
        )
    ORDER BY (ISNULL(bc.[BillLines90d], 0) + ISNULL(ec.[ExpenseLines90d], 0)) DESC, p.[Id];
END;
GO

