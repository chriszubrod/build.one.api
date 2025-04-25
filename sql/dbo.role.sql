CREATE TABLE Role (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM [Transaction];
SELECT * FROM [Role];


DELETE FROM [Role];






DROP PROCEDURE IF EXISTS CreateRole;

CREATE PROCEDURE CreateRole
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Role table using the TransactionId
    INSERT INTO Role (CreatedDatetime, ModifiedDatetime, [Name], TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Name, @TransactionId);

    COMMIT;
END

EXEC CreateRole @CreatedDatetime = '2025-01-26 00:00:00', @ModifiedDatetime = '2025-01-26 00:00:00', @Name = 'Admin';




DROP PROCEDURE IF EXISTS ReadRoles;

CREATE PROCEDURE ReadRoles
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [TransactionId]
    FROM [Role];

    COMMIT;
END





DROP PROCEDURE IF EXISTS ReadRoleByName;

CREATE PROCEDURE ReadRoleByName
    @Name VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [TransactionId]
    FROM [Role];
    WHERE [Name] = @Name;

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadRoleById;

CREATE PROCEDURE ReadRoleById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [TransactionId]
    FROM [Role];
    WHERE [Id] = @Id;

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadRoleByGuid;

CREATE PROCEDURE ReadRoleByGuid
    @Guid UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [TransactionId]
    FROM [Role]
    WHERE [GUID] = @Guid;

    COMMIT;
END


DROP PROCEDURE IF EXISTS UpdateRoleById;

CREATE PROCEDURE UpdateRoleById
    @Id INT,
    @GUID UNIQUEIDENTIFIER,
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @TransactionId INT
AS
BEGIN
    BEGIN TRANSACTION;

    UPDATE [Role]
    SET
        [ModifiedDatetime] = @ModifiedDatetime,
        [Name] = @Name,
        [TransactionId] = @TransactionId
    WHERE [Id] = @Id;

    COMMIT;
END

