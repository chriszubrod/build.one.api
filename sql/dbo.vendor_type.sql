CREATE TABLE VendorType (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [TransactionId] INT NOT NULL
);

SELECT * FROM dbo.[Transaction];
SELECT * FROM dbo.VendorType;






DROP PROCEDURE IF EXISTS CreateVendorType;

CREATE PROCEDURE CreateVendorType
    @Name VARCHAR(255)
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

    -- Insert a new record into the Vendor table using the TransactionId
    INSERT INTO VendorType (CreatedDatetime, ModifiedDatetime, [Name], TransactionId)
    VALUES (@Now, @Now, @Name, @TransactionId);

    COMMIT;
END

EXEC CreateVendorType
@Name = 'Subcontractor';






DROP PROCEDURE IF EXISTS ReadVendorTypes;

CREATE PROCEDURE ReadVendorTypes
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        TransactionId
    FROM VendorType
	ORDER BY [Name];

	COMMIT;
END

EXEC ReadVendorTypes;






DROP PROCEDURE IF EXISTS ReadVendorTypeByName;

CREATE PROCEDURE ReadVendorTypeByName
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
        TransactionId
    FROM VendorType
    WHERE [Name] = @Name;

	COMMIT;
END

EXEC ReadVendorTypeByName
@Name = 'Subcontractor';






DROP PROCEDURE IF EXISTS ReadVendorTypeByID;

CREATE PROCEDURE ReadVendorTypeByID
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
        TransactionId
    FROM VendorType
    WHERE [Id] = @ID;

	COMMIT;
END

EXEC ReadVendorTypeByID
@ID = 1;






DROP PROCEDURE IF EXISTS ReadVendorTypeByGUID;

CREATE PROCEDURE ReadVendorTypeByGUID
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
        TransactionId
    FROM VendorType
    WHERE [GUID] = @GUID;

	COMMIT;
END

EXEC ReadVendorTypeByGUID
@GUID = 'ec977828-2f3c-4967-9d94-2c33b72489e6';






DROP PROCEDURE IF EXISTS UpdateVendorType;

CREATE PROCEDURE UpdateVendorType
    @ID INT,
    @Name VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    UPDATE VendorType
    SET [ModifiedDatetime] = @Now,
        [Name] = @Name
    WHERE [Id] = @ID;

    COMMIT;
END

EXEC UpdateVendorType
@ID = 1,
@Name = 'Subcontractor';


DROP PROCEDURE IF EXISTS DeleteVendorType;

CREATE PROCEDURE DeleteVendorType
    @ID INT
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM VendorType
    WHERE [Id] = @ID;

    COMMIT;
END

EXEC DeleteVendorType
@ID = 1;





SELECT * FROM dbo.VendorType;