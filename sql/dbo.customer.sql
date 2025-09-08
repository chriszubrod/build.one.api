CREATE TABLE Customer (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [IsActive] BIT NOT NULL,
    [AddressId] INT,
    [TransactionId] INT NOT NULL,
    [MapCustomerIntuitCustomerId] INT NULL,
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM dbo.[Transaction];
SELECT * FROM dbo.Contact;
SELECT * FROM dbo.[Address];
SELECT * FROM dbo.Customer;


ALTER TABLE Customer
DROP CONSTRAINT FK__Customer__Addres__673F4B05;


ALTER TABLE Customer
DROP COLUMN IntuitCustomerId;





DROP PROCEDURE IF EXISTS CreateCustomer;

CREATE PROCEDURE CreateCustomer
    @Name VARCHAR(255),
    @IsActive BIT
AS
BEGIN
	BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (@Now, @Now);

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Customer table using the TransactionId
    INSERT INTO Customer (CreatedDatetime, ModifiedDatetime, [Name], IsActive, TransactionId)
    VALUES (@Now, @Now, @Name, @IsActive, @TransactionId);

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadCustomers;

CREATE PROCEDURE ReadCustomers
AS
BEGIN

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [IsActive],
        [TransactionId],
        [MapCustomerIntuitCustomerId]
    FROM Customer;
    
    COMMIT;
END

EXEC ReadCustomers;




DROP PROCEDURE IF EXISTS ReadCustomerById;

CREATE PROCEDURE ReadCustomerById
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
        [IsActive],
        [TransactionId],
		[MapCustomerIntuitCustomerId]
    FROM Customer
    WHERE [Id] = @Id;

    COMMIT;
END

EXEC ReadCustomerById
    @Id = 2;





DROP PROCEDURE IF EXISTS ReadCustomerByGUID;

CREATE PROCEDURE ReadCustomerByGUID
    @GUID VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [IsActive],
        [TransactionId],
		[MapCustomerIntuitCustomerId]
    FROM Customer
    WHERE [GUID] = @GUID;

    COMMIT;
END

EXEC ReadCustomerByGUID
    @GUID = '9091382E-39E9-4788-947E-2AD48103A6A4';



DROP PROCEDURE IF EXISTS ReadCustomerByName;

CREATE PROCEDURE ReadCustomerByName
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
        [IsActive],
        [TransactionId],
		[MapCustomerIntuitCustomerId]
    FROM Customer
    WHERE [Name] = @Name;

    COMMIT;
END

EXEC ReadCustomerByName
    @Name = 'Josh and Katy Whalen';



DROP PROCEDURE IF EXISTS UpdateCustomerById;

CREATE PROCEDURE UpdateCustomerById
	@Id INT,
    @Name VARCHAR(255),
    @IsActive BIT,
    @MapCustomerIntuitCustomerId INT
AS
BEGIN
	BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Update ModifiedDatetime, Name, IsActive and IntuitCustomerId in the Customer table by Id
    UPDATE Customer
    SET ModifiedDatetime = @Now,
        [Name] = @Name,
        IsActive = @IsActive,
        MapCustomerIntuitCustomerId = @MapCustomerIntuitCustomerId
    WHERE Id = @Id;

    COMMIT;
END






DROP PROCEDURE IF EXISTS DeleteCompanyById;

CREATE PROCEDURE DeleteCustomerById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM Customer
    WHERE Id = @Id;

    COMMIT;
END


DELETE FROM dbo.Customer;


SELECT *
FROM Customer
WHERE [Name] = 'Test';

UPDATE dbo.Customer
SET IntuitCustomerId='601'
WHERE [GUID]='9091382E-39E9-4788-947E-2AD48103A6A4';

SELECT * FROM dbo.Customer;