SELECT * FROM [intuit].[CompanyInfo];

DELETE FROM intuit.[CompanyInfo];

DELETE FROM intuit.[CompanyInfo]
WHERE Id='2';

DROP TABLE intuit.[CompanyInfo];

CREATE TABLE intuit.[CompanyInfo]
(
	CompanyInfoGUID UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	RealmId VARCHAR(MAX) NOT NULL,
	Id VARCHAR(MAX) NOT NULL,
	SyncToken VARCHAR(MAX) NOT NULL,
	CompanyName VARCHAR(MAX) NOT NULL,
	SupportedLanguages VARCHAR(MAX) NOT NULL,
	Country VARCHAR(MAX) NOT NULL,
	FiscalYearStartMonth VARCHAR(MAX) NOT NULL,
	LegalName VARCHAR(MAX) NOT NULL,
	CompanyStartDate DATE NOT NULL,
	EmployerId VARCHAR(MAX) NOT NULL,
	Domain VARCHAR(MAX) NOT NULL,
	Sparse VARCHAR(MAX) NOT NULL,
	CreatedTime DATETIMEOFFSET NOT NULL,
	LastUpdatedTime DATETIMEOFFSET NOT NULL
);

INSERT INTO intuit.CompanyInfo (RealmId, Id, SyncToken, CompanyName, SupportedLanguages, Country, FiscalYearStartMonth, LegalName, CompanyStartDate, EmployerId, Domain, Sparse, CreatedTime, LastUpdatedTime)
VALUES ('','','','','','','','','','','','','','');

SELECT RealmId, Id, SyncToken, LastUpdatedTime, CONVERT(datetime2, LastUpdatedTime, 1)
FROM intuit.CompanyInfo
WHERE [Id]='1';

UPDATE intuit.CompanyInfo
SET [SyncToken]='146'
WHERE [RealmId]='9130353016965726';
