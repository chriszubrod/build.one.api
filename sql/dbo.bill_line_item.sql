CREATE TABLE BillLineItem (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Description] VARCHAR(255) NOT NULL,
    [Units] INT NOT NULL DEFAULT 1,
    [Rate] NUMERIC(18,4) NOT NULL,
    [Amount] DECIMAL(18,2) NOT NULL,
    [IsBillable] BIT NULL,
    [IsBilled] BIT NULL,
    [BillId] INT NOT NULL,
	[SubCostCodeId] INT NOT NULL,
    [ProjectId] INT NOT NULL,
    FOREIGN KEY (EntryId) REFERENCES [Entry](Id),
	FOREIGN KEY (SubCostCodeId) REFERENCES CostCode(id),
    FOREIGN KEY (ProjectId) REFERENCES Project(Id),
);

EXEC sp_rename 'FK__EntryLine__Entry__07AC1A97', 'FK__BillLine__Bill__07AC1A97', 'OBJECT';
EXEC sp_rename 'FK_EntryLineItem_SubCostCode', 'FK_BillLineItem_SubCostCode', 'OBJECT';
EXEC sp_rename 'FK__EntryLine__Proje__09946309', 'FK__BillLine__Proje__09946309', 'OBJECT';

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
WHERE tr.name = 'BillLineItem';

SELECT * FROM BillLineItem;


DROP PROCEDURE IF EXISTS CreateBuildoneBillLineItem;

CREATE PROCEDURE CreateBuildoneBillLineItem
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Description VARCHAR(255),
    @Units INT,
    @Rate NUMERIC(18,4),
    @Amount DECIMAL(18,2),
    @IsBillable BIT,
    @IsBilled BIT,
    @BillId INT,
    @SubCostCodeId INT,
    @ProjectId INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the EntryLineItem table using the TransactionId
    INSERT INTO BillLineItem (CreatedDatetime, ModifiedDatetime, [Description], [Units], [Rate], [Amount], [IsBillable], [IsBilled], [BillId], [SubCostCodeId], [ProjectId])
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Description, @Units, @Rate, @Amount, @IsBillable, @IsBilled, @BillId, @SubCostCodeId, @ProjectId);

    COMMIT;
END



EXEC CreateBuildoneBillLineItem
	@CreatedDatetime = '2025-01-12 21:51:21.513258',
    @ModifiedDatetime = '2025-01-12 21:51:21.513258',
    @Description = 'Plans',
    @Units = 1,
    @Rate = 36.05,
    @Amount = 36.05,
    @IsBillable = 0,
    @IsBilled = 0,
    @EntryId = 7,
    @SubCostCodeId = 1141,
    @ProjectId = 3






DROP PROCEDURE IF EXISTS ReadBuildoneBillLineItemById;

CREATE PROCEDURE ReadBuildoneBillLineItemById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Description],
        [Units],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [BillId],
        [SubCostCodeId],
        [ProjectId]
    FROM BillLineItem
    WHERE [Id] = @Id;

    COMMIT;
END


EXEC ReadBuildoneBillLineItemById
    @Id = 1;



DROP PROCEDURE IF EXISTS ReadBillLineItemByBillId;

CREATE PROCEDURE ReadBillLineItemByBillId
    @BillId INT
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Description],
        [Units],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [BillId],
        [SubCostCodeId],
        [ProjectId]
    FROM BillLineItem
    WHERE [BillId] = @BillId;

    COMMIT;
END


DELETE FROM BillLineItem;

SELECT * FROM BillLineItem;