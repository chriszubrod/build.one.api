
CREATE TABLE ms.SharePointWorkbook (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
	[CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
	[MsGraphDownloadUrl] NVARCHAR(MAX) NULL,
	[CTag] NVARCHAR(MAX) NULL,
	[MsCreatedDatetime] DATETIMEOFFSET NULL,
	[ETag] NVARCHAR(MAX) NULL,
	[FileHashQuickXorHash] NVARCHAR(MAX) NULL,
	[FileMimeType] NVARCHAR(MAX) NULL,
	[MsId] NVARCHAR(MAX) NULL,
	[LastModifiedDatetime] DATETIMEOFFSET NULL,
	[Name] NVARCHAR(MAX) NULL,
	[MsParentId] NVARCHAR(MAX) NULL,
	[SharedScope] NVARCHAR(MAX) NULL,
	[Size] BIGINT NULL,
    [WebUrl] NVARCHAR(MAX) NULL
);

DROP TABLE ms.SharePointWorkbook;




DROP PROCEDURE IF EXISTS CreateMsSharePointWorkbook;

CREATE PROCEDURE CreateMsSharePointWorkbook
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
	@MsGraphDownloadUrl NVARCHAR(MAX),
	@CTag NVARCHAR(MAX),
	@MsCreatedDatetime DATETIMEOFFSET,
	@ETag NVARCHAR(MAX),
	@FileHashQuickXorHash NVARCHAR(MAX),
	@FileMimeType NVARCHAR(MAX),
	@MsId NVARCHAR(MAX),
	@LastModifiedDatetime DATETIMEOFFSET,
	@Name NVARCHAR(MAX),
	@MsParentId NVARCHAR(MAX),
	@SharedScope NVARCHAR(MAX),
	@Size BIGINT,
    @WebUrl NVARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the SharePointSite table
    INSERT INTO ms.SharePointWorkbook (CreatedDatetime, ModifiedDatetime, [MsGraphDownloadUrl], [CTag], [MsCreatedDatetime], [ETag], [FileHashQuickXorHash], [FileMimeType], [MsId], [LastModifiedDatetime], [Name], [MsParentId], [SharedScope], [Size], [WebUrl])
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @MsGraphDownloadUrl, @CTag, @MsCreatedDatetime, @ETag, @FileHashQuickXorHash, @FileMimeType, @MsId, CONVERT(DATETIMEOFFSET, @LastModifiedDatetime), @Name, @MsParentId, @SharedScope, @Size, @WebUrl);

    COMMIT;
END


EXEC CreateMsSharePointWorkbook
	@CreatedDatetime = '2025-01-18 00:00:00.000',
    @ModifiedDatetime = '2025-01-18 00:00:00.000',
	@MsGraphDownloadUrl = 'https://imviokguifqdnyjvkb9idegwrhi.sharepoint.com/sites/RogersBuildLLC/_layouts/15/download.aspx?UniqueId=02163af1-5407-476a-b73b-36243f7e43b9&Translate=false&tempauth=v1.eyJzaXRlaWQiOiIxNzk4MTEzOS02MjRlLTQ4YjAtYjFjYS0zNmEyMWFiOGU5NjMiLCJhcHBfZGlzcGxheW5hbWUiOiJidWlsZG9uZSIsImFwcGlkIjoiOThhNjQ1YmQtZWVkOS00ZjY0LTk4MzktMDUxNTU3MDZlMWY2IiwiYXVkIjoiMDAwMDAwMDMtMDAwMC0wZmYxLWNlMDAtMDAwMDAwMDAwMDAwL2ltdmlva2d1aWZxZG55anZrYjlpZGVnd3JoaS5zaGFyZXBvaW50LmNvbUA1ZGFmMTNhMS01MTEzLTRkMmMtYmI0My0xM2M2ZDExM2NmMTgiLCJleHAiOiIxNzM3MjU5NjI5In0.CgoKBHNuaWQSAjY0EgsItvqWz4yQ3D0QBRoNNDAuMTI2LjIzLjE2MyoscExESFZpL09jMFJieDMzcnFxMjB2Mzh3elByUGpnNWhpM0hnM0N2ODlDaz0wnwE4AUIQoXjrQh2gAHBtr4_csBlSREoQaGFzaGVkcHJvb2Z0b2tlblIIWyJrbXNpIl1yKTBoLmZ8bWVtYmVyc2hpcHwxMDAzMjAwMWE4MGE4ZGY0QGxpdmUuY29tegEyggESCaETr10TUSxNEbtDE8bRE88YkgEHSW52b2ljZZoBDFJvZ2VycyBCdWlsZKIBF2ludm9pY2VAcm9nZXJzYnVpbGQuY29tqgEQMTAwMzIwMDFBODBBOERGNLIBSWFsbGZpbGVzLnJlYWQgYWxsZmlsZXMud3JpdGUgYWxsc2l0ZXMucmVhZCBzZWxlY3RlZHNpdGVzIGFsbHByb2ZpbGVzLnJlYWTIAQE.JgFmoIZEEBgoEGNvsgH34yovhUOpbDKjJdvRRHD_UE8&ApiVersion=2.0',
	@CTag = '\"c:{02163AF1-5407-476A-B73B-36243F7E43B9},326\"',
	@MsCreatedDatetime = '2023-04-13T14:42:15Z',
	@ETag = '\"{02163AF1-5407-476A-B73B-36243F7E43B9},317\"',
	@FileHashQuickXorHash = 'OrG2CB3R9HwHyUOQ2qJs9YlgYtk=',
	@FileMimeType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
	@MsId = '017ZKYN57RHILAEB2UNJD3OOZWEQ7X4Q5Z',
	@LastModifiedDatetime = '2025-01-19T02:53:45Z',
	@Name = 'TB3 - 917 Tyne Blvd - Budget Tracker.xlsx',
	@MsParentId = '017ZKYN56O27CXUCQ6RBA3G4CA2SQMNAV3',
	@SharedScope = 'users',
	@Size = '517695',
    @WebUrl = 'https://imviokguifqdnyjvkb9idegwrhi.sharepoint.com/sites/RogersBuildLLC/_layouts/15/Doc.aspx?sourcedoc=%7B02163AF1-5407-476A-B73B-36243F7E43B9%7D&file=TB3%20-%20917%20Tyne%20Blvd%20-%20Budget%20Tracker.xlsx&action=default&mobileredirect=true'






