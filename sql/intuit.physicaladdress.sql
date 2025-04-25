CREATE TABLE intuit.PhysicalAddress
(
	PhysicalAddressGUID UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	Id VARCHAR(MAX) NOT NULL,
	PostalCode VARCHAR(MAX) NOT NULL,
	City VARCHAR(MAX) NOT NULL,
	Country VARCHAR(MAX) NOT NULL,
	Line1 VARCHAR(MAX) NOT NULL,
	CountrySubDivisionCode VARCHAR(MAX) NOT NULL,
	CompanyInfoId VARCHAR(MAX) NOT NULL
);


SELECT * FROM intuit.PhysicalAddress;

SELECT *
FROM intuit.PhysicalAddress
WHERE Id='1' AND CompanyInfoId='1';

DELETE FROM intuit.PhysicalAddress;

DROP TABLE intuit.PhysicalAddress;

ALTER TABLE intuit.PhysicalAddress
DROP COLUMN Line5, Line4, Line3, Line2, Lat, Long;

INSERT INTO intuit.PhysicalAddress (Id, PostalCode, City, Country, Line1, CountrySubDivisionCode, CompanyInfoId)
VALUES ('', '', '', '', '', '', '');

UPDATE intuit.PhysicalAddress
SET PostalCode='', City='', Country='', Line1='', CountrySubDivisionCode=''
WHERE Id='' AND CompanyInfoId='';
