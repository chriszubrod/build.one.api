-- Create table for mapping Projects to Intuit Customers.
CREATE TABLE map.Project_IntuitCustomer (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [ProjectId] INT NOT NULL,
    [IntuitCustomerId] INT NOT NULL
);

SELECT * FROM map.Project_IntuitCustomer;


DROP PROCEDURE IF EXISTS CreateMapProjectIntuitCustomer;

CREATE PROCEDURE CreateMapProjectIntuitCustomer
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @ProjectId INT,
    @IntuitCustomerId INT

AS
BEGIN
    BEGIN TRANSACTION;

    INSERT INTO map.Project_IntuitCustomer (
        [CreatedDatetime],
        [ModifiedDatetime],
        [ProjectId],
        [IntuitCustomerId]
    )

    VALUES (
        CONVERT(DATETIMEOFFSET, @CreatedDatetime),
        CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        @ProjectId,
        @IntuitCustomerId
    );

    COMMIT;
END

EXEC CreateMapProjectIntuitCustomer
	@CreatedDatetime='2025-03-15 00:00:00.000',
	@ModifiedDatetime='2025-03-15 00:00:00.000',
	@ProjectId=3,
	@IntuitCustomerId=602;








DROP PROCEDURE IF EXISTS ReadMapProjectIntuitCustomer;

CREATE PROCEDURE ReadMapProjectIntuitCustomer
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [ProjectId],
        [IntuitCustomerId]
    FROM map.Project_IntuitCustomer;

    COMMIT;
END








DROP PROCEDURE IF EXISTS ReadMapProjectIntuitCustomerById;

CREATE PROCEDURE ReadMapProjectIntuitCustomerById
    @Id INT
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [ProjectId],
        [IntuitCustomerId]
    FROM map.Project_IntuitCustomer
    WHERE [Id] = @Id;

    COMMIT;
END









DROP PROCEDURE IF EXISTS ReadMapProjectIntuitCustomerByProjectId;

CREATE PROCEDURE ReadMapProjectIntuitCustomerByProjectId
    @ProjectId INT
AS
BEGIN

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS [ModifiedDatetime],
        [ProjectId],
        [IntuitCustomerId]
    FROM map.Project_IntuitCustomer
    WHERE [ProjectId] = @ProjectId;

    COMMIT;
END
