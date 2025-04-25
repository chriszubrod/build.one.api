CREATE TABLE Contact (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [FirstName] VARCHAR(255),
    [LastName] VARCHAR(255),
    [Email] VARCHAR(255) NOT NULL,
    [Phone] VARCHAR(255),
    [TransactionId] INT NOT NULL,
	[CustomerId] INT NULL,
	[UserId] INT NULL
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
	FOREIGN KEY (CustomerId) REFERENCES [Customer](Id)
	FOREIGN KEY (UserId) REFERENCES [User](Id)
);




SELECT * FROM [Transaction];
SELECT * FROM Contact;


DELETE FROM dbo.Contact;


DROP PROCEDURE IF EXISTS CreateContact;

CREATE PROCEDURE CreateContact
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @FirstName VARCHAR(255),
    @LastName VARCHAR(255),
    @Email VARCHAR(255),
    @Phone VARCHAR(255),
	@CustomerId INT,
	@UserId INT
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
    INSERT INTO Contact (CreatedDatetime, ModifiedDatetime, FirstName, LastName, Email, Phone, TransactionId, CustomerId, UserId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @FirstName, @LastName, @Email, @Phone, @TransactionId, @CustomerId, @UserId);

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadContacts;

CREATE PROCEDURE ReadContacts
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [FirstName],
        [LastName],
        [Email],
        [Phone],
        [TransactionId],
        [CustomerId],
        [UserId]
    FROM Contact;

    COMMIT;
END






DROP PROCEDURE IF EXISTS ReadContactByEmail;


CREATE PROCEDURE ReadContactByEmail
    @Email VARCHAR(255)
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [FirstName],
        [LastName],
        [Email],
        [Phone],
        [TransactionId],
		[CustomerId],
		[UserId]
    FROM Contact
    WHERE [Email] = @Email;

	COMMIT;
END



DROP PROCEDURE IF EXISTS ReadContactByGUID;


CREATE PROCEDURE ReadContactByGUID
    @GUID VARCHAR(255)
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [FirstName],
        [LastName],
        [Email],
        [Phone],
        [TransactionId],
		[CustomerId],
		[UserId]
    FROM Contact
    WHERE [GUID] = @GUID;

	COMMIT;
END









DROP PROCEDURE IF EXISTS ReadContactByUserId;

CREATE PROCEDURE ReadContactByUserId
    @UserId INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [FirstName],
        [LastName],
        [Email],
        [Phone],
        [TransactionId],
        [CustomerId],
        [UserId]
    FROM Contact
    WHERE [UserId] = @UserId;

    COMMIT;
END



DROP PROCEDURE IF EXISTS UpdateContactByUserId;

CREATE PROCEDURE UpdateContactByUserId
    @ContactId INT,
    @ContactGuid UNIQUEIDENTIFIER,
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @FirstName VARCHAR(255),
    @LastName VARCHAR(255),
    @Email VARCHAR(255),
    @Phone VARCHAR(255),
    @TransactionId INT,
    @CustomerId INT,
    @UserId INT
AS
BEGIN
    BEGIN TRANSACTION;

    UPDATE Contact
    SET
        [ModifiedDatetime] = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [FirstName] = @FirstName,
        [LastName] = @LastName,
        [Email] = @Email,
        [Phone] = @Phone
    WHERE [UserId] = @UserId;

    COMMIT;
END

