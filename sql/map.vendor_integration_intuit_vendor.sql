CREATE TABLE map.VendorIntuitVendor (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [VendorId] INT NOT NULL,
    [IntuitVendorId] INT NOT NULL
);


DROP TABLE map.VendorIntuitVendor;



DROP PROCEDURE IF EXISTS CreateMapVendorIntuitVendor;

CREATE PROCEDURE CreateMapVendorIntuitVendor
    @VendorId INT,
    @IntuitVendorId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the VendorIntuitVendor table
    INSERT INTO map.VendorIntuitVendor (CreatedDatetime, ModifiedDatetime, VendorId, IntuitVendorId)
    VALUES (@Now, @Now, @VendorId, @IntuitVendorId);

    COMMIT TRANSACTION;
END;

EXEC CreateMapVendorIntuitVendor
    1,
    1;



DROP PROCEDURE IF EXISTS ReadMapVendorIntuitVendors;

CREATE PROCEDURE ReadMapVendorIntuitVendors
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [VendorId],
        [IntuitVendorId]
    FROM map.VendorIntuitVendor;

    COMMIT TRANSACTION;
END;

EXEC ReadMapVendorIntuitVendors;



DROP PROCEDURE IF EXISTS ReadMapVendorIntuitVendorByGUID;

CREATE PROCEDURE ReadMapVendorIntuitVendorByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [VendorId],
        [IntuitVendorId]
    FROM map.VendorIntuitVendor
    WHERE [GUID] = @GUID;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS ReadMapVendorIntuitVendorByVendorId;

CREATE PROCEDURE ReadMapVendorIntuitVendorByVendorId
    @VendorId INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [VendorId],
        [IntuitVendorId]
    FROM map.VendorIntuitVendor
    WHERE [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS UpdateMapVendorIntuitVendorById;

CREATE PROCEDURE UpdateMapVendorIntuitVendorById
    @Id INT,
    @VendorId INT,
    @IntuitVendorId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Update the record in the BillIntuitBill table
    UPDATE map.VendorIntuitVendor
    SET
        ModifiedDatetime = @Now,
        VendorId = @VendorId,
        IntuitVendorId = @IntuitVendorId
    WHERE Id = @Id;

    COMMIT TRANSACTION;
END;





DROP PROCEDURE IF EXISTS DeleteMapVendorIntuitVendorById;

CREATE PROCEDURE DeleteMapVendorIntuitVendorById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Delete the record from the SubCostCodeIntuitItem table by Id
    DELETE FROM map.VendorIntuitVendor
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



SELECT * INTO #TempVendor FROM dbo.Vendor;

INSERT INTO map.VendorIntuitVendor (CreatedDatetime, ModifiedDatetime, VendorId, IntuitVendorId)
SELECT
    SYSDATETIMEOFFSET() AS CreatedDatetime,
    SYSDATETIMEOFFSET() AS ModifiedDatetime,
    [Id] AS VendorId,
    [IntuitVendorId] AS IntuitVendorId
FROM #TempVendor
WHERE [IntuitVendorId] IS NOT NULL;

ALTER TABLE dbo.Vendor
ADD MapVendorIntuitVendorId INT NULL;

UPDATE dbo.Vendor
SET MapVendorIntuitVendorId = map.VendorIntuitVendor.[Id]
FROM dbo.Vendor
JOIN map.VendorIntuitVendor ON dbo.Vendor.[Id] = map.VendorIntuitVendor.[VendorId];

DROP TABLE #TempVendor;

SELECT * FROM dbo.Vendor;
SELECT * FROM map.VendorIntuitVendor;
SELECT * FROM intuit.Vendor ORDER BY [DisplayName]

ALTER TABLE dbo.Vendor
DROP COLUMN IntuitVendorId;