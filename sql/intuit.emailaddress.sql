CREATE TABLE intuit.EmailAddress
(
	EmailAddressGUID UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	[Address] VARCHAR(MAX) NOT NULL,
	CompanyInfoId VARCHAR(MAX) NOT NULL
);

ALTER TABLE intuit.EmailAddress
ALTER COLUMN [Address] VARCHAR(MAX) NULL;

ALTER TABLE intuit.EmailAddress
ALTER COLUMN CompanyInfoId VARCHAR(MAX) NULL;

ALTER TABLE intuit.EmailAddress
ADD CustomerId VARCHAR(MAX) NULL;

SELECT * FROM intuit.EmailAddress;

SELECT *
FROM intuit.EmailAddress
WHERE CompanyInfoId='1';

DELETE FROM intuit.EmailAddress;

DROP TABLE intuit.EmailAddress;

INSERT INTO intuit.EmailAddress ([Address], CompanyInfoId)
VALUES ('', '');

UPDATE intuit.EmailAddress
SET [Address]=''
WHERE CompanyInfoId='';
