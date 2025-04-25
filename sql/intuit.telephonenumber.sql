CREATE TABLE intuit.TelephoneNumber
(
	TelephoneNumberGUID UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	FreeFormNumber VARCHAR(MAX) NOT NULL,
	CompanyInfoId VARCHAR(MAX) NOT NULL
);

SELECT * FROM intuit.TelephoneNumber;

SELECT *
FROM intuit.TelephoneNumber
WHERE CompanyInfoId='1';

DELETE FROM intuit.TelephoneNumber;

DROP TABLE intuit.TelephoneNumber;

INSERT INTO intuit.TelephoneNumber (FreeFormNumber, CompanyInfoId)
VALUES ('', '');

UPDATE intuit.TelephoneNumber
SET FreeFormNumber=''
WHERE CompanyInfoId='';
