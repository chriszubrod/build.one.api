CREATE TABLE [Attachment]
(
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] NVARCHAR(255) NOT NULL,
    [Size] BIGINT NULL,
    [Type] NVARCHAR(100) NULL,
    StorageAccount NVARCHAR(255) NULL,
    ContainerName VARCHAR(255) NULL,
    BlobName NVARCHAR(255) NULL,
    ETag NVARCHAR(255) NULL,
    Sha256Hash CHAR(64) NULL,
    Tags NVARCHAR(MAX) NULL,
    Metadata NVARCHAR(MAX) NULL
);



DROP PROCEDURE IF EXISTS CreateAttachment;

CREATE PROCEDURE CreateAttachment
    @Name VARCHAR(255),
    @Size BIGINT,
    @Type VARCHAR(100),
    @StorageAccount VARCHAR(255),
    @ContainerName VARCHAR(255),
    @BlobName VARCHAR(255),
    @ETag VARCHAR(255),
    @Sha256Hash CHAR(64),
    @Tags NVARCHAR(MAX),
    @Metadata NVARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert Transaction record
    INSERT INTO Attachment (CreatedDatetime, ModifiedDatetime)
    VALUES (@Now, @Now);

    DECLARE @TransactionId INT = SCOPE_IDENTITY();

    -- Insert a new record into the Attachment table using the TransactionId
    INSERT INTO Attachment (CreatedDatetime, ModifiedDatetime, [Name], [Size], [Type], [StorageAccount], [ContainerName], [BlobName], [ETag], [Sha256Hash], [Tags], [Metadata])
    VALUES (@Now, @Now, @Name, @Size, @Type, @StorageAccount, @ContainerName, @BlobName, @ETag, @Sha256Hash, @Tags, @Metadata);

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadAttachments;

CREATE PROCEDURE ReadAttachments
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
        [StorageAccount],
        [ContainerName],
        [BlobName],
        [ETag],
        [Sha256Hash],
        [Tags],
        [Metadata]
    FROM Attachment;

    COMMIT;
END

EXEC ReadAttachments;






DROP PROCEDURE IF EXISTS ReadAttachmentById;

CREATE PROCEDURE ReadAttachmentById
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
        [StorageAccount],
        [ContainerName],
        [BlobName],
        [ETag],
        [Sha256Hash],
        [Tags],
        [Metadata]
    FROM Attachment
    WHERE [Id] = @Id;

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadAttachmentByGuid;

CREATE PROCEDURE ReadAttachmentByGuid
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
        [StorageAccount],
        [ContainerName],
        [BlobName],
        [ETag],
        [Sha256Hash],
        [Tags],
        [Metadata]
    FROM Attachment
    WHERE [GUID] = @GUID;

    COMMIT;
END


EXEC ReadAttachmentByGuid
    @GUID = '61';



DROP PROCEDURE IF EXISTS UpdateAttachment;

CREATE PROCEDURE UpdateAttachment
    @Id INT,
    @Name VARCHAR(255),
    @Size BIGINT,
    @Type VARCHAR(100),
    @StorageAccount VARCHAR(255),
    @ContainerName VARCHAR(255),
    @BlobName VARCHAR(255),
    @ETag VARCHAR(255),
    @Sha256Hash CHAR(64),
    @Tags NVARCHAR(MAX),
    @Metadata NVARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    UPDATE Attachment
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Size] = @Size,
        [Type] = @Type,
        [StorageAccount] = @StorageAccount,
        [ContainerName] = @ContainerName,
        [BlobName] = @BlobName,
        [ETag] = @ETag,
        [Sha256Hash] = @Sha256Hash,
        [Tags] = @Tags,
        [Metadata] = @Metadata
    WHERE [Id] = @Id;

    COMMIT;
END




DROP PROCEDURE IF EXISTS DeleteAttachmentById;

CREATE PROCEDURE DeleteAttachmentById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM Attachment WHERE [Id] = @Id;

    COMMIT;
END