DROP PROCEDURE IF EXISTS ReadMsSharePointWorkbook;

CREATE PROCEDURE ReadMsSharePointWorkbook
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
		[MsGraphDownloadUrl],
		[CTag],
		[MsCreatedDatetime],
		[ETag],
		[FileHashQuickXorHash],
		[FileMimeType],
		[MsId],
		[MsParentId],
		[SharedScope],
		[Size],
		[WebUrl]
	FROM ms.SharePointWorkbook;

    COMMIT;
END







DROP PROCEDURE IF EXISTS ReadMsSharePointWorkbookById;

CREATE PROCEDURE ReadMsSharePointWorkbookById
    @Id INT
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
		[MsGraphDownloadUrl],
		[CTag],
		CAST([MsCreatedDatetime] AS NVARCHAR(MAX)) AS [MsCreatedDatetime],
		[ETag],
		[FileHashQuickXorHash],
		[FileMimeType],
		[MsId],
		CAST([LastModifiedDatetime] AS NVARCHAR(MAX)) AS [LastModifiedDatetime],
		[Name],
		[MsParentId],
		[SharedScope],
		[Size],
		[WebUrl]
	FROM ms.SharePointWorkbook
	WHERE [Id] = @Id;

    COMMIT;
END

EXEC ReadMsSharePointWorkbookById
	@Id = 1;




DROP PROCEDURE IF EXISTS UpdateMsSharePointWorkbookByFileId;

CREATE PROCEDURE UpdateMsSharePointWorkbookByFileId
    @FileId NVARCHAR(MAX),
    @ModifiedDatetime DATETIMEOFFSET,
	@MsGraphDownloadUrl NVARCHAR(MAX),
	@CTag NVARCHAR(MAX),
	@MsCreatedDatetime DATETIMEOFFSET,
	@ETag NVARCHAR(MAX),
	@FileHashQuickXorHash NVARCHAR(MAX),
	@FileMimeType NVARCHAR(MAX),
	@MsId NVARCHAR(MAX),
	@LastModifiedDatetime DATETIMEOFFSET,
	@Name NVARCHAR(MAX),
	@MsParentId NVARCHAR(MAX),
	@SharedScope NVARCHAR(MAX),
	@Size BIGINT,
    @WebUrl NVARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

	UPDATE ms.SharePointWorkbook
    SET [ModifiedDatetime] = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [MsGraphDownloadUrl] = @MsGraphDownloadUrl,
        [CTag] = @CTag,
        [MsCreatedDatetime] = @MsCreatedDatetime,
        [ETag] = @ETag,
        [FileHashQuickXorHash] = @FileHashQuickXorHash,
        [FileMimeType] = @FileMimeType,
        [MsId] = @MsId,
        [LastModifiedDatetime] = CONVERT(DATETIMEOFFSET, @LastModifiedDatetime),
        [Name] = @Name,
        [MsParentId] = @MsParentId,
        [SharedScope] = @SharedScope,
        [Size] = @Size,
        [WebUrl] = @WebUrl
    WHERE [Id] = @FileId;

    COMMIT;
END


