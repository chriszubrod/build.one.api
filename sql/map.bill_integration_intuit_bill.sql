CREATE TABLE map.BillIntuitBill (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [BillId] INT NOT NULL,
    [IntuitBillId] INT NOT NULL
);


DROP TABLE map.BillIntuitBill;



DROP PROCEDURE IF EXISTS CreateMapBillInuitBill;

CREATE PROCEDURE CreateMapBillInuitBill
    @BillId INT,
    @IntuitBillId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the BillIntuitBill table
    INSERT INTO map.BillIntuitBill (CreatedDatetime, ModifiedDatetime, BillId, IntuitBillId)
    VALUES (@Now, @Now, @BillId, @IntuitBillId);

    COMMIT TRANSACTION;
END;

EXEC CreateBillInuitBill
    1,
    1;



DROP PROCEDURE IF EXISTS ReadMapBillIntuitBills;

CREATE PROCEDURE ReadMapBillIntuitBills
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [BillId],
        [IntuitBillId]
    FROM map.BillIntuitBill;

    COMMIT TRANSACTION;
END;

EXEC ReadMapBillIntuitBills;



DROP PROCEDURE IF EXISTS ReadMapBillIntuitBillByGUID;

CREATE PROCEDURE ReadMapBillIntuitBillByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [BillId],
        [IntuitBillId]
    FROM map.BillIntuitBill
    WHERE [GUID] = @GUID;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS ReadMapBillIntuitBillByBillId;

CREATE PROCEDURE ReadMapBillIntuitBillByBillId
    @BillId INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [BillId],
        [IntuitBillId]
    FROM map.BillIntuitBill
    WHERE [BillId] = @BillId;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS UpdateMapBillIntuitBillById;

CREATE PROCEDURE UpdateMapBillIntuitBillById
    @Id INT,
    @BillId INT,
    @IntuitBillId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Update the record in the BillIntuitBill table
    UPDATE map.BillIntuitBill
    SET
        ModifiedDatetime = @Now,
        BillId = @BillId,
        IntuitBillId = @IntuitBillId
    WHERE Id = @Id;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS DeleteMapBillIntuitBillById;

CREATE PROCEDURE DeleteMapBillIntuitBillById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Delete the record from the BillIntuitBill table by Id
    DELETE FROM map.BillIntuitBill
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
