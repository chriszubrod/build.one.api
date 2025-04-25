
CREATE TABLE ms.SharePointFile (
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

DROP TABLE ms.SharePointFile;






DROP PROCEDURE IF EXISTS CreateMsSharePointFile;

CREATE PROCEDURE CreateMsSharePointFile
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
    INSERT INTO ms.SharePointFile (CreatedDatetime, ModifiedDatetime, [MsGraphDownloadUrl], [CTag], [MsCreatedDatetime], [ETag], [FileHashQuickXorHash], [FileMimeType], [MsId], [LastModifiedDatetime], [Name], [MsParentId], [SharedScope], [Size], [WebUrl])
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @MsGraphDownloadUrl, @CTag, @MsCreatedDatetime, @ETag, @FileHashQuickXorHash, @FileMimeType, @MsId, CONVERT(DATETIMEOFFSET, @LastModifiedDatetime), @Name, @MsParentId, @SharedScope, @Size, @WebUrl);

    COMMIT;
END





DROP PROCEDURE IF EXISTS ReadMsSharePointFiles;

CREATE PROCEDURE ReadMsSharePointFiles
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
	FROM ms.SharePointFile;

    COMMIT;
END







DROP PROCEDURE IF EXISTS ReadMsSharePointFileByFileId;

CREATE PROCEDURE ReadMsSharePointFileByFileId
    @FileId NVARCHAR(MAX)
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
		[LastModifiedDatetime],
		[Name],
		[MsParentId],
		[SharedScope],
		[Size],
		[WebUrl]
	FROM ms.SharePointFile
	WHERE [Id] = @FileId;

    COMMIT;
END






DROP PROCEDURE IF EXISTS UpdateMsSharePointFileByFileId;

CREATE PROCEDURE UpdateMsSharePointFileByFileId
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

	UPDATE ms.SharePointFile
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





-- Copy from SharePointFolder to SharePointFile
INSERT INTO ms.SharePointFile (
    CreatedDatetime,
    ModifiedDatetime,
    MsGraphDownloadUrl,
    CTag,
    MsCreatedDatetime,
    ETag,
    FileHashQuickXorHash,
    FileMimeType,
    MsId,
    LastModifiedDatetime,
    Name,
    MsParentId,
    SharedScope,
    Size,
    WebUrl
)
SELECT 
    CreatedDatetime,
    ModifiedDatetime,
    MsGraphDownloadUrl,
    CTag,
    MsCreatedDatetime,
    ETag,
    FileHashQuickXorHash,
    FileMimeType,
    MsId,
    LastModifiedDatetime,
    Name,
    MsParentId,
    SharedScope,
    Size,
    WebUrl
FROM ms.SharePointFolder
WHERE Id = 123;  -- Replace with your folder ID