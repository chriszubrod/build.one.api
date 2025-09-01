CREATE TABLE CertificateOfInsurance (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [CertificateTypeId] INT NOT NULL,
    [PolicyNumber] VARCHAR(MAX) NOT NULL,
    [PolicyEffDate] DATE NOT NULL,
    [PolicyExpDate] DATE NOT NULL,
    [CertificateOfInsuranceAttachmentId] INT NOT NULL,
    [VendorId] INT NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (CertificateTypeId) REFERENCES CertificateType(Id),
    FOREIGN KEY (CertificateOfInsuranceAttachmentId) REFERENCES CertificateOfInsuranceAttachment(Id),
    FOREIGN KEY (VendorId) REFERENCES Vendor(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM [Transaction];
SELECT * FROM CertificateOfInsurance;




DROP PROCEDURE IF EXISTS CreateCertificateOfInsurance;

CREATE PROCEDURE CreateCertificateOfInsurance
    @CertificateTypeId INT,
    @PolicyNumber VARCHAR(MAX),
    @PolicyEffDate DATE,
    @PolicyExpDate DATE,
    @CertificateOfInsuranceAttachmentId INT,
    @VendorId INT
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

    -- Insert a new record into the CertificateOfInsurance table using the TransactionId
    INSERT INTO CertificateOfInsurance (
        CreatedDatetime,
        ModifiedDatetime,
        CertificateTypeId,
        PolicyNumber,
        PolicyEffDate,
        PolicyExpDate,
        CertificateOfInsuranceAttachmentId,
        VendorId,
        TransactionId
    )
    VALUES (
        @Now,
        @Now,
        @CertificateTypeId,
        @PolicyNumber,
        @PolicyEffDate,
        @PolicyExpDate,
        @CertificateOfInsuranceAttachmentId,
        @VendorId,
        @TransactionId
    );

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadCertificateOfInsurances;

CREATE PROCEDURE ReadCertificateOfInsurances
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [CertificateTypeId],
        [PolicyNumber],
        [PolicyEffDate],
        [PolicyExpDate],
        [CertificateOfInsuranceAttachmentId],
        [VendorId],
        [TransactionId]
    FROM CertificateOfInsurance;

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadCertificateOfInsuranceById;

CREATE PROCEDURE ReadCertificateOfInsuranceById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [CertificateTypeId],
        [PolicyNumber],
        [PolicyEffDate],
        [PolicyExpDate],
        [CertificateOfInsuranceAttachmentId],
        [VendorId],
        [TransactionId]
    FROM CertificateOfInsurance
    WHERE [Id] = @Id;

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadCertificateOfInsuranceByGUID;

CREATE PROCEDURE ReadCertificateOfInsuranceByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [CertificateTypeId],
        [PolicyNumber],
        [PolicyEffDate],
        [PolicyExpDate],
        [CertificateOfInsuranceAttachmentId],
        [VendorId],
        [TransactionId]
    FROM CertificateOfInsurance
    WHERE [GUID] = @GUID;

    COMMIT;
END


DROP PROCEDURE IF EXISTS UpdateCertificateOfInsuranceById;

CREATE PROCEDURE UpdateCertificateOfInsuranceById
    @Id INT,
    @CertificateTypeId INT,
    @PolicyNumber VARCHAR(MAX),
    @PolicyEffDate DATE,
    @PolicyExpDate DATE,
    @CertificateOfInsuranceAttachmentId INT,
    @VendorId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    UPDATE CertificateOfInsurance
    SET
        [ModifiedDatetime] = @Now,
        [CertificateTypeId] = @CertificateTypeId,
        [PolicyNumber] = @PolicyNumber,
        [PolicyEffDate] = @PolicyEffDate,
        [PolicyExpDate] = @PolicyExpDate,
        [CertificateOfInsuranceAttachmentId] = @CertificateOfInsuranceAttachmentId,
        [VendorId] = @VendorId
    WHERE [Id] = @Id;

    COMMIT;
END


DROP PROCEDURE IF EXISTS DeleteCertificateOfInsurance;

CREATE PROCEDURE DeleteCertificateOfInsurance
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM CertificateOfInsurance
    WHERE [Id] = @Id;

    COMMIT;
END

-- Migration snippet: add columns and FKs on an existing table
-- Run this only if your CertificateOfInsurance table does not yet have these columns
-- and constraints. Commented to avoid accidental re-runs.
--
-- ALTER TABLE dbo.CertificateOfInsurance
-- ADD
--     CertificateTypeId INT NOT NULL,
--     PolicyNumber VARCHAR(MAX) NOT NULL,
--     PolicyEffDate DATE NOT NULL,
--     PolicyExpDate DATE NOT NULL,
--     CertificateOfInsuranceAttachmentId INT NOT NULL,
--     VendorId INT NOT NULL;
--
-- ALTER TABLE dbo.CertificateOfInsurance
-- ADD CONSTRAINT FK_CertificateOfInsurance_CertificateType FOREIGN KEY (CertificateTypeId) REFERENCES dbo.CertificateType(Id),
--     CONSTRAINT FK_CertificateOfInsurance_Attachment FOREIGN KEY (CertificateOfInsuranceAttachmentId) REFERENCES dbo.CertificateOfInsuranceAttachment(Id),
--     CONSTRAINT FK_CertificateOfInsurance_Vendor FOREIGN KEY (VendorId) REFERENCES dbo.Vendor(Id);
