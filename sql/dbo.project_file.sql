CREATE TABLE dbo.ProjectFile (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [ProjectId] INT NOT NULL,
    [Module] VARCHAR(255) NOT NULL,
    [Path] VARCHAR(255) NOT NULL,
    [TransactionId] INT NOT NULL,
    [MsId] NVARCHAR(MAX) NOT NULL,
    FOREIGN KEY (ProjectId) REFERENCES Project(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);


SELECT * FROM dbo.[Transaction];
SELECT * FROM dbo.[Module];
SELECT * FROM dbo.Project;
SELECT * FROM dbo.ProjectFile;
SELECT * FROM ms.SharePointFile;


EXEC CreateBuildoneProjectFile
    @CreatedDatetime = '2025-01-12 00:00:00',
    @ModifiedDatetime = '2025-01-12 00:00:00',
    @ProjectId = 3,
    @Module = 'bill',
    @Path = '/static/bill/3',
    @Url = 'https://imviokguifqdnyjvkb9idegwrhi.sharepoint.com/sites/RogersBuildLLC/Shared%20Documents/General/200%20-%20Rogers%20Build%20Projects/TB3%20917%20Tyne%20Blvd--2023--79/14%20-%20Invoices';





DROP PROCEDURE IF EXISTS CreateBuildoneProjectFile;

CREATE PROCEDURE CreateBuildoneProjectFile
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @ProjectId INT,
    @Module VARCHAR(255),
    @Path VARCHAR(255),
    @MsId NVARCHAR(MAX)
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
    INSERT INTO ProjectFile (CreatedDatetime, ModifiedDatetime, ProjectId, Module, [Path], [MsId], TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @ProjectId, @Module, @Path, @MsId, @TransactionId);

    COMMIT;
END





DROP PROCEDURE IF EXISTS ReadBuildoneProjectFiles;

CREATE PROCEDURE ReadBuildoneProjectFiles
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
        [MsId],
        [TransactionId]
    FROM ProjectFile
	ORDER BY [Module];

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadBuildoneProjectFileByGUID;

CREATE PROCEDURE ReadBuildoneProjectFileByGUID
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
        [MsId],
        [TransactionId]
    FROM ProjectFile
    WHERE [GUID] = @GUID;

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadBuildoneProjectFilesByProjectId;

CREATE PROCEDURE ReadBuildoneProjectFilesByProjectId
    @ProjectId INT
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
        [MsId],
        [TransactionId]
    FROM ProjectFile
    WHERE [ProjectId] = @ProjectId;

    COMMIT;
END
