CREATE SCHEMA ms;
GO

CREATE TABLE ms.SharePointFolder (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
	[CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
	[CTag] NVARCHAR(MAX) NULL,
	[MsCreatedDatetime] DATETIMEOFFSET NULL,
	[ETag] NVARCHAR(MAX) NULL,
	[FolderChildCount] INT NULL,
	[MsId] NVARCHAR(MAX) NULL,
	[LastModifiedDatetime] DATETIMEOFFSET NULL,
	[Name] NVARCHAR(MAX) NULL,
	[MsParentId] NVARCHAR(MAX) NULL,
	[SharedScope] NVARCHAR(MAX) NULL,
	[Size] BIGINT NULL,
    [WebUrl] NVARCHAR(MAX) NULL
);

DROP TABLE ms.SharePointFolder;

INSERT INTO ms.SharePointFolder ([CreatedDatetime], [ModifiedDatetime], [CTag], [MsCreatedDatetime], [ETag], [FolderChildCount], [MsId], [LastModifiedDatetime], [Name], [MsParentId], [SharedScope], [Size], [WebUrl])
VALUES (
	CONVERT(DATETIMEOFFSET, SYSDATETIME()),
	CONVERT(DATETIMEOFFSET, SYSDATETIME()),
	'\"c:{2B5E354D-A8AA-4DDE-A43B-344130C4B7F6},0\"',
	CONVERT(DATETIMEOFFSET, '2023-01-20T19:43:30Z'),
	'\"{2B5E354D-A8AA-4DDE-A43B-344130C4B7F6},2\"', 
	120,
	'017ZKYN52NGVPCXKVI3ZG2IOZUIEYMJN7W',
	CONVERT(DATETIMEOFFSET, '2022-01-10T18:11:39Z'),
	'14 - Invoices',
	'017ZKYN5ZUVLRPUT3ZDJGLYQGSBM77SYQW',
	'users',
	11863444,
	'https://imviokguifqdnyjvkb9idegwrhi.sharepoint.com/sites/RogersBuildLLC/Shared%20Documents/General/200%20-%20Rogers%20Build%20Projects/TB3%20917%20Tyne%20Blvd--2023--79/14%20-%20Invoices'
);

SELECT * FROM ms.SharePointFolder;



DROP PROCEDURE IF EXISTS ReadMsSharePointFolderByFolderId;

CREATE PROCEDURE ReadMsSharePointFolderByFolderId
	@Id INT
AS
BEGIN
	BEGIN TRANSACTION;

	SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
		[CTag],
		CAST([MsCreatedDatetime] AS NVARCHAR(MAX)) AS [MsCreatedDatetime],
		[ETag],
		[FolderChildCount],
		[MsId],
		CAST([LastModifiedDatetime] AS NVARCHAR(MAX)) AS [LastModifiedDatetime],
		[Name],
		[MsParentId],
		[SharedScope],
		[Size],
		[WebUrl]
	FROM ms.SharePointFolder
	WHERE [Id] = @Id;

    COMMIT;
END

EXEC ReadMsSharePointFolderByFolderId
	@Id = 2;



DROP PROCEDURE IF EXISTS ReadMsSharePointFolderByUrl;

CREATE PROCEDURE ReadMsSharePointFolderByUrl
    @Url NVARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

	SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
		[CTag],
		CAST([MsCreatedDatetime] AS NVARCHAR(MAX)) AS [MsCreatedDatetime],
		[ETag],
		[FolderChildCount],
		[MsId],
		CAST([LastModifiedDatetime] AS NVARCHAR(MAX)) AS [LastModifiedDatetime],
		[Name],
		[MsParentId],
		[SharedScope],
		[Size],
		[WebUrl]
	FROM ms.SharePointFolder
	WHERE [WebUrl] = @Url;

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadMsSharePointFolders;

CREATE PROCEDURE ReadMsSharePointFolders
AS
BEGIN
	BEGIN TRANSACTION;

	SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
		[CTag],
		CAST([MsCreatedDatetime] AS NVARCHAR(MAX)) AS [MsCreatedDatetime],
		[ETag],
		[FolderChildCount],
		[MsId],
		CAST([LastModifiedDatetime] AS NVARCHAR(MAX)) AS [LastModifiedDatetime],
		[Name],
		[MsParentId],
		[SharedScope],
		[Size],
		[WebUrl]
	FROM ms.SharePointFolder;

    COMMIT;
END


