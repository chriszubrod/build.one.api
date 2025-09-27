
CREATE TABLE ms.SharePointWorksheet (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
	[CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
	[MsODataId] NVARCHAR(MAX) NULL,
	[MsId] NVARCHAR(MAX) NULL,
	[Name] NVARCHAR(MAX) NULL,
	[Position] INT NULL,
	[Visibility] NVARCHAR(MAX) NULL
);

DROP TABLE ms.SharePointWorksheet;

SELECT * FROM ms.SharePointWorksheet;
DELETE FROM ms.SharePointWorksheet;


DROP PROCEDURE IF EXISTS CreateMsSharePointWorksheet;

CREATE PROCEDURE CreateMsSharePointWorksheet
	@MsODataId NVARCHAR(MAX),
	@MsId NVARCHAR(MAX),
	@Name NVARCHAR(MAX),
	@Position INT,
	@Visibility NVARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the SharePointSite table
    INSERT INTO ms.SharePointWorksheet (CreatedDatetime, ModifiedDatetime, [MsODataId], [MsId], [Name], [Position], [Visibility])
    VALUES (@Now, @Now, @MsODataId, @MsId, @Name, @Position, @Visibility);

    COMMIT;
END


EXEC CreateMsSharePointWorksheet
	@MsODataId = '/sites(''imviokguifqdnyjvkb9idegwrhi.sharepoint.com%2C17981139-624e-48b0-b1ca-36a21ab8e963%2C1ae020ca-f72c-4665-98df-5a4a7b397436'')/drive/items(''017ZKYN57RHILAEB2UNJD3OOZWEQ7X4Q5Z'')/workbook/worksheets(%27%7B2E248848-EA5A-4153-B412-738524EBC991%7D%27)',
	@MsId = '{2E248848-EA5A-4153-B412-738524EBC991}',
	@Name = 'DETAILS',
	@Position = 1,
	@Visibility = 'Visible'







DROP PROCEDURE IF EXISTS ReadMsSharePointWorksheet;

CREATE PROCEDURE ReadMsSharePointWorksheet
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
		[MsODataId],
		[MsId],
		[Name],
		[Position],
		[Visibility]
	FROM ms.SharePointWorksheet;

    COMMIT;
END

EXEC ReadMsSharePointWorksheet;





DROP PROCEDURE IF EXISTS ReadMsSharePointWorksheetById;

CREATE PROCEDURE ReadMsSharePointWorksheetById
    @Id INT
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
		[MsODataId],
		[MsId],
		[Name],
		[Position],
		[Visibility]
	FROM ms.SharePointWorksheet
	WHERE [Id] = @Id;

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadMsSharePointWorksheetByMsId;

CREATE PROCEDURE ReadMsSharePointWorksheetByMsId
    @MsId NVARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [MsODataId],
        [MsId],
        [Name],
        [Position],
        [Visibility]
    FROM ms.SharePointWorksheet
    WHERE [MsId] = @MsId;

    COMMIT;
END



DROP PROCEDURE IF EXISTS UpdateMsSharePointWorksheetById;

CREATE PROCEDURE UpdateMsSharePointWorksheetById
    @Id INT,
	@MsODataId NVARCHAR(MAX),
	@MsId NVARCHAR(MAX),
	@Name NVARCHAR(MAX),
	@Position INT,
	@Visibility NVARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

	DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

	UPDATE ms.SharePointWorksheet
    SET [ModifiedDatetime] = @Now,
        [MsODataId] = @MsODataId,
        [MsId] = @MsId,
        [Name] = @Name,
        [Position] = @Position,
        [Visibility] = @Visibility
    WHERE [Id] = @Id;

    COMMIT;
END






DROP PROCEDURE IF EXISTS DeleteMsSharePointWorksheetById;
	
CREATE PROCEDURE DeleteMsSharePointWorksheetById
    @Id INT
AS
BEGIN
	BEGIN TRANSACTION;
	
	DELETE FROM ms.SharePointWorksheet
	WHERE [Id] = @Id;
	
	COMMIT;
END

EXEC DeleteMsSharePointWorksheetById
	@Id = 1;
