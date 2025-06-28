CREATE TABLE map.AttachmentSharePointFile (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [BillLineItemAttachmentId] INT NOT NULL,
    [MsSharePointFileId] INT NOT NULL
);


DROP TABLE map.AttachmentSharePointFile;



DROP PROCEDURE IF EXISTS CreateAttachmentSharePointFile;

CREATE PROCEDURE CreateAttachmentSharePointFile
    @BillLineItemAttachmentId INT,
    @MsSharePointFileId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the AttachmentSharePointFile table
    INSERT INTO map.AttachmentSharePointFile (CreatedDatetime, ModifiedDatetime, BillLineItemAttachmentId, MsSharePointFileId)
    VALUES (@Now, @Now, @BillLineItemAttachmentId, @MsSharePointFileId);

    COMMIT TRANSACTION;
END;

EXEC CreateAttachmentSharePointFile
    '2025-06-20T00:00:00.000',
    '2025-06-20T00:00:00.000',
    4,
    2;



DROP PROCEDURE IF EXISTS ReadAttachmentSharePointFile;

CREATE PROCEDURE ReadAttachmentSharePointFile
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [BillLineItemAttachmentId],
        [MsSharePointFileId]
    FROM map.AttachmentSharePointFile;
    COMMIT TRANSACTION;
END;

EXEC ReadAttachmentSharePointFile;



DROP PROCEDURE IF EXISTS ReadAttachmentSharePointFileByGUID;

CREATE PROCEDURE ReadAttachmentSharePointFileByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [BillLineItemAttachmentId],
        [MsSharePointFileId]
    FROM map.ProjectSharePointFolder
    WHERE [GUID] = @GUID;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS ReadAttachmentSharePointFileByAttachmentId;

CREATE PROCEDURE ReadAttachmentSharePointFileByAttachmentId
    @BillLineItemAttachmentid INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [BillLineItemAttachmentId],
        [MsSharePointFileId]
    FROM map.AttachmentSharePointFile
    WHERE BillLineItemAttachmentid = @BillLineItemAttachmentid;

    COMMIT TRANSACTION;
END;

EXEC ReadAttachmentSharePointFileByAttachmentId 
    @BillLineItemAttachmentid = 11;




DROP PROCEDURE IF EXISTS ReadAttachmentSharePointFileByAttachmentIdSharePointFileId;

CREATE PROCEDURE ReadAttachmentSharePointFileByAttachmentIdSharePointFileId
    @AttachmentId INT,
    @SharePointFileId INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [BillLineItemAttachmentId],
        [MsSharePointFileId]
    FROM map.AttachmentSharePointFile
    WHERE BillLineItemAttachmentid = @AttachmentId 
      AND MsSharePointFileId = @SharePointFileId;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS UpdateAttachmentSharePointFileById;

CREATE PROCEDURE UpdateAttachmentSharePointFileById
    @Id INT,
    @ModifiedDatetime DATETIMEOFFSET,
    @BillLineItemAttachmentId INT,
    @MsSharePointFileId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Update the record in the AttachmentSharePointFile table
    UPDATE map.AttachmentSharePointFile
    SET
        ModifiedDatetime = @Now,
        BillLineItemAttachmentId = @BillLineItemAttachmentId,
        MsSharePointFileId = @MsSharePointFileId
    WHERE Id = @Id;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS DeleteAttachmentSharePointFileById;

CREATE PROCEDURE DeleteAttachmentSharePointFileById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Delete the record from the AttachmentSharePointFile table by Id
    DELETE FROM map.AttachmentSharePointFile
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
