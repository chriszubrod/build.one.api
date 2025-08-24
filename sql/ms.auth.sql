CREATE TABLE ms.Auth (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
	[CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
	[ClientId] NVARCHAR(MAX) NULL,
	[Tenant] NVARCHAR(MAX) NULL,
	[ClientSecret] NVARCHAR(255) NULL,
    [AccessToken] NVARCHAR(MAX) NULL,
	[ExpiresIn] INT NULL,
	[ExtExpiresIn] INT NULL,
	[RefreshToken] NVARCHAR(MAX) NULL,
	[Scope] NVARCHAR(MAX) NULL,
    [TokenType] NVARCHAR(MAX) NULL,
	[UserId] INT NULL
);

DROP TABLE ms.Auth;

SELECT * FROM ms.Auth;

ALTER TABLE ms.Auth
ADD [UserId] INT NULL;



DROP PROCEDURE IF EXISTS CreateMsAuth;

CREATE PROCEDURE CreateMsAuth
	@CreatedDatetime DATETIMEOFFSET,
	@ModifiedDatetime DATETIMEOFFSET,
	@ClientId NVARCHAR(MAX),
	@Tenant NVARCHAR(MAX),
	@ClientSecret NVARCHAR(255),
	@AccessToken NVARCHAR(MAX),
	@ExpiresIn INT,
	@ExtExpiresIn INT,
	@RefreshToken NVARCHAR(MAX),
	@Scope NVARCHAR(MAX),
	@TokenType NVARCHAR(MAX),
	@UserId INT
AS
BEGIN
	BEGIN TRANSACTION;

	-- Insert a new record into the Auth table
	INSERT INTO ms.Auth (CreatedDatetime, ModifiedDatetime, ClientId, Tenant, ClientSecret, AccessToken, ExpiresIn, ExtExpiresIn, RefreshToken, Scope, TokenType, UserId)
	VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @ClientId, @Tenant, @ClientSecret, @AccessToken, @ExpiresIn, @ExtExpiresIn, @RefreshToken, @Scope, @TokenType, @UserId);

	COMMIT TRANSACTION;
END;


DROP PROCEDURE IF EXISTS ReadMsAuthByUserId;

CREATE PROCEDURE ReadMsAuthByUserId
	@UserId INT
AS
BEGIN
	BEGIN TRANSACTION;

	-- Select records from the Auth table by UserId
	SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
		[ClientId],
		[Tenant],
		[ClientSecret],
		[AccessToken],
		[ExpiresIn],
		[ExtExpiresIn],
		[RefreshToken],
		[Scope],
		[TokenType],
		[UserId]
	FROM ms.Auth
	WHERE UserId = @UserId;

	COMMIT TRANSACTION;
END;

EXECUTE ReadMsAuthByUserId @UserId = 2;

DELETE FROM ms.Auth WHERE Id = 2;






DROP PROCEDURE IF EXISTS UpdateMsAuthById;

CREATE PROCEDURE UpdateMsAuthById
	@Id INT,
	@GUID UNIQUEIDENTIFIER,
	@CreatedDatetime DATETIMEOFFSET,
	@ModifiedDatetime DATETIMEOFFSET,
	@ClientId NVARCHAR(MAX),
	@Tenant NVARCHAR(MAX),
	@ClientSecret NVARCHAR(255),
	@AccessToken NVARCHAR(MAX),
	@ExpiresIn INT,
	@ExtExpiresIn INT,
	@RefreshToken NVARCHAR(MAX),
	@Scope NVARCHAR(MAX),
	@TokenType NVARCHAR(MAX),
	@UserId INT
AS
BEGIN
	BEGIN TRANSACTION;

	-- Update the record in the Auth table by Id
	UPDATE ms.Auth
	SET [GUID] = @GUID,
		[CreatedDatetime] = CONVERT(DATETIMEOFFSET, @CreatedDatetime),
		[ModifiedDatetime] = CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
		[ClientId] = @ClientId,
		[Tenant] = @Tenant,
		[ClientSecret] = @ClientSecret,
		[AccessToken] = @AccessToken,
		[ExpiresIn] = @ExpiresIn,
		[ExtExpiresIn] = @ExtExpiresIn,
		[RefreshToken] = @RefreshToken,
		[Scope] = @Scope,
		[TokenType] = @TokenType,
		[UserId] = @UserId
	WHERE [Id] = @Id;

	COMMIT TRANSACTION;
END;




UPDATE ms.Auth
SET [ClientSecret]=''
WHERE [Id] = 1;

SELECT * FROM ms.Auth;