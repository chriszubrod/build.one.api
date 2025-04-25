CREATE TABLE dbo.ProjectFolder (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [ProjectId] INT NOT NULL,
    [Module] VARCHAR(255) NOT NULL,
    [Path] VARCHAR(255) NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (ProjectId) REFERENCES Project(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);


SELECT * FROM dbo.[Transaction];
SELECT * FROM dbo.[Module];
SELECT * FROM dbo.Project;
SELECT * FROM dbo.ProjectFolder;
SELECT * FROM ms.SharePointFolder;


EXEC CreateBuildoneProjectFolder
    @CreatedDatetime = '2025-01-18 00:00:00',
    @ModifiedDatetime = '2025-01-18 00:00:00',
    @ProjectId = 3,
    @Module = 'worksheet',
    @Path = '\static\worksheet\3'


UPDATE dbo.ProjectFolder
SET Path = '\static\project\3'
WHERE [Id] = 1;


DROP PROCEDURE IF EXISTS CreateBuildoneProjectFolder;

CREATE PROCEDURE CreateBuildoneProjectFolder
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @ProjectId INT,
    @Module VARCHAR(255),
    @Path VARCHAR(255)

AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the ProjectFolder table using the TransactionId
    INSERT INTO ProjectFolder (CreatedDatetime, ModifiedDatetime, ProjectId, Module, [Path], TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @ProjectId, @Module, @Path, @TransactionId);

    COMMIT;
END





DROP PROCEDURE IF EXISTS ReadBuildoneProjectFolders;

CREATE PROCEDURE ReadBuildoneProjectFolders
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [Module],
        [Path],
        [TransactionId]
    FROM ProjectFolder
	ORDER BY [Module];

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadBuildoneProjectFolderByGUID;

CREATE PROCEDURE ReadBuildoneProjectFolderByGUID
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
        [Module],
        [Path],
        [TransactionId]
    FROM ProjectFolder
    WHERE [GUID] = @GUID;

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadBuildoneProjectFolderByGUID;

CREATE PROCEDURE ReadBuildoneProjectFolderByGUID
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
        [Module],
        [Path],
        [TransactionId]
    FROM ProjectFolder
    WHERE [GUID] = @GUID;

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadBuildoneProjectFolderByProjectIdByModule;

CREATE PROCEDURE ReadBuildoneProjectFolderByProjectIdByModule
	@ProjectId INT,
    @Module VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

	SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [Module],
        [Path],
        [TransactionId]
    FROM ProjectFolder
    WHERE [ProjectId] = @ProjectId AND [Module] = @Module;

    COMMIT;
END



ALTER TABLE dbo.ProjectFolder
DROP COLUMN [Url];


UPDATE dbo.ProjectFolder
SET [Path] = '\static\project\3\bill'
WHERE [Id]=2;
