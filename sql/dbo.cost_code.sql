CREATE TABLE CostCode (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Number] NUMERIC(18, 4) NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [Desc] VARCHAR(MAX),
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

ALTER TABLE dbo.CostCode
ADD IntuitItemId VARCHAR(MAX) NULL;



SELECT * FROM [Transaction];
SELECT * FROM CostCode ORDER BY [Number];

DROP PROCEDURE IF EXISTS CreateCostCode;

CREATE PROCEDURE CreateCostCode
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Number NUMERIC(18, 4),
    @Name VARCHAR(255),
    @Desc VARCHAR(MAX),
	@IntuitItemId VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the CostCode table using the TransactionId
    INSERT INTO CostCode (CreatedDatetime, ModifiedDatetime, [Number], [Name], [Desc], TransactionId, IntuitItemId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Number, @Name, @Desc, @TransactionId, @IntuitItemId);

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadCostCodes;

CREATE PROCEDURE ReadCostCodes
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST(CreatedDatetime AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST(ModifiedDatetime AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Name],
        [Desc],
        [TransactionId],
		IntuitItemId
    FROM CostCode;
END




DROP PROCEDURE IF EXISTS ReadCostCodeByName;


CREATE PROCEDURE ReadCostCodeByName
    @Name VARCHAR(255)
AS
BEGIN
    SELECT
		[Id],
		[GUID],
		CAST(CreatedDatetime AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST(ModifiedDatetime AS NVARCHAR(MAX)) AS ModifiedDatetime,
		[Number],
		[Name],
		[Desc],
		[TransactionId],
		IntuitItemId
	FROM CostCode
	WHERE [Name] = @Name;
END



DROP PROCEDURE IF EXISTS ReadBuildoneCostCodeById;

CREATE PROCEDURE ReadCostCodeById
    @Id INT
AS
BEGIN

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST(CreatedDatetime AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST(ModifiedDatetime AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Name],
        [Desc],
        [TransactionId],
        IntuitItemId
    FROM CostCode WHERE Id = @Id;

    COMMIT;
END;







DROP PROCEDURE IF EXISTS ReadCostCodeByGUID;

CREATE PROCEDURE ReadCostCodeByGUID
    @GUID VARCHAR(255)
AS
BEGIN

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST(CreatedDatetime AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST(ModifiedDatetime AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Number],
        [Name],
        [Desc],
        [TransactionId],
        IntuitItemId
    FROM CostCode
    WHERE [GUID] = @GUID;

    COMMIT;
END;









DROP PROCEDURE IF EXISTS ReadCostCodeByIntuitItemId;

CREATE PROCEDURE ReadCostCodeByIntuitItemId
    @IntuitItemId VARCHAR(MAX)
AS
BEGIN
    SELECT
        [Id],
		[GUID],
		CAST(CreatedDatetime AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST(ModifiedDatetime AS NVARCHAR(MAX)) AS ModifiedDatetime,
		[Number],
		[Name],
		[Desc],
		[TransactionId],
		IntuitItemId
    FROM CostCode
    WHERE IntuitItemId = @IntuitItemId;
END



DELETE FROM dbo.CostCode;






































DROP PROCEDURE IF EXISTS CreateBuildoneCostCodeByIdIntuitSync;

CREATE PROCEDURE CreateBuildoneCostCodeByIdIntuitSync
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @Number NUMERIC(18, 4),
    @Name VARCHAR(255),
    @Desc VARCHAR(MAX),
	@IntuitItemId VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the CostCode table using the TransactionId
    INSERT INTO CostCode (CreatedDatetime, ModifiedDatetime, [Number], [Name], [Desc], TransactionId, IntuitItemId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @Number, @Name, @Desc, @TransactionId, @IntuitItemId);

    COMMIT;
END


DROP PROCEDURE IF EXISTS UpdateBuildoneCostCodeByIdIntuitSync;

CREATE PROCEDURE UpdateBuildoneCostCodeByIdIntuitSync
    @Id INT,
    @ModifiedDatetime DATETIMEOFFSET,
    @Number NUMERIC(18, 4),
    @Name VARCHAR(255),
    @Desc VARCHAR(MAX),
    @TransactionId INT,
	@IntuitItemId VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    UPDATE CostCode
    SET ModifiedDatetime = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        [Number] = @Number,
        [Name] = @Name,
        [Desc] = @Desc,
        TransactionId = @TransactionId,
        IntuitItemId = @IntuitItemId
    WHERE Id = @Id;

    COMMIT;
END
