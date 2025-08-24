CREATE TABLE map.ProjectIntuitCustomer (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [ProjectId] INT NOT NULL,
    [IntuitCustomerId] INT NOT NULL
);


DROP TABLE map.ProjectIntuitCustomer;



DROP PROCEDURE IF EXISTS CreateMapProjectIntuitCustomer;

CREATE PROCEDURE CreateMapProjectIntuitCustomer
    @ProjectId INT,
    @IntuitCustomerId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Insert a new record into the ProjectIntuitCustomer table
    INSERT INTO map.ProjectIntuitCustomer (CreatedDatetime, ModifiedDatetime, ProjectId, IntuitCustomerId)
    VALUES (@Now, @Now, @ProjectId, @IntuitCustomerId);

    COMMIT TRANSACTION;
END;

EXEC CreateMapProjectIntuitCustomer
    3,
    602;



DROP PROCEDURE IF EXISTS ReadMapProjectIntuitCustomers;

CREATE PROCEDURE ReadMapProjectIntuitCustomers
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [IntuitCustomerId]
    FROM map.ProjectIntuitCustomer;

    COMMIT TRANSACTION;
END;

EXEC ReadMapProjectIntuitCustomers;



DROP PROCEDURE IF EXISTS ReadMapProjectIntuitCustomerByGUID;

CREATE PROCEDURE ReadMapProjectIntuitCustomerByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [IntuitCustomerId]
    FROM map.ProjectIntuitCustomer
    WHERE [GUID] = @GUID;

    COMMIT TRANSACTION;
END;

EXEC ReadMapProjectIntuitCustomerByGUID
    @GUID = 'b92fd87e-9167-4ebc-80c0-5e737e765e4e';



DROP PROCEDURE IF EXISTS ReadMapProjectIntuitCustomerByProjectId;

CREATE PROCEDURE ReadMapProjectIntuitCustomerByProjectId
    @ProjectId INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [ProjectId],
        [IntuitCustomerId]
    FROM map.ProjectIntuitCustomer
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;

EXEC ReadMapProjectIntuitCustomerByProjectId
    @ProjectId = 3;



DROP PROCEDURE IF EXISTS UpdateMapProjectIntuitCustomerById;

CREATE PROCEDURE UpdateMapProjectIntuitCustomerById
    @Id INT,
    @ProjectId INT,
    @IntuitCustomerId INT
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

    -- Update the record in the ProjectIntuitCustomer table
    UPDATE map.ProjectIntuitCustomer
    SET
        ModifiedDatetime = @Now,
        ProjectId = @ProjectId,
        IntuitCustomerId = @IntuitCustomerId
    WHERE Id = @Id;

    COMMIT TRANSACTION;
END;

EXEC UpdateMapProjectIntuitCustomerById
    @Id = 1,
    @ProjectId = 3,
    @IntuitCustomerId = 602;



DROP PROCEDURE IF EXISTS DeleteMapProjectIntuitCustomerById;

CREATE PROCEDURE DeleteMapProjectIntuitCustomerById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Delete the record from the ProjectIntuitCustomer table by Id
    DELETE FROM map.ProjectIntuitCustomer
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
