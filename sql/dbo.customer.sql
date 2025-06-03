CREATE TABLE Customer (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [IsActive] BIT NOT NULL,
    [AddressId] INT,
    [TransactionId] INT NOT NULL,
    [IntuitCustomerId] VARCHAR(MAX) NULL,
    FOREIGN KEY (ContactId) REFERENCES Contact(Id),
    FOREIGN KEY (AddressId) REFERENCES [Address](Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM dbo.[Transaction];
SELECT * FROM dbo.Contact;
SELECT * FROM dbo.[Address];
SELECT * FROM dbo.Customer;


ALTER TABLE Customer
DROP CONSTRAINT FK__Customer__Contac__664B26CC;


ALTER TABLE Customer
DROP COLUMN ContactId;





DROP PROCEDURE IF EXISTS CreateCustomer;

CREATE PROCEDURE CreateCustomer
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @CustomerName VARCHAR(255),
    @IsActive BIT,
    -- Contact
    @FirstName VARCHAR(255),
    @LastName VARCHAR(255),
    @Email VARCHAR(255),
    @Phone VARCHAR(255),
    -- Address
    @StreetOne VARCHAR(255),
    @StreetTwo VARCHAR(255),
    @City VARCHAR(255),
    @State VARCHAR(2),
    @Zip VARCHAR(10),
	-- Intuit
	@IntuitCustomerId VARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Contact table using the TransactionId
    INSERT INTO Contact (CreatedDatetime, ModifiedDatetime, FirstName, LastName, Email, Phone, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @FirstName, @LastName, @Email, @Phone, @TransactionId);

    -- Get the Id of the last inserted record
    DECLARE @ContactId INT;
    SET @ContactId = SCOPE_IDENTITY();

    -- Insert a new record into the Address table using the TransactionId
    INSERT INTO [Address] (CreatedDatetime, ModifiedDatetime, StreetOne, StreetTwo, City, [State], Zip, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @StreetOne, @StreetTwo, @City, @State, @Zip, @TransactionId);

    -- Get the Id of the last inserted record
    DECLARE @AddressId INT;
    SET @AddressId = SCOPE_IDENTITY();

    -- Insert a new record into the Customer table using the TransactionId
    INSERT INTO Customer (CreatedDatetime, ModifiedDatetime, [Name], IsActive, ContactId, AddressId, TransactionId, IntuitCustomerId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @CustomerName, @IsActive, @ContactId, @AddressId, @TransactionId, @IntuitCustomerId);

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadCustomers;

CREATE PROCEDURE ReadCustomers
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [IsActive],
        [AddressId],
        [TransactionId],
		[IntuitCustomerId]
    FROM Customer;
END


DROP PROCEDURE IF EXISTS ReadCustomerById;

CREATE PROCEDURE ReadCustomerById
    @Id INT
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [IsActive],
        [AddressId],
        [TransactionId],
		[IntuitCustomerId]
    FROM Customer
    WHERE [Id] = @Id;
END



DROP PROCEDURE IF EXISTS ReadCustomerByGUID;

CREATE PROCEDURE ReadCustomerByGUID
    @GUID VARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [IsActive],
        [AddressId],
        [TransactionId],
		[IntuitCustomerId]
    FROM Customer
    WHERE [GUID] = @GUID;
END







CREATE PROCEDURE ReadCustomerByName
    @Name VARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)),
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)),
        [Name],
        [IsActive],
        [ContactId],
        [AddressId],
        [TransactionId],
		[IntuitCustomerId]
    FROM Customer
    WHERE [Name] = @Name;
END

CREATE PROCEDURE UpdateCustomerById
	@Id INT,
    @ModifiedDatetime DATETIMEOFFSET,
    @CustomerName VARCHAR(255),
    @IsActive BIT,
	@TransactionId INT,
	-- Intuit
	@IntuitCustomerId VARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

    -- Update ModifiedDatetime in the Transaction table
    UPDATE [Transaction]
    SET ModifiedDatetime = CONVERT(DATETIMEOFFSET, @ModifiedDatetime)
    WHERE Id = @TransactionId;

    -- Update ModifiedDatetime, Name, IsActive and IntuitCustomerId in the Customer table by Id
    UPDATE Customer
    SET ModifiedDatetime = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [Name] = @CustomerName,
        IsActive = @IsActive,
        IntuitCustomerId = @IntuitCustomerId
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