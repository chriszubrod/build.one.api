CREATE TABLE Module (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Desc] VARCHAR(MAX),
    [Slug] VARCHAR(MAX) NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM [Transaction];
SELECT * FROM Module;



CREATE PROCEDURE CreateModule
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @Desc VARCHAR(MAX),
    @Slug VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Module table using the TransactionId
    INSERT INTO Module (CreatedDatetime, ModifiedDatetime, [Name], [Desc], Slug, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Name, @Desc, @Slug, @TransactionId);

    COMMIT;
END


EXEC CreateModule
	@CreatedDatetime = '2025-02-08 00:00:00',
	@ModifiedDatetime = '2025-02-08 00:00:00',
	@Name = 'Bills',
	@Desc = 'Create and edit bills.',
	@Slug = '/bills'


DROP PROCEDURE IF EXISTS ReadModules;

CREATE PROCEDURE ReadModules
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT 
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [Desc],
        [Slug],
        [TransactionId]
    FROM Module;

    COMMIT;
END





DROP PROCEDURE IF EXISTS ReadModuleByGUID;

CREATE PROCEDURE ReadModuleByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [Desc],
        [Slug],
        [TransactionId]
    FROM Module
    WHERE [GUID] = @GUID;

    COMMIT;
END







CREATE PROCEDURE ReadModuleByName
    @Name VARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)),
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)),
        [Name],
        [Desc],
        [Slug],
        [TransactionId]
    FROM Module
    WHERE [Name] = @Name;
END





UPDATE Module
SET [Name] = 'bill', [Desc] = 'Manage bills', [Slug] = '/entry/bills'
WHERE [Id] = 4;





SELECT * FROM Module;

UPDATE Module
SET Slug='/entry/bills'
WHERE [Id]=4;
