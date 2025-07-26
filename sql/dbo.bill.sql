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
SELECT * FROM [BilLLineItemAttachment];

DELETE FROM [Bill]
WHERE [Id] > 64;



DROP PROCEDURE IF EXISTS CreateBillWithLineItemsAndAttachments;


CREATE TYPE BillLineItemType AS TABLE (
    [RowKey] UNIQUEIDENTIFIER NOT NULL,
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

CREATE TYPE BillLineItemAttachmentType AS TABLE (
    [LineItemRowKey] UNIQUEIDENTIFIER NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] NVARCHAR(255) NOT NULL,
    [Size] BIGINT NOT NULL,
    [Type] NVARCHAR(100) NOT NULL,
    [Content] VARBINARY(MAX) NOT NULL
);


CREATE PROCEDURE CreateBillWithLineItemsAndAttachments
    -- Shared
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    -- Bill
    @Number NVARCHAR(255),
    @Date DATE,
    @Amount DECIMAL(18,2),
    @VendorId INT,
    -- BillLineItems
    @BillLineItems BillLineItemType READONLY,
    -- Attachments
    @BillLineItemAttachments BillLineItemAttachmentType READONLY
AS
BEGIN
    BEGIN TRANSACTION;

    -- Step 1: Insert Transaction record
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (
        CONVERT(DATETIMEOFFSET, @CreatedDatetime),
        CONVERT(DATETIMEOFFSET, @ModifiedDatetime)
    );

    DECLARE @TransactionId INT = SCOPE_IDENTITY();

    -- Step 2: Insert Bill record
    INSERT INTO Bill (CreatedDatetime, ModifiedDatetime, [Number], [Date], [Amount], VendorId, TransactionId)
    VALUES (
        CONVERT(DATETIMEOFFSET, @CreatedDatetime),
        CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        @Number, @Date, @Amount, @VendorId, @TransactionId
    );

    DECLARE @BillId INT = SCOPE_IDENTITY();

    -- Step 3: Create temp table to map RowKey -> BillLineItemId
    DECLARE @LineItemMap TABLE (
        RowKey UNIQUEIDENTIFIER NOT NULL,
        BillLineItemId INT NOT NULL
    );

    -- Step 4: Insert BillLineItems and populate map
    -- First insert all line items
    INSERT INTO BillLineItem (
        CreatedDatetime, ModifiedDatetime, [Description], [Units], [Rate], [Amount],
        [IsBillable], [IsBilled], [BillId], [SubCostCodeId], [ProjectId]
    )
    SELECT
        CONVERT(DATETIMEOFFSET, @CreatedDatetime),
        CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        i.Description,
        i.Units,
        i.Rate,
        i.Amount,
        i.IsBillable,
        i.IsBilled,
        @BillId,
        i.SubCostCodeId,
        i.ProjectId
    FROM @BillLineItems AS i;

    -- Then populate the map table
    INSERT INTO @LineItemMap (RowKey, BillLineItemId)
    SELECT 
        i.RowKey,
        bl.Id
    FROM @BillLineItems i
    INNER JOIN BillLineItem bl ON 
        bl.CreatedDatetime = CONVERT(DATETIMEOFFSET, @CreatedDatetime) AND
        bl.ModifiedDatetime = CONVERT(DATETIMEOFFSET, @ModifiedDatetime) AND
        bl.Description = i.Description AND
        bl.Units = i.Units AND
        bl.Rate = i.Rate AND
        bl.Amount = i.Amount AND
        bl.IsBillable = i.IsBillable AND
        bl.IsBilled = i.IsBilled AND
        bl.BillId = @BillId AND
        bl.SubCostCodeId = i.SubCostCodeId AND
        bl.ProjectId = i.ProjectId;

    -- Step 5: Insert Attachments using the map
    INSERT INTO BillLineItemAttachment (
        CreatedDatetime, ModifiedDatetime, [Name], [Size], [Type], [Content], BillLineItemId
    )
    SELECT
        CONVERT(DATETIMEOFFSET, a.CreatedDatetime),
        CONVERT(DATETIMEOFFSET, a.ModifiedDatetime),
        a.Name,
        a.Size,
        a.Type,
        CONVERT(VARBINARY(MAX), a.Content, 1),
        m.BillLineItemId
    FROM @BillLineItemAttachments a
    INNER JOIN @LineItemMap m ON a.LineItemRowKey = m.RowKey;

    COMMIT;
END;








DROP PROCEDURE IF EXISTS ReadBills;

CREATE PROCEDURE ReadBills
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
        [VendorId],
        [TransactionId]
    FROM [Bill];

    COMMIT;
END





DROP PROCEDURE IF EXISTS ReadBillByNumber;

CREATE PROCEDURE ReadBillByNumber
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
        [VendorId],
        [TransactionId]
    FROM Bill
    WHERE [Number] = @Number;

	COMMIT;
END



DROP PROCEDURE IF EXISTS ReadBillByGuid;

CREATE PROCEDURE ReadBillByGuid
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
        [VendorId],
        [TransactionId]
    FROM Bill
    WHERE [GUID] = @Guid;

    COMMIT;
END


DROP PROCEDURE IF EXISTS UpdateBill;

CREATE PROCEDURE UpdateBill
    @Id INT,
    @Number NVARCHAR(255),
    @Date DATE,
    @Amount DECIMAL(18,2),
    @VendorId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    UPDATE Bill
    SET
        [Number] = @Number,
        [Date] = @Date,
        [Amount] = @Amount,
        [VendorId] = @VendorId,
        [ModifiedDatetime] = @Now
    WHERE [Id] = @Id;

    COMMIT;
END







DELETE FROM Bill;