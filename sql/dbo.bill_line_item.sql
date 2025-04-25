CREATE TABLE EntryLineItem (
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
    [EntryId] INT NOT NULL,
	[SubCostCodeId] INT NOT NULL,
    [ProjectId] INT NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (EntryId) REFERENCES [Entry](Id),
	FOREIGN KEY (SubCostCodeId) REFERENCES CostCode(id),
    FOREIGN KEY (ProjectId) REFERENCES Project(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);


SELECT * FROM EntryLineItem;


DROP PROCEDURE IF EXISTS CreateBuildoneEntryLineItem;

CREATE PROCEDURE CreateBuildoneEntryLineItem
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Description VARCHAR(255),
    @Units INT,
    @Rate NUMERIC(18,4),
    @Amount DECIMAL(18,2),
    @IsBillable BIT,
    @IsBilled BIT,
    @EntryId INT,
    @SubCostCodeId INT,
    @ProjectId INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the EntryLineItem table using the TransactionId
    INSERT INTO EntryLineItem (CreatedDatetime, ModifiedDatetime, [Description], [Units], [Rate], [Amount], [IsBillable], [IsBilled], [EntryId], [SubCostCodeId], [ProjectId])
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Description, @Units, @Rate, @Amount, @IsBillable, @IsBilled, @EntryId, @SubCostCodeId, @ProjectId);

    COMMIT;
END



EXEC CreateBuildoneEntryLineItem
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






DROP PROCEDURE IF EXISTS ReadBuildoneEntryLineItemById;

CREATE PROCEDURE ReadBuildoneEntryLineItemById
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
        [EntryId],
        [SubCostCodeId],
        [ProjectId],
        [TransactionId]
    FROM EntryLineItem
    WHERE [Id] = @Id;

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadEntryLineItemByEntryId;

CREATE PROCEDURE ReadEntryLineItemByEntryId
    @EntryId INT
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
        [EntryId],
        [SubCostCodeId],
        [ProjectId]
    FROM EntryLineItem
    WHERE [EntryId] = @EntryId;

    COMMIT;
END


DELETE FROM EntryLineItem;

SELECT * FROM EntryLineItem;