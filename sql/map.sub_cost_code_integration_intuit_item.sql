CREATE TABLE map.SubCostCodeIntuitItem (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [SubCostCodeId] INT NOT NULL,
    [IntuitItemId] INT NOT NULL
);


DROP TABLE map.SubCostCodeIntuitItem;



DROP PROCEDURE IF EXISTS CreateMapSubCostCodeIntuitItem;

CREATE PROCEDURE CreateMapSubCostCodeIntuitItem
    @SubCostCodeId INT,
    @IntuitItemId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the SubCostCodeIntuitItem table
    INSERT INTO map.SubCostCodeIntuitItem (CreatedDatetime, ModifiedDatetime, SubCostCodeId, IntuitItemId)
    VALUES (@Now, @Now, @SubCostCodeId, @IntuitItemId);

    COMMIT TRANSACTION;
END;

EXEC CreateMapSubCostCodeIntuitItem
    1,
    1;



DROP PROCEDURE IF EXISTS ReadMapSubCostCodeIntuitItems;

CREATE PROCEDURE ReadMapSubCostCodeIntuitItems
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [SubCostCodeId],
        [IntuitItemId]
    FROM map.SubCostCodeIntuitItem;

    COMMIT TRANSACTION;
END;

EXEC ReadMapSubCostCodeIntuitItems;



DROP PROCEDURE IF EXISTS ReadMapSubCostCodeIntuitItemByGUID;

CREATE PROCEDURE ReadMapSubCostCodeIntuitItemByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [SubCostCodeId],
        [IntuitItemId]
    FROM map.SubCostCodeIntuitItem
    WHERE [GUID] = @GUID;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS ReadMapSubCostCodeIntuitItemBySubCostCodeId;

CREATE PROCEDURE ReadMapSubCostCodeIntuitItemBySubCostCodeId
    @SubCostCodeId INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [SubCostCodeId],
        [IntuitItemId]
    FROM map.SubCostCodeIntuitItem
    WHERE [SubCostCodeId] = @SubCostCodeId;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS UpdateMapSubCostCodeIntuitItemById;

CREATE PROCEDURE UpdateMapSubCostCodeIntuitItemById
    @Id INT,
    @SubCostCodeId INT,
    @IntuitItemId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Update the record in the BillIntuitBill table
    UPDATE map.SubCostCodeIntuitItem
    SET
        ModifiedDatetime = @Now,
        SubCostCodeId = @SubCostCodeId,
        IntuitItemId = @IntuitItemId
    WHERE Id = @Id;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS DeleteMapSubCostCodeIntuitItemById;

CREATE PROCEDURE DeleteMapSubCostCodeIntuitItemById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Delete the record from the SubCostCodeIntuitItem table by Id
    DELETE FROM map.SubCostCodeIntuitItem
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



SELECT * INTO #TempSubCostCode FROM dbo.SubCostCode;

INSERT INTO map.SubCostCodeIntuitItem (CreatedDatetime, ModifiedDatetime, SubCostCodeId, IntuitItemId)
SELECT
    SYSDATETIMEOFFSET() AS CreatedDatetime,
    SYSDATETIMEOFFSET() AS ModifiedDatetime,
    [Id] AS SubCostCodeId,
    [IntuitItemId] AS IntuitItemId
FROM #TempSubCostCode
WHERE [IntuitItemId] IS NOT NULL;

ALTER TABLE dbo.SubCostCode
ADD MapSubCostCodeIntuitItemId INT NULL;

UPDATE dbo.SubCostCode
SET MapSubCostCodeIntuitItemId = map.SubCostCodeIntuitItem.[Id]
FROM dbo.SubCostCode
JOIN map.SubCostCodeIntuitItem ON dbo.SubCostCode.[Id] = map.SubCostCodeIntuitItem.[SubCostCodeId];

DROP TABLE #TempSubCostCode;

SELECT * FROM dbo.SubCostCode;
SELECT * FROM map.SubCostCodeIntuitItem;
SELECT * FROM intuit.Item ORDER BY [Id]

ALTER TABLE dbo.SubCostCode
DROP COLUMN IntuitItemId;