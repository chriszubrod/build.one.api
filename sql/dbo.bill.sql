CREATE TABLE [Bill] (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Number] NVARCHAR(255) NOT NULL,
    [Date] DATE NOT NULL,
    [Amount] DECIMAL(18,2) NOT NULL,
    [VendorId] INT NOT NULL,
    [AttachmentId] INT,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (VendorId) REFERENCES Vendor(Id),
    FOREIGN KEY (AttachmentId) REFERENCES Attachment(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);


SELECT * FROM [Transaction];
SELECT * FROM [Bill];
SELECT * FROM [BillLineItem];
SELECT * FROM [Attachment];
SELECT * FROM [Vendor];


DELETE FROM [Bill]
WHERE Id IN (42,43,44);

ALTER TABLE [Bill]
DROP COLUMN [AttachmentId];

SELECT 
    fk.name AS ForeignKeyName,
    tp.name AS ParentTable,
    cp.name AS ParentColumn,
    tr.name AS ReferencingTable,
    cr.name AS ReferencingColumn
FROM sys.foreign_keys fk
INNER JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
INNER JOIN sys.tables tp ON fkc.referenced_object_id = tp.object_id
INNER JOIN sys.columns cp ON fkc.referenced_object_id = cp.object_id AND fkc.referenced_column_id = cp.column_id
INNER JOIN sys.tables tr ON fkc.parent_object_id = tr.object_id
INNER JOIN sys.columns cr ON fkc.parent_object_id = cr.object_id AND fkc.parent_column_id = cr.column_id
WHERE fk.name = 'FK__Entry__Attachmen__618671AF';

ALTER TABLE Entry
DROP CONSTRAINT FK__Entry__Attachmen__618671AF;




DROP PROCEDURE IF EXISTS CreateEntryWithLineItemsAndAttachment;

CREATE TYPE EntryLineItemType AS TABLE (
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Description] VARCHAR(255) NOT NULL,
    [Units] INT NOT NULL DEFAULT 1,
    [Rate] NUMERIC(18,4) NOT NULL,
    [Amount] DECIMAL(18,2) NOT NULL,
    [IsBillable] BIT NOT NULL,
    [IsBilled] BIT NOT NULL,
	[SubCostCodeId] INT NOT NULL,
    [ProjectId] INT NOT NULL
);

CREATE TYPE AttachmentType AS TABLE (
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] NVARCHAR(255) NOT NULL,
    [Text] NVARCHAR(MAX) NOT NULL,
    [NumberOfPages] INT NOT NULL,
    [FilePath] NVARCHAR(255) NOT NULL,
    [FileSize] NVARCHAR(255) NOT NULL,
    [FileType] NVARCHAR(255) NOT NULL
);

CREATE PROCEDURE CreateEntryWithLineItemsAndAttachment
	-- Shared
	@CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
	-- Entry
    @Number NVARCHAR(255),
    @Date DATE,
    @Amount DECIMAL(18,2),
    @EntryTypeId INT,
    @VendorId INT,
	-- EntrLineItems
	@EntryLineItems EntryLineItemType READONLY,
	-- Attachment
	@Attachments AttachmentType READONLY
AS
BEGIN
	BEGIN TRANSACTION;

	-- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

	-- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

	-- Insert a new record into the Entry table using the TransactionId
    INSERT INTO Entry (CreatedDatetime, ModifiedDatetime, [Number], [Date], [Amount], EntryTypeId, VendorId, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Number, @Date, @Amount, @EntryTypeId, @VendorId, @TransactionId);

	-- Get the Id of the last inserted record
    DECLARE @EntryId INT;
    SET @EntryId = SCOPE_IDENTITY();

	-- Iterate over the table parameter and insert line items
    INSERT INTO EntryLineItem (CreatedDatetime, ModifiedDatetime, [Description], [Units], [Rate], [Amount], [IsBillable], [IsBilled], EntryId, SubCostCodeId, ProjectId)
    SELECT CONVERT(DATETIMEOFFSET, @CreatedDatetime) AS CreatedDatetime, CONVERT(DATETIMEOFFSET, @ModifiedDatetime) AS ModifiedDatetime, [Description], [Units], [Rate], [Amount], [IsBillable], [IsBilled], @EntryId, [SubCostCodeId], [ProjectId]
    FROM @EntryLineItems;

    -- Iterate over the table parameter and insert attachments
    INSERT INTO Attachment (CreatedDatetime, ModifiedDatetime, [Name], [Text], [NumberOfPages], TransactionId, [FilePath], [FileSize], [FileType])
    SELECT CONVERT(DATETIMEOFFSET, @CreatedDatetime) AS CreatedDatetime, CONVERT(DATETIMEOFFSET, @ModifiedDatetime) AS ModifiedDatetime, [Name], [Text], [NumberOfPages], @TransactionId, [FilePath], [FileSize], [FileType]
    FROM @Attachments;

    COMMIT;
