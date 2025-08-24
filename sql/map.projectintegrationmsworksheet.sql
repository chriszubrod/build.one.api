CREATE TABLE map.ProjectSharePointWorksheet (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [ProjectId] INT NOT NULL,
    [MsSharePointWorksheetId] INT NOT NULL
);


SELECT * FROM map.ProjectSharePointWorksheet;




DROP PROCEDURE IF EXISTS CreateProjectSharePointWorksheet;

CREATE PROCEDURE CreateProjectSharePointWorksheet
    @ProjectId INT,
    @MsSharePointWorksheetId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the ProjectSharePointWorksheet table
    INSERT INTO map.ProjectSharePointWorksheet (CreatedDatetime, ModifiedDatetime, ProjectId, MsSharePointWorksheetId)
    VALUES (@Now, @Now, @ProjectId, @MsSharePointWorksheetId);

    COMMIT TRANSACTION;
END;

EXEC CreateProjectSharePointWorksheet
    @ProjectId = 3,
    @MsSharePointWorksheetId = 1;






DROP PROCEDURE IF EXISTS ReadProjectSharePointWorksheet;

CREATE PROCEDURE ReadProjectSharePointWorksheet
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [MsSharePointWorksheetId]
    FROM map.ProjectSharePointWorksheet;

    COMMIT TRANSACTION;
END;

EXEC ReadProjectSharePointWorksheet;








DROP PROCEDURE IF EXISTS ReadProjectSharePointWorksheetByProjectId;

CREATE PROCEDURE ReadProjectSharePointWorksheetByProjectId
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
        [MsSharePointWorksheetId]
    FROM map.ProjectSharePointWorksheet
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;

EXEC ReadProjectSharePointWorksheetByProjectId
    @ProjectId = 3;
