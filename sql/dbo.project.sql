CREATE TABLE Project (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Abbreviation] VARCHAR(255) NOT NULL,
    [Status] CHAR(1) NOT NULL, --(P)lanning, (D)esign, (B)uild, (C)ompleted
    [CustomerId] INT NOT NULL,
    [TransactionId] INT NOT NULL,
	[mapProjectIntuitCustomer] VARCHAR(MAX) NULL
    FOREIGN KEY (CustomerId) REFERENCES Customer(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM [Transaction];
SELECT * FROM Customer;
SELECT * FROM Project;


EXEC sp_rename 'Project.mapProjectIntuitCustomer', 'mapProjectIntuitCustomerId', 'COLUMN';

UPDATE dbo.Project
SET mapProjectIntuitCustomerId=1
WHERE Id=3;



DROP PROCEDURE IF EXISTS CreateProject;

CREATE PROCEDURE CreateProject
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @Abbreviation VARCHAR(255),
    @Status CHAR(1),
    @CustomerGUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Get the Id of Customer record
    DECLARE @CustomerId INT;
    SET @CustomerId = (SELECT Id FROM Customer WHERE [GUID] = @CustomerGUID);

    -- Insert a new record into the Project table using the TransactionId
    INSERT INTO Project (CreatedDatetime, ModifiedDatetime, [Name], Abbreviation, [Status], CustomerId, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Name, @Abbreviation, @Status, @CustomerId, @TransactionId);

    COMMIT;
END






DROP PROCEDURE IF EXISTS ReadProjects;

CREATE PROCEDURE ReadProjects
AS
BEGIN
    SELECT
        P.[Id],
        P.[GUID],
        CAST(P.[CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST(P.[ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        P.[Name],
        P.[Abbreviation],
        P.[Status],
        P.[CustomerId],
        P.[TransactionId],
		P.[mapProjectIntuitCustomerId],
		C.[Name] AS CustomerName
    FROM Project P
    JOIN Customer C ON P.[CustomerId] = C.[Id];
END




DROP PROCEDURE IF EXISTS ReadProjectByName;

CREATE PROCEDURE ReadProjectByName
    @Name VARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)),
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)),
        [Name],
        [Abbreviation],
        [Status],
        [CustomerId],
        [TransactionId]
    FROM Project
    WHERE [Name] = @Name;
END




DROP PROCEDURE IF EXISTS ReadProjectByID;

CREATE PROCEDURE ReadProjectByID
    @ID INT
AS
BEGIN
	BEGIN TRANSACTION

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [Status],
        [CustomerId],
        [TransactionId],
		[IntuitCustomerId]
    FROM Project
    WHERE [Id] = @ID;

	COMMIT;
END






DROP PROCEDURE IF EXISTS ReadProjectByGUID;

CREATE PROCEDURE ReadProjectByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
	BEGIN TRANSACTION

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [Status],
        [CustomerId],
        [TransactionId],
		[mapProjectIntuitCustomerId]
    FROM Project
    WHERE [GUID] = @GUID;

	COMMIT
END



DROP PROCEDURE IF EXISTS UpdateProjectById;

CREATE PROCEDURE UpdateProjectById
    @Id INT,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @Abbreviation VARCHAR(255),
    @Status CHAR(1),
    @CustomerId INT
AS
BEGIN
    BEGIN TRANSACTION;

    UPDATE Project
    SET [ModifiedDatetime] = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [Name] = @Name,
        [Abbreviation] = @Abbreviation,
        [Status] = @Status,
        [CustomerId] = @CustomerId
    WHERE [Id] = @Id;

    COMMIT;
END


















CREATE PROCEDURE ReadIntuitProjects
AS
BEGIN
    SELECT
        CustomerGUID,
		RealmId,
		[Id],
		DisplayName
    FROM intuit.Customer
	WHERE IsProject=1 AND IsActive=1
	ORDER BY DisplayName;
END

DELETE FROM dbo.Project;


UPDATE dbo.Project
SET IntuitCustomerId='602'
WHERE [GUID]='EEE87BD4-CE42-42CD-A68D-09380514184D';

SELECT
	P.[Id],
	P.[GUID],
	CAST(P.[CreatedDatetime] AS NVARCHAR(MAX)),
	CAST(P.[ModifiedDatetime] AS NVARCHAR(MAX)),
	P.[Name],
	P.[Abbreviation],
	P.[Status],
	P.[CustomerId],
	C.[GUID] AS CustomerGUID,
	C.[Name] AS CustomerName,
	P.[TransactionId]
FROM Project P
JOIN Customer C ON P.[CustomerId] = C.[Id]
WHERE P.[GUID] = 'EEE87BD4-CE42-42CD-A68D-09380514184D';

SELECT * FROM dbo.Project;

SELECT * FROM dbo.Project;

