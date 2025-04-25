CREATE TABLE intuit.NameValue
(
	NameValueGUID UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	[Name] VARCHAR(MAX) NOT NULL,
	[Value] VARCHAR(MAX) NOT NULL,
	CompanyInfoId VARCHAR(MAX) NOT NULL
);

SELECT * FROM intuit.NameValue;

SELECT *
FROM intuit.NameValue
WHERE [Name]='t' AND CompanyInfoId='1';

DELETE FROM intuit.NameValue;

DROP TABLE intuit.NameValue;

INSERT INTO intuit.NameValue ([Name], [Value], CompanyInfoId)
VALUES ('', '', '');

UPDATE intuit.NameValue
SET [Value]=''
WHERE [Name]='' AND CompanyInfoId='';