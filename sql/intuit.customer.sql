CREATE TABLE intuit.Customer
(
	[GUID] UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	RealmId VARCHAR(MAX) NOT NULL,
	Id VARCHAR(MAX) NOT NULL,
	DisplayName VARCHAR(MAX) NOT NULL,
	FullyQualifiedName VARCHAR(MAX) NULL,
	IsJob INT NULL,
	ParentRefValue VARCHAR(MAX) NULL,
	[Level] INT NULL,
	IsProject INT NULL,
	ClientEntityId VARCHAR(MAX) NULL,
	IsActive INT NULL,
	SyncToken VARCHAR(MAX) NOT NULL,
	V4IDPseudonym VARCHAR(MAX) NULL,
	CreatedDatetime DATETIMEOFFSET NOT NULL,
	LastUpdatedDatetime DATETIMEOFFSET NOT NULL
);

SELECT *
FROM intuit.Customer
WHERE IsProject=0 OR IsProject IS NULL
ORDER BY DisplayName;


DROP PROCEDURE CreateIntuitCustomer;

CREATE PROCEDURE CreateIntuitCustomer
    @RealmId VARCHAR(MAX),
	@Id VARCHAR(MAX),
	@DisplayName VARCHAR(MAX),
	@FullyQualifiedName VARCHAR(MAX),
	@IsJob INT,
	@ParentRefValue VARCHAR(MAX),
	@Level INT,
	@IsProject INT,
	@ClientEntityId VARCHAR(MAX),
	@IsActive INT,
	@SyncToken VARCHAR(MAX),
	@V4IDPseudonym VARCHAR(MAX),
	@CreatedDatetime DATETIMEOFFSET,
	@LastUpdatedDatetime DATETIMEOFFSET
AS
BEGIN
	BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO intuit.Customer (RealmId, Id, DisplayName, FullyQualifiedName, IsJob, ParentRefValue, [Level], IsProject, ClientEntityId, IsActive, SyncToken, V4IDPseudonym, CreatedDatetime, LastUpdatedDatetime)
    VALUES (@RealmId, @Id, @DisplayName, @FullyQualifiedName, @IsJob, @ParentRefValue, @Level, @IsProject, @ClientEntityId, @IsActive, @SyncToken, @V4IDPseudonym, @CreatedDatetime, @LastUpdatedDatetime);

    COMMIT;
END





DROP PROCEDURE ReadIntuitCustomerById;


CREATE PROCEDURE ReadIntuitCustomerById
    @Id VARCHAR(MAX)
AS
BEGIN
    SELECT RealmId, Id, SyncToken, DisplayName, CONVERT(datetime2, LastUpdatedDatetime) AS LastUpdatedDatetime
    FROM intuit.Customer
    WHERE [Id] = @Id;
END





DROP PROCEDURE IF EXISTS ReadIntuitCustomerByIdGUID;

CREATE PROCEDURE ReadIntuitCustomerByIdGUID
	@GUID UNIQUEIDENTIFIER
AS
BEGIN
	BEGIN TRANSACTION;

	SELECT 
		[GUID],
		[RealmId],
		[Id],
		[DisplayName],
		[FullyQualifiedName],
		[IsJob],
		[ParentRefValue],
		[Level],
		[IsProject],
		[ClientEntityId],
		[IsActive],
		[SyncToken],
		[V4IDPseudonym],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([LastUpdatedDatetime] AS NVARCHAR(MAX)) AS [LastUpdatedDatetime]
	FROM intuit.Customer
	WHERE [GUID] = @GUID;

	COMMIT;
END

EXEC ReadIntuitCustomerByIdGUID '3D0FDD7C-F5ED-4687-931A-6E15122B124C';





DROP PROCEDURE UpdateIntuitCustomerByRealmIdAndCustomerId;


CREATE PROCEDURE UpdateIntuitCustomerByRealmIdAndCustomerId
    @RealmId VARCHAR(MAX),
    @Id VARCHAR(MAX),
    @DisplayName VARCHAR(MAX),
    @FullyQualifiedName VARCHAR(MAX),
    @IsJob INT,
    @ParentRefValue VARCHAR(MAX),
    @Level INT,
    @IsProject INT,
    @ClientEntityId VARCHAR(MAX),
    @IsActive INT,
    @SyncToken VARCHAR(MAX),
    @V4IDPseudonym VARCHAR(MAX),
    @CreatedDatetime DATETIMEOFFSET,
    @LastUpdatedDatetime DATETIMEOFFSET
AS
BEGIN	
    UPDATE intuit.Customer
    SET DisplayName=@DisplayName, FullyQualifiedName=@FullyQualifiedName, IsJob=@IsJob, ParentRefValue=@ParentRefValue, [Level]=@Level, IsProject=@IsProject, ClientEntityId=@ClientEntityId, IsActive=@IsActive, SyncToken=@SyncToken, V4IDPseudonym=@V4IDPseudonym, CreatedDatetime=@CreatedDatetime, LastUpdatedDatetime=@LastUpdatedDatetime
    WHERE RealmId=@RealmId AND Id=@Id;
END





DROP PROCEDURE IF EXISTS ReadIntuitProjects;

CREATE PROCEDURE ReadIntuitProjects
AS
BEGIN
	BEGIN TRANSACTION;

    SELECT 
		[GUID],
        [RealmId],
		[Id],
		[DisplayName],
		[FullyQualifiedName],
		[IsJob],
		[ParentRefValue],
		[Level],
		[IsProject],
		[ClientEntityId],
		[IsActive],
		[SyncToken],
		[V4IDPseudonym],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([LastUpdatedDatetime] AS NVARCHAR(MAX)) AS [LastUpdatedDatetime]
	FROM intuit.Customer
	WHERE IsProject=1
	ORDER BY DisplayName;

	COMMIT;
END










EXEC sp_rename 'intuit.Customer.CustomerGUID', [GUID], 'COLUMN';

SELECT * FROM [intuit].Customer ORDER BY DisplayName;

SELECT *
FROM [intuit].Customer
WHERE IsProject=1
ORDER BY DisplayName;


DELETE FROM intuit.Customer;


DELETE FROM intuit.Customer
WHERE Id='2';


DROP TABLE intuit.Customer;


INSERT INTO intuit.Customer (RealmId, Id, DisplayName, FullyQualifiedName, IsJob, ParentRefValue, [Level], IsProject, ClientEntityId, IsActive, SyncToken, V4IDPseudonym, CreatedDatetime, LastUpdatedDatetime)
VALUES ('', '', '', '', '', '', '', '', '', '', '', '', CAST('2022-04-19T14:55:06-07:00' AS datetimeoffset), CAST('2022-08-02T15:02:24-07:00' AS datetimeoffset));

SELECT RealmId, [Id], SyncToken, DisplayName, LastUpdatedDatetime, CONVERT(datetime2, LastUpdatedDatetime, 1)
FROM intuit.Customer
WHERE [Id]='1021';

UPDATE intuit.Customer
SET [SyncToken]='146'
WHERE [RealmId]='9130353016965726';

UPDATE intuit.Customer
SET DisplayName=?, FullyQualifiedName=?, IsJob=?, ParentRefValue=?, [Level]=?, IsProject=?, ClientEntityId=?, IsActive=?, SyncToken=?, V4IDPseudonym=?, CreatedDatetime=?, LastUpdatedDatetime=?
WHERE RealmId=? AND Id=?;



