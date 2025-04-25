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
    [TokenType] NVARCHAR(MAX) NULL
);

DROP TABLE ms.Auth;

