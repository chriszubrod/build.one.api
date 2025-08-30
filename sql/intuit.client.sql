CREATE TABLE intuit.Client
(
	ClienGUID] UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	CreatedDatetime DATETIME NOT NULL,
	ModifiedDatetime DATETIME NOT NULL,
	ClientId VARCHAR(MAX) NOT NULL,
	ClientSecret VARCHAR(MAX) NOT NULL
);



DROP PROCEDURE IF EXISTS ReadIntuitClient;

CREATE PROCEDURE ReadIntuitClient
AS
BEGIN

	BEGIN TRANSACTION;

	SELECT
		ClienGUID
		CreatedDatetime,
		ModifiedDatetime,
		ClientId,
		ClientSecret
	FROM intuit.Client;

	COMMIT TRANSACTION;
END;

EXEC ReadIntuitClient;


SELECT * FROM intuit.Client;