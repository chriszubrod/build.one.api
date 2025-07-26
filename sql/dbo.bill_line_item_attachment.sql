CREATE TABLE BillLineItemAttachment (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Size] BIGINT NULL,
    [Type] VARCHAR(100) NULL,
	[Content] VARBINARY(MAX) NULL,
    [BillLineItemId] INT NOT NULL,
    FOREIGN KEY (BillLineItemId) REFERENCES [BillLineItem](Id)
);





DROP PROCEDURE IF EXISTS CreateBuildoneBillLineItemAttachment;

CREATE PROCEDURE CreateBuildoneBillLineItemAttachment
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @Size BIGINT,
    @Type VARCHAR(100),
    @Content VARBINARY(MAX),
    @BillLineItemId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the EntryLineItem table using the TransactionId
    INSERT INTO BillLineItemAttachment (CreatedDatetime, ModifiedDatetime, [Name], [Size], [Type], [Content], [BillLineItemId])
    VALUES (CONVERT(DATETIMEOFFSET, @Now), CONVERT(DATETIMEOFFSET, @Now), @Name, @Size, @Type, @Content, @BillLineItemId);

    COMMIT;
END



EXEC CreateBuildoneEntryLineItemAttachment
	@CreatedDatetime = '2025-01-12 21:51:21.513258',
    @ModifiedDatetime = '2025-01-12 21:51:21.513258',
    @Name = 'Plans',
    @Size = 123456,
    @Type = 'application/pdf',
    @Content = 0x1234567890ABCDEF,
    @BillLineItemId = 1
--
--
--
--




DROP PROCEDURE IF EXISTS ReadBillLineItemAttachments;

CREATE PROCEDURE ReadBillLineItemAttachments
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
        [Content],
        [BillLineItemId]
    FROM BillLineItemAttachment;

    COMMIT;
END

EXEC ReadBillLineItemAttachments;






DROP PROCEDURE IF EXISTS ReadBuildoneBillLineItemAttachmentById;

CREATE PROCEDURE ReadBuildoneBillLineItemAttachmentById
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
        [Content],
        [BillLineItemId]
    FROM BillLineItemAttachment
    WHERE [Id] = @Id;

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadBillLineItemAttachmentByBillLineItemId;

CREATE PROCEDURE ReadBillLineItemAttachmentByBillLineItemId
    @BillLineItemId INT
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
        [Content],
        [BillLineItemId]
    FROM BillLineItemAttachment
    WHERE [BillLineItemId] = @BillLineItemId;
    COMMIT;
END


EXEC ReadBillLineItemAttachmentByBillLineItemId
    @BillLineItemId = 61



DROP PROCEDURE IF EXISTS UpdateBillLineItemAttachment;

CREATE PROCEDURE UpdateBillLineItemAttachment
    @Id INT,
    @Name VARCHAR(255),
    @Size BIGINT,
    @Type VARCHAR(100),
    @Content VARBINARY(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    UPDATE BillLineItemAttachment
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Size] = @Size,
        [Type] = @Type,
        [Content] = @Content
    WHERE [Id] = @Id;

    COMMIT;
END










UPDATE BillLineItemAttachment
SET
    [Name] = 'TB3 - Walker Lumber & Hardware - 030536 - Lumber & Hardware - 13.1000 - $769.93 - 4-29-2025.pdf'
WHERE [Id] = 12;

--
--


DELETE FROM BillLineItemAttachment;

SELECT * FROM BillLineItemAttachment;
