CREATE TABLE Attachment (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Size] BIGINT NULL,
    [Type] VARCHAR(100) NULL,
	[Content] VARBINARY(MAX) NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

ALTER TABLE Attachment
DROP COLUMN [WrOfPages];

ALTER TABLE Attachment
ADD [FileContent] VARBINARY(MAX) NULL;


SELECT * FROM [Transaction];
SELECT * FROM Attachment;

DROP PROCEDURE CreateAttachment;

CREATE PROCEDURE CreateAttachment
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Name VARCHAR(255),
    @Text VARCHAR(MAX),
    @NumberOfPages INT,
	@FileSize VARCHAR(255),
	@FileType VARCHAR(255),
	@FilePath VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Attachment table using the TransactionId
    INSERT INTO Attachment (CreatedDatetime, ModifiedDatetime, [Name], [Text], NumberOfPages, TransactionId, FilePath, FileSize, FileType)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Name, @Text, @NumberOfPages, @TransactionId, @FilePath, @FileSize, @FileType);

    COMMIT;
END

DROP PROCEDURE ReadAttachmentById;

CREATE PROCEDURE ReadAttachmentById
    @Id INT
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [Text],
        [NumberOfPages],
        [TransactionId],
		[FilePath],
		[FileSize],
		[FileType]
    FROM Attachment
    WHERE [Id] = @Id;
END




UPDATE [Attachment]
SET [Name]='TB3 - Gs Framing - 601370 - Framing Labor - 13.2 - $10,000.00 - 2-5-2025.pdf'
WHERE [Id]=1;