CREATE TABLE Company (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL
);




SELECT * FROM [Transaction];
SELECT * FROM Company;


DELETE FROM dbo.Company;


DROP PROCEDURE IF EXISTS CreateCompany;

CREATE PROCEDURE CreateCompany
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255)
AS
BEGIN

    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Company table using the TransactionId
    INSERT INTO Company (CreatedDatetime, ModifiedDatetime, [Name])
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Name);

    -- Get the Output parameter to the new Company ID
    DECLARE @CompanyId INT;
    SET @CompanyId = SCOPE_IDENTITY();

    COMMIT;

    -- Return the new Company
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name]
    FROM Company
    WHERE [Id] = @CompanyId;

END;


DELETE FROM dbo.Company;
EXEC CreateCompany
    @CreatedDatetime = '2023-10-01 12:00:00 +00:00',
    @ModifiedDatetime = '2023-10-01 12:00:00 +00:00',
    @Name = 'Test Company';



DROP PROCEDURE IF EXISTS ReadCompany;

CREATE PROCEDURE ReadCompany
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name]
    FROM Company;

    COMMIT;
END






DROP PROCEDURE IF EXISTS ReadCompanyByGUID;


CREATE PROCEDURE ReadCompanyByGUID
    @GUID VARCHAR(255)
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name]
    FROM Company
    WHERE [GUID] = @GUID;

	COMMIT;
END






DROP PROCEDURE IF EXISTS UpdateCompanyById;

CREATE PROCEDURE UpdateCompanyById
    @Id INT,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    UPDATE Company
    SET
        [ModifiedDatetime] = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [Name] = @Name
    WHERE [Id] = @Id;

    COMMIT;
END;




DROP PROCEDURE IF EXISTS DeleteCompanyById;

CREATE PROCEDURE DeleteCompanyById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Delete the Company record
    DELETE FROM Company WHERE [Id] = @Id;

    -- Return the deleted Company
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name]
    FROM Company
    WHERE [Id] = @Id;

    COMMIT;
END
