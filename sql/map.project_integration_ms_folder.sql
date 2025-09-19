CREATE TABLE map.ProjectSharePointFolder (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [ProjectId] INT NOT NULL,
    [ModuleId] INT NOT NULL,
    [MsSharePointFolderId] INT NOT NULL
);


DROP TABLE map.ProjectSharePointFolder;



DROP PROCEDURE IF EXISTS CreateMapProjectSharePointFolder;

CREATE PROCEDURE CreateMapProjectSharePointFolder
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @ProjectId INT,
    @ModuleId INT,
    @MsSharePointFolderId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the ProjectSharePointFolder table
    INSERT INTO map.ProjectSharePointFolder (CreatedDatetime, ModifiedDatetime, ProjectId, ModuleId, MsSharePointFolderId)
    VALUES (@Now, @Now, @ProjectId, @ModuleId, @MsSharePointFolderId);

    COMMIT;
END

EXEC CreateMapProjectSharePointFolder
    SYSDATETIMEOFFSET(),
    SYSDATETIMEOFFSET(),
    3,
    4,
    2;



DROP PROCEDURE IF EXISTS ReadMapProjectSharePointFolder;

CREATE PROCEDURE ReadMapProjectSharePointFolder
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [ModuleId],
        [MsSharePointFolderId]
    FROM map.ProjectSharePointFolder;

    COMMIT;
END

EXEC ReadMapProjectSharePointFolder;



DROP PROCEDURE IF EXISTS ReadMapProjectSharePointFolderByGUID;

CREATE PROCEDURE ReadMapProjectSharePointFolderByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [ModuleId],
        [MsSharePointFolderId]
    FROM map.ProjectSharePointFolder
    WHERE [GUID] = @GUID;

    COMMIT;
END

EXEC ReadMapProjectSharePointFolderByGUID
    'put-some-guid-here';



DROP PROCEDURE IF EXISTS ReadMapProjectSharePointFolderByProjectId;

CREATE PROCEDURE ReadMapProjectSharePointFolderByProjectId
    @ProjectId Int
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [ModuleId],
        [MsSharePointFolderId]
    FROM map.ProjectSharePointFolder
    WHERE [ProjectId] = @ProjectId;

    COMMIT;
END

SELECT * FROM Project;
EXEC ReadMapProjectSharePointFolderByProjectId
    4;







DROP PROCEDURE IF EXISTS ReadMapProjectSharePointFolderByProjectIdByModuleId;

CREATE PROCEDURE ReadMapProjectSharePointFolderByProjectIdByModuleId
    @ProjectId INT,
    @ModuleId INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [ModuleId],
        [MsSharePointFolderId]
    FROM map.ProjectSharePointFolder
    WHERE ProjectId = @ProjectId AND ModuleId = @ModuleId;

    COMMIT;
END

EXEC ReadMapProjectSharePointFolderByProjectIdByModuleId
    3,
    4;




DROP PROCEDURE IF EXISTS UpdateMapProjectSharePointFolderById;

CREATE PROCEDURE UpdateMapProjectSharePointFolderById
    @Id INT,
    @ProjectId INT,
    @ModuleId INT,
    @MsSharePointFolderId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Update the record in the ProjectSharePointFolder table
    UPDATE map.ProjectSharePointFolder
    SET
        ModifiedDatetime = @Now,
        ProjectId = @ProjectId,
        ModuleId = @ModuleId,
        MsSharePointFolderId = @MsSharePointFolderId
    WHERE Id = @Id;

    COMMIT;
END

EXEC UpdateMapProjectSharePointFolderById
    1,
    3,
    4,
    2;



DROP PROCEDURE IF EXISTS DeleteMapProjectSharePointFolderById;

CREATE PROCEDURE DeleteMapProjectSharePointFolderById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Delete the record from the ProjectSharePointFolder table by Id
    DELETE FROM map.ProjectSharePointFolder
    WHERE [Id] = @Id;

    COMMIT;
END

EXEC DeleteMapProjectSharePointFolderById
    1;
