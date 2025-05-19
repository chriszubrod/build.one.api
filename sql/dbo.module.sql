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
	@Name = 'Modules',
	@Desc = 'Manage system modules.',
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
    FROM Module
    ORDER BY [Name] ASC;

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





DROP PROCEDURE IF EXISTS ReadModuleByName;

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

EXEC ReadModuleByName
    @Name = 'Projects'


DROP PROCEDURE IF EXISTS ReadModuleBySlug;

CREATE PROCEDURE ReadModuleBySlug
    @Slug VARCHAR(255)
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
    WHERE [Slug] = @Slug;
END



DROP PROCEDURE IF EXISTS UpdateModuleById;

CREATE PROCEDURE UpdateModuleById
    @Id INT,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @Desc VARCHAR(MAX),
    @Slug VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    UPDATE Module
    SET ModifiedDatetime = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [Name] = @Name,
        [Desc] = @Desc,
        Slug = @Slug
    WHERE [Id] = @Id;

    COMMIT;
END

EXEC UpdateModuleById
    @Id = 4,
    @ModifiedDatetime = '2025-05-18 03:09:00',
    @Name = 'Bills',
    @Desc = 'Create and edit bills.',
    @Slug = '/bills'








SELECT * FROM Module;

UPDATE Module
SET Slug='/entry/bills'
WHERE [Id]=4;
