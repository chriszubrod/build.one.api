CREATE TABLE PaymentTerm (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Value] NVARCHAR(255) NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM [Transaction];
SELECT * FROM PaymentTerm;

CREATE PROCEDURE CreatePaymentTerm
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @Value NVARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the PaymentTerm table using the TransactionId
    INSERT INTO PaymentTerm (CreatedDatetime, ModifiedDatetime, [Name], [Value], TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Name, @Value, @TransactionId);

    COMMIT;
END

CREATE PROCEDURE ReadPaymentTerms
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [Value],
        [TransactionId]
    FROM PaymentTerm;
END

CREATE PROCEDURE ReadPaymentTermByName
    @Name VARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [Value],
        [TransactionId]
    FROM PaymentTerm
    WHERE [Name] = @Name;
END


DROP PROCEDURE IF EXISTS ReadPaymentTermByGUID;

CREATE PROCEDURE ReadPaymentTermByGUID
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
        [Value],
        [TransactionId]
    FROM PaymentTerm
    WHERE [GUID] = @GUID;

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadPaymentTermByID;

CREATE PROCEDURE ReadPaymentTermByID
    @ID INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [Value],
        [TransactionId]
    FROM PaymentTerm
    WHERE [Id] = @ID;

    COMMIT;
END




DROP PROCEDURE IF EXISTS UpdatePaymentTermById;

CREATE PROCEDURE UpdatePaymentTermById
    @Id INT,
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @Value NVARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Update the PaymentTerm record
    UPDATE PaymentTerm
    SET CreatedDatetime = CONVERT(DATETIMEOFFSET, @CreatedDatetime),
        ModifiedDatetime = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [Name] = @Name,
        [Value] = @Value
    WHERE Id = @Id;

    COMMIT;
END





DELETE FROM PaymentTerm;
SELECT * FROM PaymentTerm;
