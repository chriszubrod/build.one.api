-- Create table for mapping projects to SharePoint folders.
CREATE TABLE IF NOT EXISTS map.Project_SharePointFolder (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [ProjectId] INT NOT NULL,
    [SharePointFolderId] INT NOT NULL,
    [FolderType] NVARCHAR(50) NOT NULL, -- 'root', 'bill', 'expense', 'invoice', 'other'
);




DROP PROCEDURE IF EXISTS CreateMapProjectSharePointFolder;

CREATE PROCEDURE CreateMapProjectSharePointFolder
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @ProjectId INT,
    @SharePointFolderId INT,
    @FolderType NVARCHAR(50)

AS
BEGIN
    BEGIN TRANSACTION;

    INSERT INTO map.Project_SharePointFolder (
        [CreatedDatetime],
        [ModifiedDatetime],
        [ProjectId],
        [SharePointFolderId],
        [FolderType]
    )

    VALUES (
        CONVERT(DATETIMEOFFSET, @CreatedDatetime),
        CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        @ProjectId,
        @SharePointFolderId,
        @FolderType
    );

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadMapProjectSharePointFolders;

CREATE PROCEDURE ReadMapProjectSharePointFolders
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [ProjectId],
        [SharePointFolderId],
        [FolderType]
    FROM map.Project_SharePointFolder;

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadMapProjectSharePointFolderById;

CREATE PROCEDURE ReadMapProjectSharePointFolderById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [ProjectId],
        [SharePointFolderId],
        [FolderType]
    FROM map.Project_SharePointFolder
    WHERE [Id] = @Id;

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadMapProjectSharePointFolderByProjectId;

CREATE PROCEDURE ReadMapProjectSharePointFolderByProjectId
    @ProjectId INT
AS
BEGIN

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [ProjectId],
        [SharePointFolderId],
        [FolderType]
    FROM map.Project_SharePointFolder
    WHERE [ProjectId] = @ProjectId;

    COMMIT;
END
