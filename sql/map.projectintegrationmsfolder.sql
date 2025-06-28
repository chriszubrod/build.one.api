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



DROP PROCEDURE IF EXISTS CreateProjectSharePointFolder;

CREATE PROCEDURE CreateProjectSharePointFolder
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @ProjectId INT,
    @ModuleId INT,
    @MsSharePointFolderId INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the ProjectSharePointFolder table
    INSERT INTO map.ProjectSharePointFolder (CreatedDatetime, ModifiedDatetime, ProjectId, ModuleId, MsSharePointFolderId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @ProjectId, @ModuleId, @MsSharePointFolderId);

    COMMIT TRANSACTION;
END;

EXEC CreateProjectSharePointFolder
    '2025-06-20T00:00:00.000',
    '2025-06-20T00:00:00.000',
    3,
    4,
    2;



DROP PROCEDURE IF EXISTS ReadProjectSharePointFolder;

CREATE PROCEDURE ReadProjectSharePointFolder
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

    COMMIT TRANSACTION;
END;

EXEC ReadProjectSharePointFolder;



DROP PROCEDURE IF EXISTS ReadProjectSharePointFolderByGUID;

CREATE PROCEDURE ReadProjectSharePointFolderByGUID
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

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS ReadProjectSharePointFolderByProjectIdByModuleId;

CREATE PROCEDURE ReadProjectSharePointFolderByProjectIdByModuleId
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

    COMMIT TRANSACTION;
END;

EXEC ReadProjectSharePointFolderByProjectIdByModuleId
    3,
    4;




DROP PROCEDURE IF EXISTS UpdateProjectSharePointFolderById;

CREATE PROCEDURE UpdateProjectSharePointFolderById
    @Id INT,
    @ModifiedDatetime DATETIMEOFFSET,
    @ProjectId INT,
    @ModuleId INT,
    @MsSharePointFolderId INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Update the record in the ProjectSharePointFolder table
    UPDATE map.ProjectSharePointFolder
    SET
        ModifiedDatetime = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        ProjectId = @ProjectId,
        ModuleId = @ModuleId,
        MsSharePointFolderId = @MsSharePointFolderId
    WHERE Id = @Id;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS DeleteProjectSharePointFolderById;

CREATE PROCEDURE DeleteProjectSharePointFolderById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Delete the record from the ProjectSharePointFolder table by Id
    DELETE FROM map.ProjectSharePointFolder
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
