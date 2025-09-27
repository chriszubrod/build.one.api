CREATE TABLE dbo.Certificate
(
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [CertificateTypeId] INT NOT NULL,
    [PolicyNumber] VARCHAR(MAX) NOT NULL,
    [PolicyEffDate] DATE NOT NULL,
    [PolicyExpDate] DATE NOT NULL,
    [CertificateAttachmentId] INT NOT NULL,
    [VendorId] INT NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (CertificateTypeId) REFERENCES CertificateType(Id),
    FOREIGN KEY (CertificateAttachmentId) REFERENCES CertificateAttachment(Id),
    FOREIGN KEY (VendorId) REFERENCES Vendor(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT *
FROM [Transaction];
SELECT *
FROM CertificateOfInsurance;




DROP PROCEDURE IF EXISTS CreateCertificate;

CREATE PROCEDURE CreateCertificate
    @CertificateTypeId INT,
    @PolicyNumber VARCHAR(MAX),
    @PolicyEffDate DATE,
    @PolicyExpDate DATE,
    @CertificateAttachmentId INT,
    @VendorId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction]
        (CreatedDatetime, ModifiedDatetime)
    VALUES
        (@Now, @Now);

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Certificate table using the TransactionId
    INSERT INTO Certificate
        (
        CreatedDatetime,
        ModifiedDatetime,
        CertificateTypeId,
        PolicyNumber,
        PolicyEffDate,
        PolicyExpDate,
        CertificateAttachmentId,
        VendorId,
        TransactionId
        )
    VALUES
        (
            @Now,
            @Now,
            @CertificateTypeId,
            @PolicyNumber,
            @PolicyEffDate,
            @PolicyExpDate,
            @CertificateAttachmentId,
            @VendorId,
            @TransactionId
    );

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadCertificates;

CREATE PROCEDURE ReadCertificates
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
        [CertificateAttachmentId],
        [VendorId],
        [TransactionId]
    FROM Certificate;

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadCertificateById;

CREATE PROCEDURE ReadCertificateById
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
        [CertificateAttachmentId],
        [VendorId],
        [TransactionId]
    FROM Certificate
    WHERE [Id] = @Id;

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadCertificateByGUID;

CREATE PROCEDURE ReadCertificateByGUID
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
        [CertificateAttachmentId],
        [VendorId],
        [TransactionId]
    FROM Certificate
    WHERE [GUID] = @GUID;

    COMMIT;
END


DROP PROCEDURE IF EXISTS UpdateCertificateById;

CREATE PROCEDURE UpdateCertificateById
    @Id INT,
    @CertificateTypeId INT,
    @PolicyNumber VARCHAR(MAX),
    @PolicyEffDate DATE,
    @PolicyExpDate DATE,
    @CertificateAttachmentId INT,
    @VendorId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    UPDATE Certificate
    SET
        [ModifiedDatetime] = @Now,
        [CertificateTypeId] = @CertificateTypeId,
        [PolicyNumber] = @PolicyNumber,
        [PolicyEffDate] = @PolicyEffDate,
        [PolicyExpDate] = @PolicyExpDate,
        [CertificateAttachmentId] = @CertificateAttachmentId,
        [VendorId] = @VendorId
    WHERE [Id] = @Id;

    COMMIT;
END


DROP PROCEDURE IF EXISTS DeleteCertificate;

CREATE PROCEDURE DeleteCertificate
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM Certificate
    WHERE [Id] = @Id;

    COMMIT;
END

-- Migration snippet: if renaming from CertificateOfInsurance
-- Keep commented to avoid accidental re-runs
-- EXEC sp_rename 'dbo.CertificateOfInsurance', 'Certificate';
-- EXEC sp_rename 'dbo.CertificateOfInsurance.CertificateOfInsuranceAttachmentId', 'CertificateAttachmentId', 'COLUMN';
-- ALTER TABLE dbo.Certificate WITH CHECK ADD CONSTRAINT FK_Certificate_Attachment FOREIGN KEY (CertificateAttachmentId) REFERENCES dbo.Attachment(Id);