END




DROP PROCEDURE IF EXISTS CreateBuildoneEntry;


CREATE PROCEDURE CreateBuildoneEntry
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Number NVARCHAR(255),
    @Date DATE,
    @Amount DECIMAL(18,2),
    @EntryTypeId INT,
    @VendorId INT,
    @EntryLineItems EntryLineItemType READONLY
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Entry table using the TransactionId
    INSERT INTO Entry (CreatedDatetime, ModifiedDatetime, [Number], [Date], [Amount], EntryTypeId, VendorId, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Number, @Date, @Amount, @EntryTypeId, @VendorId, @TransactionId);

    -- Get the Id of the last inserted record
    DECLARE @EntryId INT;
    SET @EntryId = SCOPE_IDENTITY();

    -- Iterate over the table parameter and insert line items
    INSERT INTO EntryLineItem (CreatedDatetime, ModifiedDatetime, [Description], [Units], [Rate], [Amount], [IsBillable], [IsBilled], EntryId, SubCostCodeId, ProjectId)
    SELECT CONVERT(DATETIMEOFFSET, @CreatedDatetime) AS CreatedDatetime, CONVERT(DATETIMEOFFSET, @ModifiedDatetime) AS ModifiedDatetime, [Description], [Units], [Rate], [Amount], [IsBillable], [IsBilled], @EntryId, [SubCostCodeId], [ProjectId]
    FROM @EntryLineItems;

    COMMIT;
END


DECLARE @EntryLineItems EntryLineItemType;
INSERT INTO @EntryLineItems VALUES
	('2025-01-12 22:08:04.478549','2025-01-12 22:08:04.478549','Plans',1,36.05,36.05,0,0,1141,3);
EXEC CreateBuildoneEntry
	@CreatedDatetime = '2025-01-12 22:08:04.478549',
    @ModifiedDatetime = '2025-01-12 22:08:04.478549',
    @Number = '25-1024',
    @Date = '01/03/2025',
    @Amount = '36.05',
    @EntryTypeId = 2,
    @VendorId = 117,
    @EntryLineItems = @EntryLineItems





DROP PROCEDURE IF EXISTS ReadEntries;

CREATE PROCEDURE ReadEntries
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Date],
        [Amount],
        [EntryTypeId],
        [VendorId],
        [AttachmentId],
        [TransactionId]
    FROM [Entry];

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadEntriesByEntryTypeId;

CREATE PROCEDURE ReadEntriesByEntryTypeId
	@EntryTypeId INT
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Date],
        [Amount],
        [EntryTypeId],
        [VendorId],
        [AttachmentId],
        [TransactionId]
    FROM [Entry]
	WHERE [EntryTypeId]=@EntryTypeId;

    COMMIT;
END

EXEC ReadEntriesByEntryTypeId @EntryTypeId=2;



DROP PROCEDURE IF EXISTS ReadEntryByNumber;

CREATE PROCEDURE ReadEntryByNumber
    @Number NVARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Date],
        [Amount],
        [EntryTypeId],
        [VendorId],
        [AttachmentId],
        [TransactionId]
    FROM Entry
    WHERE [Number] = @Number;

	COMMIT;
END



DROP PROCEDURE IF EXISTS ReadEntryByGuid;

CREATE PROCEDURE ReadEntryByGuid
    @Guid NVARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Date],
        [Amount],
        [EntryTypeId],
        [VendorId],
        [AttachmentId],
        [TransactionId]
    FROM Entry
    WHERE [GUID] = @Guid;

    COMMIT;
END






DELETE FROM dbo.[EntryLineItem];
DELETE FROM dbo.[Entry];


SELECT * FROM dbo.[Entry];



