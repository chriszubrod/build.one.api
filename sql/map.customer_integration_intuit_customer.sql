CREATE TABLE map.CustomerIntuitCustomer (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [CustomerId] INT NOT NULL,
    [IntuitCustomerId] INT NOT NULL
);


-- Drops for local iteration/testing
-- DROP TABLE map.CustomerIntuitCustomer;


DROP PROCEDURE IF EXISTS CreateMapCustomerIntuitCustomer;

CREATE PROCEDURE CreateMapCustomerIntuitCustomer
    @CustomerId INT,
    @IntuitCustomerId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    INSERT INTO map.CustomerIntuitCustomer (CreatedDatetime, ModifiedDatetime, CustomerId, IntuitCustomerId)
    VALUES (@Now, @Now, @CustomerId, @IntuitCustomerId);

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS ReadMapCustomerIntuitCustomers;

CREATE PROCEDURE ReadMapCustomerIntuitCustomers
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [CustomerId],
        [IntuitCustomerId]
    FROM map.CustomerIntuitCustomer;

    COMMIT TRANSACTION;
END;

EXEC ReadMapCustomerIntuitCustomers;


DROP PROCEDURE IF EXISTS ReadMapCustomerIntuitCustomerByGUID;

CREATE PROCEDURE ReadMapCustomerIntuitCustomerByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [CustomerId],
        [IntuitCustomerId]
    FROM map.CustomerIntuitCustomer
    WHERE [GUID] = @GUID;

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS ReadMapCustomerIntuitCustomerByCustomerId;

CREATE PROCEDURE ReadMapCustomerIntuitCustomerByCustomerId
    @CustomerId INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [CustomerId],
        [IntuitCustomerId]
    FROM map.CustomerIntuitCustomer
    WHERE [CustomerId] = @CustomerId;

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS ReadMapCustomerIntuitCustomerByIntuitCustomerId;

CREATE PROCEDURE ReadMapCustomerIntuitCustomerByIntuitCustomerId
    @IntuitCustomerId INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [CustomerId],
        [IntuitCustomerId]
    FROM map.CustomerIntuitCustomer
    WHERE [IntuitCustomerId] = @IntuitCustomerId;

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS UpdateMapCustomerIntuitCustomerById;

CREATE PROCEDURE UpdateMapCustomerIntuitCustomerById
    @Id INT,
    @CustomerId INT,
    @IntuitCustomerId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    UPDATE map.CustomerIntuitCustomer
    SET
        ModifiedDatetime = @Now,
        CustomerId = @CustomerId,
        IntuitCustomerId = @IntuitCustomerId
    WHERE Id = @Id;

    COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS DeleteMapCustomerIntuitCustomerById;

CREATE PROCEDURE DeleteMapCustomerIntuitCustomerById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM map.CustomerIntuitCustomer
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


-- Returns Intuit customers that are not mapped to any Build One Customer
-- and are not Jobs or Projects (IsJob/IsProject is NULL or 0)
DROP PROCEDURE IF EXISTS ReadAvailableIntuitCustomersForCustomerMap;

CREATE PROCEDURE ReadAvailableIntuitCustomersForCustomerMap
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT 
        c.[GUID],
        c.[RealmId],
        c.[Id],
        c.[DisplayName],
        c.[FullyQualifiedName],
        c.[IsJob],
        c.[ParentRefValue],
        c.[Level],
        c.[IsProject],
        c.[ClientEntityId],
        c.[IsActive],
        c.[SyncToken],
        c.[V4IDPseudonym],
        CAST(c.[CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST(c.[LastUpdatedDatetime] AS NVARCHAR(MAX)) AS [LastUpdatedDatetime]
    FROM intuit.Customer c
    LEFT JOIN map.CustomerIntuitCustomer m ON m.IntuitCustomerId = c.Id
    WHERE m.Id IS NULL
      AND (c.IsJob IS NULL OR c.IsJob = 0)
      AND (c.IsProject IS NULL OR c.IsProject = 0)
    ORDER BY c.DisplayName;

    COMMIT TRANSACTION;
END;
