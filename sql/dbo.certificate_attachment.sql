CREATE TABLE CertificateAttachment (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Size] BIGINT NULL,
    [Type] VARCHAR(100) NULL,
    [ContainerName] VARCHAR(255) NOT NULL,
    [BlobName] VARCHAR(1024) NOT NULL,
    [BlobUrl] VARCHAR(2048) NULL,
    [CertificateId] INT NOT NULL,
    FOREIGN KEY (CertificateId) REFERENCES [Certificate](Id)
);


-- Create
DROP PROCEDURE IF EXISTS CreateCertificateAttachment;
CREATE PROCEDURE CreateCertificateAttachment
    @Name VARCHAR(255),
    @Size BIGINT,
    @Type VARCHAR(100),
    @ContainerName VARCHAR(255),
    @BlobName VARCHAR(1024),
    @BlobUrl VARCHAR(2048),
    @CertificateId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    INSERT INTO CertificateAttachment (
        CreatedDatetime,
        ModifiedDatetime,
        [Name],
        [Size],
        [Type],
        [ContainerName],
        [BlobName],
        [BlobUrl],
        CertificateId
    )
    VALUES (
        @Now,
        @Now,
        @Name,
        @Size,
        @Type,
        @ContainerName,
        @BlobName,
        @BlobUrl,
        @CertificateId
    );

    COMMIT;
END


-- Read all
DROP PROCEDURE IF EXISTS ReadCertificateAttachments;
CREATE PROCEDURE ReadCertificateAttachments
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [Size],
        [Type],
        [ContainerName],
        [BlobName],
        [BlobUrl],
        [CertificateId]
    FROM CertificateAttachment;
    COMMIT;
END


-- Read by Id
DROP PROCEDURE IF EXISTS ReadCertificateAttachmentById;
CREATE PROCEDURE ReadCertificateAttachmentById
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
        [Size],
        [Type],
        [ContainerName],
        [BlobName],
        [BlobUrl],
        [CertificateId]
    FROM CertificateAttachment
    WHERE [Id] = @Id;
    COMMIT;
END


-- Read by GUID
DROP PROCEDURE IF EXISTS ReadCertificateAttachmentByGUID;
CREATE PROCEDURE ReadCertificateAttachmentByGUID
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
        [Size],
        [Type],
        [ContainerName],
        [BlobName],
        [BlobUrl],
        [CertificateId]
    FROM CertificateAttachment
    WHERE [GUID] = @GUID;
    COMMIT;
END


-- Read by CertificateId
DROP PROCEDURE IF EXISTS ReadCertificateAttachmentByCertificateId;
CREATE PROCEDURE ReadCertificateAttachmentByCertificateId
    @CertificateId INT
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [Size],
        [Type],
        [ContainerName],
        [BlobName],
        [BlobUrl],
        [CertificateId]
    FROM CertificateAttachment
    WHERE [CertificateId] = @CertificateId;
    COMMIT;
END


-- Update by Id
DROP PROCEDURE IF EXISTS UpdateCertificateAttachment;
CREATE PROCEDURE UpdateCertificateAttachment
    @Id INT,
    @Name VARCHAR(255),
    @Size BIGINT,
    @Type VARCHAR(100),
    @BlobUrl VARCHAR(2048)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    UPDATE CertificateAttachment
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Size] = @Size,
        [Type] = @Type,
        [BlobUrl] = @BlobUrl
    WHERE [Id] = @Id;

    COMMIT;
END


-- Delete by Id
DROP PROCEDURE IF EXISTS DeleteCertificateAttachment;
CREATE PROCEDURE DeleteCertificateAttachment
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;
    DELETE FROM CertificateAttachment WHERE [Id] = @Id;
    COMMIT;
END
