CREATE TABLE intuit.Item
(
	ItemGUID UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	RealmId VARCHAR(MAX) NULL,
	[Name] VARCHAR(MAX) NULL,
	IsActive INT NULL,
	IsSubItem INT NULL,
	ParentRefValue VARCHAR(MAX) NULL,
	[Level] INT NULL,
	FullyQualifiedName VARCHAR(MAX) NULL,
	Id VARCHAR(MAX) NULL,
	SyncToken VARCHAR(MAX) NULL,
	CreatedDatetime DATETIMEOFFSET NULL,
	LastUpdatedDatetime DATETIMEOFFSET NULL
);


SELECT *
FROM [intuit].Item
ORDER BY [Name];

SELECT *
FROM [intuit].Item
WHERE [Id]=1224;


DELETE FROM intuit.Item;


DELETE FROM intuit.Item
WHERE Id='2';


DROP TABLE intuit.Item;


INSERT INTO intuit.Item (RealmId, [Name], IsActive, IsSubItem, ParentRefValue, [Level], FullyQualifiedName, Id, SyncToken, CreatedDatetime, LastUpdtedDatetime)
VALUES ('', '', '', '', '', '', '', '', '', CAST('2022-04-19T14:55:06-07:00' AS datetimeoffset), CAST('2022-08-02T15:02:24-07:00' AS datetimeoffset));

SELECT RealmId, [Id], SyncToken, DisplayName, LastUpdatedDatetime, CONVERT(datetime2, LastUpdatedDatetime)
FROM intuit.Item
WHERE [Id]='1021';

UPDATE intuit.Item
SET [SyncToken]='146'
WHERE [RealmId]='9130353016965726';

UPDATE intuit.Item
SET [Name]=?, IsActive=?, IsSubItem=?, ParentRefValue=?, [Level]=?, FullyQualifiedName=?, SyncToken=?, CreatedDatetime=?, LastUpdtedDatetime=?
WHERE RealmId=? AND Id=?;


DROP PROCEDURE IF EXISTS ReadIntuitItemById;

CREATE PROCEDURE ReadIntuitItemById
	@Id VARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

	SELECT
		ItemGUID,
		RealmId,
		[Name],
		IsActive,
		IsSubItem,
		ParentRefValue,
		[Level],
		FullyQualifiedName,
		Id,
		SyncToken,
		CAST(CreatedDatetime AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST(LastUpdatedDatetime AS NVARCHAR(MAX)) AS LastUpdatedDatetime
	FROM intuit.Item
	WHERE [Id]=@Id;

	COMMIT;
END


DROP PROCEDURE IF EXISTS UpdateIntuitItemByRealmIdAndItemId;

CREATE PROCEDURE UpdateIntuitItemByRealmIdAndItemId
	@RealmId VARCHAR(MAX),
	@Name VARCHAR(MAX),
	@IsActive INT,
	@IsSubItem INT,
	@ParentRefValue VARCHAR(MAX),
	@Level INT,
	@FullyQualifiedName VARCHAR(MAX),
	@Id VARCHAR(MAX),
	@SyncToken VARCHAR(MAX),
	@CreatedDatetime DATETIMEOFFSET,
	@LastUpdatedDatetime DATETIMEOFFSET
AS
BEGIN
	BEGIN TRANSACTION;

	UPDATE intuit.Item
	SET [Name]=@Name,
		IsActive=@IsActive,
		IsSubItem=@IsSubItem,
		ParentRefValue=@ParentRefValue,
		[Level]=@Level,
		FullyQualifiedName=@FullyQualifiedName,
		SyncToken=@SyncToken,
		CreatedDatetime=CAST(@CreatedDatetime AS DATETIMEOFFSET),
		LastUpdatedDatetime=CAST(@LastUpdatedDatetime AS DATETIMEOFFSET)
	WHERE RealmId=@RealmId AND Id=@Id;

	COMMIT;
END
