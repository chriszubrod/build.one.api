CREATE TABLE Vendor (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Abbreviation] VARCHAR(255) NULL,
    [IsActive] BIT NULL,
    [VendorTypeId] INT NULL,
    [ContactId] INT NULL,
    [AddressId] INT NULL,
    [CertificateId] INT NULL,
    [PaymentTermId] INT NULL,
    [TransactionId] INT NOT NULL,
	[MapVendorIntuitVendorId] VARCHAR(MAX) NULL,
    FOREIGN KEY (ContactId) REFERENCES Contact(Id),
    FOREIGN KEY (AddressId) REFERENCES [Address](Id),
    FOREIGN KEY (CertificateId) REFERENCES Certificate(Id),
    FOREIGN KEY (PaymentTermId) REFERENCES PaymentTerm(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

EXEC sp_rename 'Vendor.Type', 'VendorTypeId', 'COLUMN';
ALTER TABLE Vendor
ALTER COLUMN VendorTypeId INT;

SELECT * FROM dbo.[Transaction];
SELECT * FROM dbo.Vendor;
SELECT * FROM dbo.Contact;
SELECT * FROM dbo.[Address];
SELECT * FROM dbo.Certificate;
SELECT * FROM dbo.PaymentTerm;




DROP PROCEDURE IF EXISTS CreateVendor;

CREATE PROCEDURE CreateVendor
    @Name VARCHAR(255),
    @Abbreviation VARCHAR(255),
    @IsActive BIT,
    @VendorTypeId INT
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
    INSERT INTO Vendor (CreatedDatetime, ModifiedDatetime, [Name], Abbreviation, IsActive, [VendorTypeId])
    VALUES (@Now, @Now, @Name, @Abbreviation, @IsActive, @VendorTypeId);

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadVendors;

CREATE PROCEDURE ReadVendors
AS
BEGIN

    BEGIN TRANSACTION;
    
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        Abbreviation,
        IsActive,
        [VendorTypeId],
        ContactId,
        AddressId,
        CertificateId,
        PaymentTermId,
        TransactionId,
		MapVendorIntuitVendorId
    FROM Vendor
	ORDER BY [Name];

	COMMIT;
END

EXEC ReadVendors;


DROP PROCEDURE IF EXISTS ReadVendorByName;

CREATE PROCEDURE ReadVendorByName
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
        Abbreviation,
        IsActive,
        [VendorTypeId],
        ContactId,
        AddressId,
        CertificateId,
        PaymentTermId,
        TransactionId,
		MapVendorIntuitVendorId
    FROM Vendor
    WHERE [Name] = @Name;

	COMMIT;
END



DROP PROCEDURE IF EXISTS ReadVendorByID;

CREATE PROCEDURE ReadVendorByID
    @ID VARCHAR(255)
AS
BEGIN

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        Abbreviation,
        IsActive,
        [VendorTypeId],
        ContactId,
        AddressId,
        CertificateId,
        PaymentTermId,
        TransactionId,
		MapVendorIntuitVendorId
    FROM Vendor
    WHERE [Id] = @ID;

    COMMIT;
END

EXEC ReadVendorByID
    @ID = '834';


DROP PROCEDURE IF EXISTS ReadVendorByGUID;

CREATE PROCEDURE ReadVendorByGUID
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
        Abbreviation,
        IsActive,
        [VendorTypeId],
        ContactId,
        AddressId,
        CertificateId,
        PaymentTermId,
        TransactionId,
		MapVendorIntuitVendorId
    FROM Vendor
    WHERE [GUID] = @GUID;

	COMMIT;
END


DROP PROCEDURE IF EXISTS UpdateVendor;

CREATE PROCEDURE UpdateVendor
    @ID VARCHAR(255),
    @Name VARCHAR(255),
    @Abbreviation VARCHAR(255),
    @IsActive BIT,
    @VendorTypeId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Update the Vendor table
    UPDATE Vendor
    SET ModifiedDatetime = @Now,
        [Name] = @Name,
        Abbreviation = @Abbreviation,
        IsActive = @IsActive,
        [VendorTypeId] = @VendorTypeId
    WHERE [Id] = @ID;

    COMMIT;
END


DROP PROCEDURE IF EXISTS DeleteVendor;

CREATE PROCEDURE DeleteVendor
    @ID INT
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM Vendor
    WHERE [Id] = @ID;

    COMMIT;
END
