CREATE TABLE EntryType (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Description] VARCHAR(255) NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM [Transaction];
SELECT * FROM EntryType;


DROP PROCEDURE IF EXISTS CreateEntryType;

CREATE PROCEDURE CreateEntryType
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @Description VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (@CreatedDatetime, @ModifiedDatetime);

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the EntryType table using the TransactionId
    INSERT INTO EntryType (CreatedDatetime, ModifiedDatetime, [Name], [Description], TransactionId)
    VALUES (@CreatedDatetime, @ModifiedDatetime, @Name, @Description, @TransactionId);

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadEntryTypeByGUID;

CREATE PROCEDURE ReadEntryTypeByGUID
    @GUID VARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)),
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)),
        [Name],
        [Description],
        [TransactionId]
    FROM EntryType
    WHERE [GUID] = @GUID;
END


DROP PROCEDURE IF EXISTS ReadEntryTypeByID;

CREATE PROCEDURE ReadEntryTypeByID
    @ID VARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [Name],
        [Description],
        [TransactionId]
    FROM EntryType
    WHERE [Id] = @ID;

	COMMIT;
END



DROP PROCEDURE IF EXISTS ReadEntryTypeByName;

CREATE PROCEDURE ReadEntryTypeByName
    @Name VARCHAR(255)
AS
BEGIN
	BEGIN TRANSACTION

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [Description],
        [TransactionId]
    FROM EntryType
    WHERE [Name] = @Name;

	COMMIT
END

EXEC ReadEntryTypeByName
	@Name = 'bill';


UPDATE EntryType
SET [Name]='bill'
WHERE [Id]=2;
