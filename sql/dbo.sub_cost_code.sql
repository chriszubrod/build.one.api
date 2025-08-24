CREATE TABLE SubCostCode (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Number] NUMERIC(18, 4) NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Description] VARCHAR(MAX),
	[CostCodeId] INT NOT NULL,
    [TransactionId] INT NOT NULL,
    [IntuitItemId] VARCHAR(MAX) NULL,
	FOREIGN KEY (CostCodeId) REFERENCES CostCode(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

ALTER TABLE SubCostCode
ALTER COLUMN [Number] NUMERIC(18, 2) NOT NULL;

SELECT * FROM [Transaction];
SELECT * FROM CostCode;
SELECT * FROM SubCostCode;





DROP PROCEDURE IF EXISTS CreateSubCostCode;

CREATE PROCEDURE CreateSubCostCode
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Number NUMERIC(18, 4),
    @Name VARCHAR(255),
    @Description VARCHAR(MAX),
    @CostCodeGUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Get the Id of the CostCode record
    DECLARE @CostCodeId INT;
    SET @CostCodeId = (SELECT Id FROM CostCode WHERE GUID = @CostCodeGUID);

    -- Insert a new record into the SubCostCode table using the TransactionId and CostCodeId
    INSERT INTO SubCostCode (CreatedDatetime, ModifiedDatetime, [Number], [Name], [Description], CostCodeId, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Number, @Name, @Description, @CostCodeId, @TransactionId);

    COMMIT;
END





DROP PROCEDURE IF EXISTS ReadSubCostCodes;


CREATE PROCEDURE ReadSubCostCodes
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Name],
        [Description],
        [CostCodeId],
        [TransactionId]
    FROM SubCostCode
    ORDER BY [Number];

	COMMIT;
END

EXEC ReadSubCostCodes;






DROP PROCEDURE IF EXISTS ReadSubCostCodeByName;

CREATE PROCEDURE ReadSubCostCodeByName
    @Name VARCHAR(255)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Name],
        [Description],
        [CostCodeId],
        [TransactionId]
    FROM SubCostCode
    WHERE [Name] = @Name;

	COMMIT;
END




DROP PROCEDURE IF EXISTS UpdateSubCostCode;

CREATE PROCEDURE UpdateSubCostCode
    @Id INT,
    @Number NUMERIC(18, 4),
    @Name VARCHAR(255),
    @Description VARCHAR(MAX),
    @CostCodeID INT,
    @TransactionId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    UPDATE SubCostCode
    SET [Number] = @Number,
        [Name] = @Name,
        [Description] = @Description,
        [CostCodeId] = @CostCodeID,
        [TransactionId] = @TransactionId,
        [ModifiedDatetime] = @Now
    WHERE [Id] = @Id;

    COMMIT;
END












DROP PROCEDURE IF EXISTS ReadBuildoneSubCostCodeByIntuitItemId;

CREATE PROCEDURE ReadBuildoneSubCostCodeByIntuitItemId
    @IntuitItemId VARCHAR(MAX)
AS
BEGIN
    SELECT 
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Name],
        [Description],
        [CostCodeId],
        [TransactionId],
        [IntuitItemId]
    FROM SubCostCode
    WHERE IntuitItemId = @IntuitItemId;
END





DROP PROCEDURE IF EXISTS ReadSubCostCodeByGUID;

CREATE PROCEDURE ReadSubCostCodeByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Name],
        [Description],
        [CostCodeId],
        [TransactionId]
    FROM SubCostCode
    WHERE [GUID] = @GUID;

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadSubCostCodeByID;

CREATE PROCEDURE ReadSubCostCodeByID
    @ID INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Name],
        [Description],
        [CostCodeId],
        [TransactionId]
    FROM SubCostCode
    WHERE [ID] = @ID;

    COMMIT;
END

EXEC ReadSubCostCodeByID
    @ID = 1237; -- Example ID, replace with actual ID as needed








DROP PROCEDURE IF EXISTS ReadIntuitSubItems;

CREATE PROCEDURE ReadIntuitSubItems
AS
BEGIN
    SELECT
        ItemGUID,
		RealmId,
		[Id],
		[Name],
		FullyQualifiedName
    FROM intuit.Item
	WHERE IsActive=1 AND IsSubItem=1
	ORDER BY [Name];
END




DELETE FROM dbo.SubCostCode;



DROP PROCEDURE IF EXISTS CreateBuildoneSubCostCodeByIdIntuitSync;

CREATE PROCEDURE CreateBuildoneSubCostCodeByIdIntuitSync
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Number NUMERIC(18, 4),
    @Name VARCHAR(255),
    @Description VARCHAR(MAX),
    @IntuitItemId VARCHAR(MAX),
    @ParentRefValue VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Get the Id of the CostCode record
    DECLARE @CostCodeId INT;
    SET @CostCodeId = (SELECT Id FROM CostCode WHERE IntuitItemId = @ParentRefValue);

    -- Insert a new record into the SubCostCode table using the TransactionId and CostCodeId
    INSERT INTO SubCostCode (CreatedDatetime, ModifiedDatetime, [Number], [Name], [Description], CostCodeId, TransactionId, IntuitItemId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Number, @Name, @Description, @CostCodeId, @TransactionId, @IntuitItemId);

    COMMIT;
END


DROP PROCEDURE IF EXISTS UpdateBuildoneSubCostCodeByIdIntuitSync;

CREATE PROCEDURE UpdateBuildoneSubCostCodeByIdIntuitSync
    @SubCostCodeId INT,
    @ModifiedDatetime DATETIMEOFFSET,
    @Number NUMERIC(18, 4),
    @Name VARCHAR(255),
    @Description VARCHAR(MAX),
    @TransactionId INT,
    @IntuitItemId VARCHAR(MAX),
    @ParentRefValue VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Get the Id of the CostCode record
    DECLARE @CostCodeId INT;
    SET @CostCodeId = (SELECT Id FROM CostCode WHERE IntuitItemId = @ParentRefValue);

    UPDATE SubCostCode
    SET ModifiedDatetime = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [Number] = @Number,
        [Name] = @Name,
        [Description] = @Description,
        [CostCodeId] = @CostCodeId,
        [TransactionId] = @TransactionId,
        [IntuitItemId] = @IntuitItemId
    WHERE [Id] = @SubCostCodeId;

	COMMIT;
END

