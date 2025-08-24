CREATE TABLE map.ProjectSharePointWorkbook (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [ProjectId] INT NOT NULL,
    [MsSharePointWorkbookId] INT NOT NULL
);


SELECT * FROM map.ProjectSharePointWorkbook;




DROP PROCEDURE IF EXISTS CreateProjectSharePointWorkbook;

CREATE PROCEDURE CreateProjectSharePointWorkbook
    @ProjectId INT,
    @MsSharePointWorkbookId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the ProjectSharePointWorkbook table
    INSERT INTO map.ProjectSharePointWorkbook (CreatedDatetime, ModifiedDatetime, ProjectId, MsSharePointWorkbookId)
    VALUES (@Now, @Now, @ProjectId, @MsSharePointWorkbookId);

    COMMIT TRANSACTION;
END;

EXEC CreateProjectSharePointWorkbook
    @ProjectId = 3,
    @MsSharePointWorkbookId = 1;






DROP PROCEDURE IF EXISTS ReadProjectSharePointWorkbook;

CREATE PROCEDURE ReadProjectSharePointWorkbook
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [MsSharePointWorkbookId]
    FROM map.ProjectSharePointWorkbook;

    COMMIT TRANSACTION;
END;

EXEC ReadProjectSharePointWorkbook;








DROP PROCEDURE IF EXISTS ReadProjectSharePointWorkbookByProjectId;

CREATE PROCEDURE ReadProjectSharePointWorkbookByProjectId
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
        [MsSharePointWorkbookId]
    FROM map.ProjectSharePointWorkbook
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;

EXEC ReadProjectSharePointWorkbookByProjectId
    @ProjectId = 3;
