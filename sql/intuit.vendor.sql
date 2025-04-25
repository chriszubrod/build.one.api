CREATE TABLE intuit.Vendor
(
	VendorGUID UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	RealmId VARCHAR(MAX) NULL,
	TaxId VARCHAR(MAX) NULL,
	Id VARCHAR(MAX) NULL,
	SyncToken VARCHAR(MAX) NULL,
	CreatedDatetime DATETIMEOFFSET NULL,
	LastUpdatedDatetime DATETIMEOFFSET NULL,
	CompanyName VARCHAR(MAX) NULL,
	DisplayName VARCHAR(MAX) NULL,
	PrintOnCheckName VARCHAR(MAX) NULL,
	IsActive INT NULL,
	V4IDPseudonym VARCHAR(MAX) NULL,
	PrimaryEmailAddress VARCHAR(MAX) NULL
);

SELECT * FROM intuit.Vendor ORDER BY DisplayName;





DROP PROCEDURE CreateIntuitVendor;

CREATE PROCEDURE CreateIntuitVendor
(
	@RealmId VARCHAR(MAX),
	@TaxId VARCHAR(MAX),
	@VendorId VARCHAR(MAX),
	@SyncToken VARCHAR(MAX),
	@CreatedDatetime DATETIMEOFFSET,
	@LastUpdatedDatetime DATETIMEOFFSET,
	@CompanyName VARCHAR(MAX),
	@DisplayName VARCHAR(MAX),
	@PrintOnCheckName VARCHAR(MAX),
	@IsActive INT,
	@V4IDPseudonym VARCHAR(MAX),
	@PrimaryEmailAddress VARCHAR(MAX)
)
AS
BEGIN
	INSERT INTO intuit.Vendor (RealmId, TaxId, Id, SyncToken, CreatedDatetime, LastUpdatedDatetime, CompanyName, DisplayName, PrintOnCheckName, IsActive, V4IDPseudonym, PrimaryEmailAddress)
	VALUES (@RealmId, @TaxId, @VendorId, @SyncToken, @CreatedDatetime, @LastUpdatedDatetime, @CompanyName, @DisplayName, @PrintOnCheckName, @IsActive, @V4IDPseudonym, @PrimaryEmailAddress);
END




DROP PROCEDURE ReadIntuitVendorById;

CREATE PROCEDURE ReadIntuitVendorById
(
	@VendorId VARCHAR(MAX)
)
AS
BEGIN
	SELECT
		VendorGUID,
		RealmId,
		TaxId,
		[Id],
		SyncToken,
		CAST(CreatedDatetime AS NVARCHAR(MAX)) AS CreatedDatetime,
		CAST(LastUpdatedDatetime AS NVARCHAR(MAX)) AS LastUpdatedDatetime,
		CompanyName,
		DisplayName,
		PrintOnCheckName,
		IsActive,
		V4IDPseudonym,
		PrimaryEmailAddress
	FROM intuit.Vendor 
	WHERE Id=@VendorId;
END




DROP PROCEDURE UpdateIntuitVendorByRealmIdAndVendorId;

CREATE PROCEDURE UpdateIntuitVendorByRealmIdAndVendorId
(
	@RealmId VARCHAR(MAX),
	@TaxId VARCHAR(MAX),
	@VendorId VARCHAR(MAX),
	@SyncToken VARCHAR(MAX),
	@CreatedDatetime DATETIMEOFFSET,
	@LastUpdatedDatetime DATETIMEOFFSET,
	@CompanyName VARCHAR(MAX),
	@DisplayName VARCHAR(MAX),
	@PrintOnCheckName VARCHAR(MAX),
	@IsActive INT,
	@V4IDPseudonym VARCHAR(MAX),
	@PrimaryEmailAddress VARCHAR(MAX)
)
AS
BEGIN
	UPDATE intuit.Vendor
	SET TaxId=@TaxId, SyncToken=@SyncToken, CreatedDatetime=@CreatedDatetime, LastUpdatedDatetime=@LastUpdatedDatetime, CompanyName=@CompanyName, DisplayName=@DisplayName, PrintOnCheckName=@PrintOnCheckName, IsActive=@IsActive, V4IDPseudonym=@V4IDPseudonym, PrimaryEmailAddress=@PrimaryEmailAddress
	WHERE RealmId=@RealmId AND Id=@VendorId;
END




























SELECT *
FROM [intuit].Vendor
ORDER BY CompanyName;

SELECT *
FROM [intuit].Vendor
WHERE [Id]=1224;


DELETE FROM intuit.Vendor;


DELETE FROM intuit.Vendor
WHERE Id='2';


DROP TABLE intuit.Vendor;


INSERT INTO intuit.Vendor (RealmId, TaxId, Id, SyncToken, CreatedDatetime, LastUpdtedDatetime, CompanyName, DisplayName, PrintOnCheckName, IsActive, V4IDPseudonym, PrimaryEmailAddress)
VALUES ('', '', '', '', CAST('2022-04-19T14:55:06-07:00' AS datetimeoffset), CAST('2022-08-02T15:02:24-07:00' AS datetimeoffset), '', '', '', '', '', '');

SELECT RealmId, [Id], SyncToken, DisplayName, LastUpdatedDatetime, CONVERT(datetime2, LastUpdatedDatetime)
FROM intuit.Vendor
WHERE [Id]='1021';

UPDATE intuit.Vendor
SET [SyncToken]='146'
WHERE [RealmId]='9130353016965726';

UPDATE intuit.Vendor
SET TaxId=?, SyncToken=?, CreatedDatetime=?, LastUpdatedDatetime=?, CompanyName=?, DisplayName=?, PrintOnCheckName=?, IsActive=?, V4IDPseudonym=?, PrimaryEmailAddress=?
WHERE RealmId=? AND Id=?;
