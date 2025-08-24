CREATE TABLE Vendor (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Abbreviation] VARCHAR(255) NULL,
    [TaxIdNumber] VARCHAR(9),
    [IsActive] BIT NULL,
    [Type] VARCHAR(255) NULL,
    [ContactId] INT NULL,
    [AddressId] INT NULL,
    [CertificateOfInsuranceId] INT NULL,
    [PaymentTermId] INT NULL,
    [TransactionId] INT NOT NULL,
	[MapVendorIntuitVendorId] VARCHAR(MAX) NULL,
    FOREIGN KEY (ContactId) REFERENCES Contact(Id),
    FOREIGN KEY (AddressId) REFERENCES [Address](Id),
    FOREIGN KEY (CertificateOfInsuranceId) REFERENCES CertificateOfInsurance(Id),
    FOREIGN KEY (PaymentTermId) REFERENCES PaymentTerm(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

ALTER TABLE dbo.Vendor
ALTER COLUMN IsActive BIT NULL;


SELECT * FROM dbo.[Transaction];
SELECT * FROM dbo.Vendor;
SELECT * FROM dbo.Contact;
SELECT * FROM dbo.[Address];
SELECT * FROM dbo.CertificateOfInsurance;
SELECT * FROM dbo.PaymentTerm;





DROP PROCEDURE IF EXISTS CreateVendor;

CREATE PROCEDURE CreateVendor
    @Name VARCHAR(255),
    @Abbreviation VARCHAR(255),
    @TaxIdNumber VARCHAR(9),
    @IsActive BIT,
    @Type VARCHAR(255),
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
    -- Certificate Of Insurance
    -- Payment Term
    @PaymentTermGUID UNIQUEIDENTIFIER
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

    -- Insert a new record into the Contact table using the TransactionId
    INSERT INTO Contact (CreatedDatetime, ModifiedDatetime, FirstName, LastName, Email, Phone, TransactionId)
    VALUES (@Now, @Now, @FirstName, @LastName, @Email, @Phone, @TransactionId);

    -- Get the Id of the last inserted record
    DECLARE @ContactId INT;
    SET @ContactId = SCOPE_IDENTITY();

    -- Insert a new record into the Address table using the TransactionId
    INSERT INTO [Address] (CreatedDatetime, ModifiedDatetime, StreetOne, StreetTwo, City, [State], Zip, TransactionId)
    VALUES (@Now, @Now, @StreetOne, @StreetTwo, @City, @State, @Zip, @TransactionId);

    -- Get the Id of the last inserted record
    DECLARE @AddressId INT;
    SET @AddressId = SCOPE_IDENTITY();

    -- Certificate Of Insurance
    --@CertificateOfInsuranceId INT

    -- Get the Id of the Payment Term using the PaymentTermGUID
    DECLARE @PaymentTermId INT;
    SELECT @PaymentTermId = Id FROM PaymentTerm WHERE GUID = @PaymentTermGUID;

    -- Insert a new record into the Vendor table using the TransactionId
    INSERT INTO Vendor (CreatedDatetime, ModifiedDatetime, [Name], Abbreviation, TaxIdNumber, IsActive, [Type], ContactId, AddressId, PaymentTermId, TransactionId)
    VALUES (@Now, @Now, @Name, @Abbreviation, @TaxIdNumber, @IsActive, @Type, @ContactId, @AddressId, @PaymentTermId, @TransactionId);

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
        TaxIdNumber,
        IsActive,
        [Type],
        ContactId,
        AddressId,
        CertificateOfInsuranceId,
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
        TaxIdNumber,
        IsActive,
        [Type],
        ContactId,
        AddressId,
        CertificateOfInsuranceId,
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
        TaxIdNumber,
        IsActive,
        [Type],
        ContactId,
        AddressId,
        CertificateOfInsuranceId,
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
        TaxIdNumber,
        IsActive,
        [Type],
        ContactId,
        AddressId,
        CertificateOfInsuranceId,
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
    @TaxIdNumber VARCHAR(9),
    @IsActive BIT,
    @Type VARCHAR(255),
    @ContactId INT,
    @AddressId INT,
    @CertificateOfInsuranceId INT,
    @PaymentTermId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Update the Vendor table
    UPDATE Vendor
    SET ModifiedDatetime = @Now,
        [Name] = @Name,
        Abbreviation = @Abbreviation,
        TaxIdNumber = @TaxIdNumber,
        IsActive = @IsActive,
        [Type] = @Type,
        ContactId = @ContactId,
        AddressId = @AddressId,
        CertificateOfInsuranceId = @CertificateOfInsuranceId,
        PaymentTermId = @PaymentTermId
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
