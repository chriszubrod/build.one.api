CREATE TABLE intuit.WebSiteAddress
(
	WebSiteAddressGUID UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	URI VARCHAR(MAX) NOT NULL,
	CompanyInfoId VARCHAR(MAX) NOT NULL
);

SELECT * FROM intuit.WebSiteAddress;

SELECT *
FROM intuit.WebSiteAddress
WHERE CompanyInfoId='1';

DELETE FROM intuit.WebSiteAddress;

DROP TABLE intuit.WebSiteAddress;

INSERT INTO intuit.WebSiteAddress (URI, CompanyInfoId)
VALUES ('', '');

UPDATE intuit.WebSiteAddress
SET URI=''
WHERE CompanyInfoId='';
