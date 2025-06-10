CREATE SCHEMA ms;
GO


CREATE TABLE ms.SharePointSite (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
	[CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
	[ODataContext] NVARCHAR(MAX) NULL,
	[Description] NVARCHAR(MAX) NULL,
	[DisplayName] NVARCHAR(255) NULL,
    [SiteId] NVARCHAR(MAX) NULL,
	[LastModifiedDatetime] DATETIMEOFFSET NULL,
	[Name] NVARCHAR(MAX) NULL,
	[Root] NVARCHAR(MAX) NULL,
	[SiteCollectionHostName] NVARCHAR(MAX) NULL,
    [WebUrl] NVARCHAR(MAX) NULL
);

DROP TABLE ms.SharePointSite;




DROP PROCEDURE IF EXISTS CreateMsSharePointSite;

CREATE PROCEDURE CreateMsSharePointSite
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
	@ODataContext NVARCHAR(MAX),
	@Description NVARCHAR(MAX),
	@DisplayName NVARCHAR(255),
    @SiteId NVARCHAR(MAX),
	@LastModifiedDatetime DATETIMEOFFSET,
	@Name NVARCHAR(MAX),
	@Root NVARCHAR(MAX),
	@SiteCollectionHostName NVARCHAR(MAX),
    @WebUrl NVARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the SharePointSite table
    INSERT INTO ms.SharePointSite (CreatedDatetime, ModifiedDatetime, [ODataContext], [Description], [DisplayName], [SiteId], [LastModifiedDatetime], [Name], [Root], [SiteCollectionHostName], [WebUrl])
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @ODataContext, @Description, @DisplayName, @SiteId, CONVERT(DATETIMEOFFSET, @LastModifiedDatetime), @Name, @Root, @SiteCollectionHostName, @WebUrl);

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadMsSharePointSites;

CREATE PROCEDURE ReadMsSharePointSites
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
		[ODataContext],
		[Description],
		[DisplayName],
		[SiteId],
		CAST([LastModifiedDatetime] AS NVARCHAR(MAX)) AS [LastModifiedDatetime],
		[Name],
		[Root],
		[SiteCollectionHostName],
		[WebUrl]
	FROM ms.SharePointSite;

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadMsSharePointSiteBySiteId;

CREATE PROCEDURE ReadMsSharePointSiteBySiteId
    @SiteId NVARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
		[ODataContext],
		[Description],
		[DisplayName],
		[SiteId],
		CAST([LastModifiedDatetime] AS NVARCHAR(MAX)) AS [LastModifiedDatetime],
		[Name],
		[Root],
		[SiteCollectionHostName],
		[WebUrl]
	FROM ms.SharePointSite
	WHERE [SiteId] = @SiteId;

    COMMIT;
END





DROP PROCEDURE IF EXISTS UpdateMsSharePointSiteBySiteId;

CREATE PROCEDURE UpdateMsSharePointSiteBySiteId
    @ModifiedDatetime DATETIMEOFFSET,
	@ODataContext NVARCHAR(MAX),
	@Description NVARCHAR(MAX),
	@DisplayName NVARCHAR(255),
    @SiteId NVARCHAR(MAX),
	@LastModifiedDatetime DATETIMEOFFSET,
	@Name NVARCHAR(MAX),
	@Root NVARCHAR(MAX),
	@SiteCollectionHostName NVARCHAR(MAX),
    @WebUrl NVARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

    UPDATE ms.SharePointSite
    SET [ModifiedDatetime] = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [ODataContext] = @ODataContext,
        [Description] = @Description,
        [DisplayName] = @DisplayName,
        [LastModifiedDatetime] = CONVERT(DATETIMEOFFSET, @LastModifiedDatetime),
        [Name] = @Name,
        [Root] = @Root,
        [SiteCollectionHostName] = @SiteCollectionHostName,
        [WebUrl] = @WebUrl
    WHERE [SiteId] = @SiteId;

    COMMIT;
END
