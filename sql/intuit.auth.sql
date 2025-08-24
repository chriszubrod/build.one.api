CREATE TABLE intuit.Auth
(
	[AuthGUID] UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	CreatedDatetime DATETIME NOT NULL,
	ModifiedDatetime DATETIME NOT NULL,
	Code VARCHAR(MAX) NOT NULL,
	RealmId VARCHAR(MAX) NOT NULL,
	TokenType VARCHAR(MAX) NOT NULL,
	IdToken VARCHAR(MAX) NOT NULL,
	AccessToken VARCHAR(MAX) NOT NULL,
	ExpiresIn VARCHAR(MAX) NOT NULL,
	RefreshToken VARCHAR(MAX) NOT NULL,
	XRefreshTokenExpiresIn VARCHAR(MAX) NOT NULL
);



DROP PROCEDURE IF EXISTS ReadIntuitAuth;

CREATE PROCEDURE ReadIntuitAuth
AS
BEGIN

	BEGIN TRANSACTION;

	SELECT
		[AuthGUID],
		CreatedDatetime,
		ModifiedDatetime,
		Code,
		RealmId,
		TokenType,
		IdToken,
		AccessToken,
		ExpiresIn,
		RefreshToken,
		XRefreshTokenExpiresIn
	FROM intuit.Auth;

	COMMIT TRANSACTION;
END;

EXEC ReadIntuitAuth;